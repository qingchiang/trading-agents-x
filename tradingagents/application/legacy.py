"""Idempotent, read-only parser and importer for Markdown decision memory."""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from tradingagents.agents.utils.rating import parse_rating
from tradingagents.dataflows.symbol_utils import (
    match_exchange_suffix,
    normalize_symbol,
)

from .contracts import (
    AnalysisRequest,
    ResearchDecision,
    ResearchRating,
    RunProfile,
)
from .repository import RunRepository
from .settings import AppSettings

_SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
_DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
_REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)
_RANGE_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(\d+)d\]"
)


class ImportIssue(BaseModel):
    model_config = ConfigDict(frozen=True)
    index: int
    content_hash: str
    error: str


class LegacyImportReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str
    dry_run: bool
    backup: str | None = None
    total_blocks: int = 0
    importable: int = 0
    imported: int = 0
    skipped: int = 0
    malformed: int = 0
    issues: tuple[ImportIssue, ...] = ()
    run_ids: tuple[str, ...] = ()


class LegacyMemoryImporter:
    def __init__(
        self,
        settings: AppSettings,
        repository: RunRepository,
    ):
        self.settings = settings
        self.repository = repository

    def import_file(
        self,
        source: Path,
        *,
        dry_run: bool = True,
        create_backup: bool = True,
    ) -> LegacyImportReport:
        source = source.expanduser().resolve()
        text = source.read_text(encoding="utf-8")
        blocks = [block.strip() for block in text.split(_SEPARATOR) if block.strip()]
        issues: list[ImportIssue] = []
        parsed: list[tuple[str, dict[str, Any]]] = []
        skipped = 0
        for index, block in enumerate(blocks, start=1):
            digest = hashlib.sha256(block.encode()).hexdigest()
            if self.repository.has_legacy_import(digest):
                skipped += 1
                continue
            try:
                parsed.append((digest, self._parse(block)))
            except Exception as exc:
                issues.append(
                    ImportIssue(
                        index=index,
                        content_hash=digest,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                if not dry_run:
                    self.repository.record_legacy_import(
                        str(source),
                        digest,
                        "error",
                        error_message=str(exc),
                    )
        if dry_run:
            return LegacyImportReport(
                source=str(source),
                dry_run=True,
                total_blocks=len(blocks),
                importable=len(parsed),
                skipped=skipped,
                malformed=len(issues),
                issues=tuple(issues),
            )

        backup = None
        if create_backup and parsed:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = source.with_name(f"{source.name}.bak.{stamp}")
            shutil.copy2(source, backup_path)
            backup = str(backup_path)
        run_ids = []
        for digest, entry in parsed:
            run_id = self._import_entry(source, digest, entry)
            if run_id:
                run_ids.append(run_id)
            else:
                skipped += 1
        return LegacyImportReport(
            source=str(source),
            dry_run=False,
            backup=backup,
            total_blocks=len(blocks),
            importable=len(parsed),
            imported=len(run_ids),
            skipped=skipped,
            malformed=len(issues),
            issues=tuple(issues),
            run_ids=tuple(run_ids),
        )

    def _import_entry(
        self,
        source: Path,
        digest: str,
        entry: dict[str, Any],
    ) -> str | None:
        ticker = normalize_symbol(entry["ticker"])
        asset_type = entry.get("asset_type")
        request = AnalysisRequest(
            ticker=ticker,
            analysis_date=entry["analysis_date"],
            asset_type=asset_type,
            profile=RunProfile.STANDARD,
        )
        rating_text = parse_rating(entry["decision"])
        try:
            rating = ResearchRating(rating_text)
        except ValueError:
            rating = ResearchRating.HOLD
        decision = ResearchDecision(
            rating=rating,
            confidence=0.5,
            thesis=entry["decision"] or "Imported legacy decision.",
            evidence_refs=(),
            catalysts=(),
            risks=(),
            invalidation_conditions=(),
            time_horizon=(
                f"Legacy {entry['holding_intervals']}-interval feedback window"
            ),
        )
        benchmark = self._benchmark(ticker)
        return self.repository.import_legacy_memory(
            source_path=str(source),
            content_hash=digest,
            request=request,
            decision=decision,
            benchmark=benchmark,
            raw_return=entry["raw_return"],
            alpha_return=entry["alpha_return"],
            holding_intervals=entry["holding_intervals"],
            observation_start=entry["observation_start"],
            observation_end=entry["observation_end"],
            reflection=entry["reflection"],
        )

    def _parse(self, raw: str) -> dict[str, Any]:
        lines = raw.splitlines()
        if not lines or not lines[0].startswith("[") or not lines[0].endswith("]"):
            raise ValueError("missing legacy header")
        fields = [part.strip() for part in lines[0][1:-1].split("|")]
        if len(fields) < 4:
            raise ValueError("legacy header has fewer than four fields")
        analysis_date = date.fromisoformat(fields[0])
        ticker = fields[1]
        if not ticker:
            raise ValueError("ticker is empty")
        pending = fields[3] == "pending"
        raw_return = None if pending else _parse_percent(fields[3])
        alpha_return = (
            None
            if pending or len(fields) < 5
            else _parse_percent(fields[4])
        )
        holding = 5
        if len(fields) >= 6:
            match = re.fullmatch(r"(\d+)d", fields[5])
            if match:
                holding = int(match.group(1))
        body = "\n".join(lines[1:]).strip()
        decision_match = _DECISION_RE.search(body)
        if not decision_match or not decision_match.group(1).strip():
            raise ValueError("DECISION section is missing")
        reflection_match = _REFLECTION_RE.search(body)
        reflection = (
            reflection_match.group(1).strip() if reflection_match else ""
        )
        observation_start = None
        observation_end = None
        range_match = _RANGE_RE.search(reflection)
        if range_match:
            observation_start = date.fromisoformat(range_match.group(1))
            observation_end = date.fromisoformat(range_match.group(2))
            holding = int(range_match.group(3))
        metadata: dict[str, str] = {}
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("META:"):
                for field in stripped.removeprefix("META:").split("|"):
                    key, separator, value = field.strip().partition("=")
                    if separator:
                        metadata[key] = value.strip()
            break
        return {
            "ticker": ticker,
            "analysis_date": analysis_date,
            "asset_type": metadata.get("asset_type"),
            "decision": decision_match.group(1).strip(),
            "raw_return": raw_return,
            "alpha_return": alpha_return,
            "holding_intervals": holding,
            "observation_start": observation_start,
            "observation_end": observation_end,
            "reflection": reflection,
        }

    def _benchmark(self, ticker: str) -> str:
        config = self.settings.default_run_settings.data_config
        explicit = config.get("benchmark_ticker")
        if explicit:
            return normalize_symbol(str(explicit))
        benchmark_map = config.get("benchmark_map", {})
        suffix = match_exchange_suffix(ticker, benchmark_map)
        return benchmark_map.get(suffix or "", "SPY")


def _parse_percent(value: str) -> float:
    stripped = value.strip()
    if not stripped.endswith("%"):
        raise ValueError(f"invalid percentage: {value!r}")
    return float(stripped.removesuffix("%")) / 100
