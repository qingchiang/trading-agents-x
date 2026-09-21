"""One-time, read-only predecessor conversion; never used by normal run reads."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from tradingagents.configuration.settings import AppSettings
from tradingagents.persistence.migrations import upgrade_database

SOURCE_REVISION = "0013_submission_identity"


class MigrationError(ValueError):
    """A safe, credential-free offline conversion failure."""


@dataclass(frozen=True)
class MigrationReport:
    retained_runs: int
    removed_runs: int
    retained_nodes: int
    verified_tables: int
    missing_fields: dict[str, int] = field(default_factory=dict)
    integrity: str = "ok"

    def to_dict(self):
        return asdict(self)


def _tables(connection):
    return {
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _convert(source, target):
    versions = source.execute("SELECT version_num FROM alembic_version").fetchall()
    if versions != [(SOURCE_REVISION,)]:
        raise MigrationError("Only 0013_submission_identity can be converted")
    if source.execute("SELECT 1 FROM runs WHERE status IN ('queued','running') LIMIT 1").fetchone():
        raise MigrationError("Stop Web and worker and finish or cancel all queued or running Runs")
    if source.execute("PRAGMA quick_check").fetchall() != [("ok",)] or source.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("Source database integrity validation failed")
    removed = {
        row[0] for row in source.execute(
            "SELECT id FROM runs WHERE research_schema_version IS NULL AND research_kind IS NULL"
        )
    }
    retained = {
        row[0] for row in source.execute("SELECT id FROM runs")
    } - removed
    for row in source.execute("SELECT id, source_run_id, full_baseline_run_id FROM runs"):
        if row[0] in retained and any(value in removed for value in row[1:]):
            raise MigrationError("A retained Run references pre-Timeline history; resolve it before conversion")
    legacy_identities = {provider: identity for identity, provider in source.execute(
        "SELECT id, legacy_provider FROM model_connections WHERE legacy_provider IS NOT NULL"
    )}
    copied = {}
    missing = Counter()
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("BEGIN")
    target.execute("PRAGMA defer_foreign_keys=ON")
    for table in sorted(_tables(target) - {"alembic_version"}):
        # Table/column identifiers come exclusively from the new bundled schema.
        columns = [row[1] for row in target.execute(f'PRAGMA table_info("{table}")')]
        source_columns = {row[1] for row in source.execute(f'PRAGMA table_info("{table}")')}
        common = [name for name in columns if name in source_columns]
        names = ', '.join(f'"{name}"' for name in common)
        rows = source.execute(f'SELECT {names} FROM "{table}"').fetchall()
        owner = 'id' if table == 'runs' else 'run_id' if 'run_id' in common else None
        if owner:
            index = common.index(owner)
            rows = [row for row in rows if row[index] in retained]
        if table == 'primary_research_cycles':
            index = common.index('full_run_id')
            rows = [row for row in rows if row[index] in retained]
        from tradingagents.persistence.cutover_projection import (
            config_projection,
            configuration_projection,
            request_projection,
            submission_projection,
        )
        if table == 'runs':
            projected = []
            for row in rows:
                values = dict(zip(common, row, strict=True))
                request = json.loads(values['request_json'])
                config = json.loads(values['config_json'])
                method = json.loads(values['method_snapshot_json']) if values['method_snapshot_json'] else None
                submission = json.loads(values['submission_json']) if values['submission_json'] else None
                values['audit_snapshot_json'] = json.dumps({'request': request, 'config': config, 'method': method, 'submission': submission})
                projected_request = request_projection(request, config)
                if any(not role.get('connection_id') for role in projected_request['models'].values()):
                    missing['connection_identity'] += 1
                values['request_json'] = json.dumps(projected_request)
                values['config_json'] = json.dumps(config_projection(config, values['research_kind']))
                canonical_submission = submission_projection(submission, legacy_identities)
                values['submission_json'] = json.dumps(canonical_submission) if canonical_submission is not None else None
                projected.append(values)
            common = [*common, 'audit_snapshot_json']
            names = ', '.join(f'"{name}"' for name in common)
            rows = [tuple(values[name] for name in common) for values in projected]
        elif table == 'application_configuration':
            index = common.index('values_json')
            rows = [tuple(json.dumps(configuration_projection(json.loads(value), legacy_identities)) if i == index else value for i, value in enumerate(row)) for row in rows]
        elif table == 'configuration_credentials':
            from tradingagents.configuration.importing import legacy_credential_fields
            from tradingagents.llm.models import credential_name
            aliases = legacy_credential_fields()
            name_index, value_index = common.index('name'), common.index('value')
            credentials = {row[name_index]: row[value_index] for row in rows}
            for alias, (provider, field) in aliases.items():
                if alias not in credentials:
                    continue
                identity = legacy_identities.get(provider)
                if not identity:
                    raise MigrationError("A credential alias has no recorded connection identity")
                name = credential_name(identity, field)
                if name in credentials and credentials[name] != credentials[alias]:
                    raise MigrationError("Conflicting credential copies require resolution before conversion")
                credentials[name] = credentials.pop(alias)
            rows = [tuple(name if field == 'name' else value for field in common) for name, value in credentials.items()]
        if rows:
            target.executemany(
                f'INSERT INTO "{table}" ({names}) VALUES ({", ".join("?" for _ in common)})', rows
            )
        copied[table] = (names, rows)
    for (raw,) in target.execute("SELECT incremental_products_json FROM research_nodes WHERE research_kind='incremental'"):
        products = json.loads(raw) if raw else {}
        for name in ('analysis_brief', 'decision_outcome'):
            if not products or products.get(name) is None:
                missing[name] += 1
    if target.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("Destination relationship integrity validation failed")
    for table, (names, expected) in copied.items():
        actual = target.execute(f'SELECT {names} FROM "{table}"').fetchall()
        if sorted(actual, key=repr) != sorted(expected, key=repr):
            raise MigrationError("Destination content integrity validation failed")
    target.commit()
    if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise MigrationError("Destination database integrity validation failed")
    nodes = target.execute("SELECT count(*) FROM research_nodes").fetchone()[0]
    return MigrationReport(len(retained), len(removed), nodes, len(copied), dict(missing))


def migrate_current(source: Path, destination: Path) -> MigrationReport:
    """Publish a validated database atomically without overwriting either input.

    Stop the old Web and worker before invoking this command. Checkpoints are
    deliberately excluded; completed execution attempts and events are retained.
    """
    source, destination = Path(source).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise MigrationError("Destination already exists")
    if not source.is_file():
        raise MigrationError("Source database does not exist")
    try:
        with TemporaryDirectory(prefix=".cutover-", dir=destination.parent) as temporary:
            working = Path(temporary) / "current.db"
            settings = AppSettings.from_env(environ={
                "TRADINGAGENTS_HOME": temporary,
                "TRADINGAGENTS_DATABASE_PATH": str(working),
            })
            upgrade_database(settings)
            os.chmod(working, 0o600)
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old:
                old.execute("PRAGMA query_only=ON")
                source_version = old.execute("PRAGMA data_version").fetchone()
                old.execute("BEGIN")
                with closing(sqlite3.connect(working)) as new:
                    report = _convert(old, new)
                    new.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    new.execute("PRAGMA journal_mode=DELETE")
                old.rollback()
                if old.execute("PRAGMA data_version").fetchone() != source_version:
                    raise MigrationError("Source changed during conversion; stop Web and worker and retry")
            with working.open('rb') as handle:
                os.fsync(handle.fileno())
            # link() fails if another process created the destination meanwhile.
            os.link(working, destination)
            return report
    except MigrationError:
        raise
    except (sqlite3.Error, OSError):
        # Never include SQL parameters or connection/configuration contents.
        raise MigrationError("Offline database integrity or filesystem validation failed") from None
