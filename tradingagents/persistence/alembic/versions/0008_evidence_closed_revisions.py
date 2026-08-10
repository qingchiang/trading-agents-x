"""Separate Revision role, strategy, and Change Conclusion.

Revision ID: 0008_evidence_closed_revisions
Revises: 0007_shadow_research_updates
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_evidence_closed_revisions"
down_revision: str | Sequence[str] | None = "0007_shadow_research_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _create_immutability_trigger(*, legacy: bool) -> None:
    semantic_columns = (
        "execution_strategy, outcome,"
        if legacy
        else "role, execution_strategy, outcome, change_conclusion, indeterminate_reason,"
    )
    op.execute(
        f"""
        CREATE TRIGGER research_revisions_immutable_content
        BEFORE UPDATE OF chain_id, sequence, predecessor_revision_id, cutoff,
          {semantic_columns} language, current_state_json, delta_json,
          coverage_json, update_summary_json, evidence_snapshot_json,
          research_update_audit_json, metrics_json, created_at
        ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revision content is immutable');
        END
        """
    )


def _create_no_delete_trigger() -> None:
    op.execute(
        """
        CREATE TRIGGER research_revisions_no_delete
        BEFORE DELETE ON research_revisions
        BEGIN
          SELECT RAISE(ABORT, 'Research Revisions cannot be deleted');
        END
        """
    )


def _create_run_reference_triggers() -> None:
    for operation, suffix in (
        ("INSERT", "insert"),
        (
            "UPDATE OF update_intent_id, research_chain_id, "
            "baseline_revision_id, research_execution_strategy",
            "update",
        ),
    ):
        op.execute(
            f"""
            CREATE TRIGGER runs_research_update_refs_{suffix}
            BEFORE {operation} ON runs
            WHEN (NEW.update_intent_id IS NOT NULL
              OR NEW.research_chain_id IS NOT NULL
              OR NEW.baseline_revision_id IS NOT NULL
              OR NEW.research_execution_strategy IS NOT NULL) AND (
              NEW.update_intent_id IS NULL
              OR NEW.research_chain_id IS NULL
              OR NEW.baseline_revision_id IS NULL
              OR NEW.research_execution_strategy NOT IN ('full', 'incremental')
              OR NOT EXISTS (SELECT 1 FROM research_chains WHERE id = NEW.research_chain_id)
              OR NOT EXISTS (
                SELECT 1 FROM research_revisions
                WHERE id = NEW.baseline_revision_id
                  AND chain_id = NEW.research_chain_id
              )
            )
            BEGIN
              SELECT RAISE(ABORT, 'invalid Research Chain update references');
            END
            """
        )


def _upgrade_audit(value: dict[str, object]) -> dict[str, object]:
    audit = dict(value)
    if audit.get("schema_version") == "2":
        return audit
    audit["schema_version"] = "2"
    coverage = audit.get("coverage")
    if isinstance(coverage, dict):
        coverage = dict(coverage)
        coverage.setdefault("schema_version", "1")
        audit["coverage"] = coverage
    candidate_value = audit.get("candidate")
    if isinstance(candidate_value, dict):
        candidate = dict(candidate_value)
        candidate["schema_version"] = "2"
        candidate["change_conclusion"] = candidate.pop("outcome")
        candidate_coverage = candidate.get("coverage")
        if isinstance(candidate_coverage, dict):
            candidate_coverage = dict(candidate_coverage)
            candidate_coverage.setdefault("schema_version", "1")
            candidate["coverage"] = candidate_coverage
        summary_value = candidate.get("update_summary")
        if isinstance(summary_value, dict):
            summary = dict(summary_value)
            summary.setdefault("schema_version", "1")
            if "outcome" in summary:
                summary["change_conclusion"] = summary.pop("outcome")
            candidate["update_summary"] = summary
        audit["candidate"] = candidate
    return audit


def _downgrade_audit(value: dict[str, object]) -> dict[str, object]:
    audit = dict(value)
    audit.pop("schema_version", None)
    if audit.get("comparison") == "inconclusive":
        audit["comparison"] = "not_applicable"
    candidate_value = audit.get("candidate")
    if isinstance(candidate_value, dict):
        candidate = dict(candidate_value)
        candidate.pop("schema_version", None)
        candidate["outcome"] = candidate.pop("change_conclusion")
        summary_value = candidate.get("update_summary")
        if isinstance(summary_value, dict):
            summary = dict(summary_value)
            if "change_conclusion" in summary:
                summary["outcome"] = summary.pop("change_conclusion")
            candidate["update_summary"] = summary
        audit["candidate"] = candidate
    return audit


def _merge_snapshot_values(
    authoritative: dict[str, object], bounded: dict[str, object]
) -> dict[str, object]:
    merged = dict(authoritative)
    authoritative_bundle = authoritative.get("bundle")
    bounded_bundle = bounded.get("bundle")
    if not isinstance(authoritative_bundle, dict) or not isinstance(bounded_bundle, dict):
        return merged
    bundle = dict(authoritative_bundle)
    for field, identity in (("items", "ref"), ("tables", "id")):
        values = {
            item[identity]: item
            for item in (*bundle.get(field, ()), *bounded_bundle.get(field, ()))
            if isinstance(item, dict) and identity in item
        }
        bundle[field] = list(values.values())
    bundle["digest"] = None
    merged["bundle"] = bundle
    for field, identity in (
        ("lineage", "evidence_ref"),
        ("source_records", "version_id"),
        ("source_record_lineage", "version_id"),
    ):
        values = {
            item[identity]: item
            for item in (*authoritative.get(field, ()), *bounded.get(field, ()))
            if isinstance(item, dict) and identity in item
        }
        merged[field] = list(values.values())
    watermarks = {
        (item.get("source"), item.get("scanned_start"), item.get("scanned_end")): item
        for item in (
            *authoritative.get("source_watermarks", ()),
            *bounded.get("source_watermarks", ()),
        )
        if isinstance(item, dict)
    }
    merged["source_watermarks"] = list(watermarks.values())
    return merged


def _merge_revision_candidate_snapshots() -> None:
    connection = op.get_bind()
    revisions = sa.table(
        "research_revisions",
        sa.column("id", sa.String()),
        sa.column("research_update_audit_json", sa.JSON()),
        sa.column("evidence_snapshot_json", sa.JSON()),
    )
    rows = connection.execute(
        sa.select(
            revisions.c.id,
            revisions.c.research_update_audit_json,
            revisions.c.evidence_snapshot_json,
        ).where(revisions.c.research_update_audit_json.is_not(None))
    ).all()
    for row_id, audit, snapshot in rows:
        candidate = audit.get("candidate") if isinstance(audit, dict) else None
        bounded = candidate.get("evidence_snapshot") if isinstance(candidate, dict) else None
        if not isinstance(snapshot, dict) or not isinstance(bounded, dict):
            continue
        connection.execute(
            revisions.update()
            .where(revisions.c.id == row_id)
            .values(evidence_snapshot_json=_merge_snapshot_values(snapshot, bounded))
        )


def _rewrite_audits(
    transform: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    connection = op.get_bind()
    for table_name in ("runs", "research_revisions"):
        table = sa.table(
            table_name,
            sa.column("id", sa.String()),
            sa.column("research_update_audit_json", sa.JSON()),
        )
        rows = connection.execute(
            sa.select(table.c.id, table.c.research_update_audit_json).where(
                table.c.research_update_audit_json.is_not(None)
            )
        ).all()
        for row_id, value in rows:
            connection.execute(
                table.update()
                .where(table.c.id == row_id)
                .values(research_update_audit_json=transform(value))
            )


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute("DROP TRIGGER IF EXISTS research_revisions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_insert")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_update")
    op.add_column(
        "research_revisions",
        sa.Column(
            "role",
            sa.String(length=20),
            nullable=False,
            server_default="initial",
        ),
    )
    op.add_column(
        "research_revisions",
        sa.Column("change_conclusion", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "research_revisions",
        sa.Column("indeterminate_reason", sa.String(length=50), nullable=True),
    )
    op.execute(
        "UPDATE research_revisions SET role = 'update' WHERE predecessor_revision_id IS NOT NULL"
    )
    op.execute(
        "UPDATE research_revisions SET change_conclusion = outcome "
        "WHERE role = 'update' AND outcome IN ('material_change', 'no_material_change')"
    )
    op.execute(
        "UPDATE research_revisions SET change_conclusion = 'indeterminate', "
        "indeterminate_reason = 'coverage_incomplete' "
        "WHERE role = 'update' AND outcome = 'coverage_incomplete'"
    )
    _merge_revision_candidate_snapshots()
    _rewrite_audits(_upgrade_audit)
    _create_immutability_trigger(legacy=False)
    _create_no_delete_trigger()
    _create_run_reference_triggers()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS research_revisions_immutable_content")
    op.execute("DROP TRIGGER IF EXISTS research_revisions_no_delete")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_insert")
    op.execute("DROP TRIGGER IF EXISTS runs_research_update_refs_update")
    _rewrite_audits(_downgrade_audit)
    op.execute(
        "UPDATE research_revisions SET outcome = CASE "
        "WHEN role = 'initial' THEN 'material_change' "
        "WHEN change_conclusion = 'indeterminate' THEN 'coverage_incomplete' "
        "ELSE change_conclusion END"
    )
    op.drop_column("research_revisions", "indeterminate_reason")
    op.drop_column("research_revisions", "change_conclusion")
    op.drop_column("research_revisions", "role")
    _create_immutability_trigger(legacy=True)
    _create_no_delete_trigger()
    _create_run_reference_triggers()
