"""Safe recording primitives for opt-in, real-model graph evaluations."""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import message_to_dict
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tradingagents.application.contracts import (
    AnalystReport,
    EvidenceBundle,
    EvidenceItem,
    EvidenceQuality,
    EvidenceTable,
    EvidenceTableCell,
    EvidenceTableColumn,
    EvidenceTableRow,
    ResearchArtifact,
    ResearchDecision,
    ResearchRating,
    RunMetrics,
)

from .contracts import (
    EvalIssue,
    EvalMeasurement,
    EvalVariant,
    QualityScores,
)


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvaluationRuntimeIdentity(FrozenModel):
    commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    provider: str = Field(min_length=1)
    quick_model: str = Field(min_length=1)
    deep_model: str = Field(min_length=1)
    quick_reasoning_effort: str | None = None
    deep_reasoning_effort: str | None = None
    output_language: str = Field(min_length=1)
    temperature: float | None = None


class FrozenAnalystInput(FrozenModel):
    """One Analyst's immutable tool transcript and role-local evidence."""

    analyst: Literal["market", "social", "news", "fundamentals"]
    tool_response: str = Field(min_length=1)
    evidence: EvidenceBundle
    table_expected: bool = False


class FrozenEvaluationCase(FrozenModel):
    """One immutable prompt input built from recorded tool responses."""

    case_id: str = Field(min_length=1)
    evidence: EvidenceBundle
    analyst_inputs: tuple[FrozenAnalystInput, ...] = Field(min_length=1)
    reports: dict[str, AnalystReport] = Field(default_factory=dict)
    expected_rating: ResearchRating | None = None
    expected_risk_terms: tuple[str, ...] = ()
    table_expected: bool = False

    @model_validator(mode="after")
    def validate_reports(self) -> FrozenEvaluationCase:
        analysts = tuple(item.analyst for item in self.analyst_inputs)
        if len(analysts) != len(set(analysts)):
            raise ValueError("analyst inputs must use unique roles")
        combined_refs = {item.ref for item in self.evidence.items}
        for item in self.analyst_inputs:
            if (
                item.evidence.instrument != self.evidence.instrument
                or item.evidence.analysis_date != self.evidence.analysis_date
            ):
                raise ValueError(
                    "analyst evidence must match the case instrument and cutoff"
                )
            if not {
                evidence.ref for evidence in item.evidence.items
            }.issubset(combined_refs):
                raise ValueError(
                    "analyst evidence must belong to the combined bundle"
                )
        for key, report in self.reports.items():
            if key != report.analyst:
                raise ValueError("report key must match its analyst")
            if key not in analysts:
                raise ValueError("report has no matching Analyst input")
        if self.reports and set(self.reports) != set(analysts):
            raise ValueError(
                "graph-ready cases require one report for every Analyst input"
            )
        return self


class FrozenEvaluationSuite(FrozenModel):
    suite_version: Literal["2"] = "2"
    cases: tuple[FrozenEvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_cases(self) -> FrozenEvaluationSuite:
        case_ids = tuple(case.case_id for case in self.cases)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case IDs must be unique")
        return self


class RecordedEvalOutput(FrozenModel):
    """Complete output record produced before blinded quality scoring."""

    suite_version: Literal["2"] = "2"
    variant: EvalVariant
    case_id: str = Field(min_length=1)
    source_case_id: str = Field(min_length=1)
    repetition: int = Field(ge=1, le=3)
    runtime: EvaluationRuntimeIdentity
    prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime_prompt_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    metrics: RunMetrics
    risk_recall: float = Field(ge=0.0, le=1.0)
    issues: tuple[EvalIssue, ...] = ()
    evidence: EvidenceBundle
    reports: dict[str, AnalystReport]
    decision: ResearchDecision | None = None
    artifacts: tuple[ResearchArtifact, ...] = ()
    raw_baseline_output: dict[str, Any] | None = None

    @property
    def severe_issues(self) -> int:
        return sum(issue.severity == "severe" for issue in self.issues)

    @property
    def record_id(self) -> str:
        return f"{self.variant}:{self.case_id}:{self.repetition}"


class EvalReview(FrozenModel):
    """Blinded rubric result kept separate from generated output."""

    record_id: str = Field(min_length=1)
    quality: QualityScores
    reviewer: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)


class EvaluationCallPlan(FrozenModel):
    analyst_jobs: int = Field(ge=0)
    graph_cases: int = Field(ge=0)
    repetitions: int = Field(default=3, ge=1, le=3)
    primary_calls: dict[str, int]
    total_primary_calls_min: int = Field(ge=0)
    total_primary_calls_max: int = Field(ge=0)
    recovery_calls_excluded: bool = True


class PromptHashCallback(BaseCallbackHandler):
    """Hash the dynamic runtime prompt trace without persisting prompt text."""

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._runs: set[Any] = set()
        self._hashes: list[str] = []

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        **kwargs: Any,
    ) -> None:
        self._capture({"prompts": prompts}, kwargs)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        **kwargs: Any,
    ) -> None:
        self._capture(
            {
                "messages": [
                    [message_to_dict(message) for message in batch]
                    for batch in messages
                ]
            },
            kwargs,
        )

    def _capture(
        self,
        payload: dict[str, Any],
        kwargs: dict[str, Any],
    ) -> None:
        run_id = kwargs.get("run_id")
        safe_params = {
            key: value
            for key, value in (kwargs.get("invocation_params") or {}).items()
            if key in {"tools", "tool_choice", "response_format"}
        }
        digest = canonical_hash({"payload": payload, "params": safe_params})
        with self._lock:
            if run_id is not None and run_id in self._runs:
                return
            if run_id is not None:
                self._runs.add(run_id)
            self._hashes.append(digest)

    def digest(self) -> str:
        with self._lock:
            return canonical_hash(sorted(self._hashes))


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_call_plan(
    *,
    analyst_jobs: int,
    graph_cases: int,
    repetitions: int = 3,
) -> EvaluationCallPlan:
    """Return primary logical calls before any bounded recovery attempts."""

    factor = repetitions
    primary = {
        "main_analyst": analyst_jobs * factor,
        "v2_analyst": analyst_jobs * factor,
        "main_medium": graph_cases * factor * 18,
        "v2_standard": graph_cases * factor * 8,
        "v2_deep_min": graph_cases * factor * 10,
        "v2_deep_max": graph_cases * factor * 14,
    }
    minimum = sum(
        primary[key]
        for key in (
            "main_analyst",
            "v2_analyst",
            "main_medium",
            "v2_standard",
            "v2_deep_min",
        )
    )
    maximum = minimum - primary["v2_deep_min"] + primary["v2_deep_max"]
    return EvaluationCallPlan(
        analyst_jobs=analyst_jobs,
        graph_cases=graph_cases,
        repetitions=repetitions,
        primary_calls=primary,
        total_primary_calls_min=minimum,
        total_primary_calls_max=maximum,
    )


def measurement_from_record(
    record: RecordedEvalOutput,
    review: EvalReview,
    *,
    artifact_path: str,
) -> EvalMeasurement:
    if review.record_id != record.record_id:
        raise ValueError("review does not identify the recorded output")
    layer, profile = {
        "main_analyst": ("analyst", "analyst"),
        "v2_analyst": ("analyst", "analyst"),
        "main_medium": ("graph", "medium"),
        "v2_standard": ("graph", "standard"),
        "v2_deep": ("graph", "deep"),
    }[record.variant]
    runtime = record.runtime
    return EvalMeasurement(
        layer=layer,
        variant=record.variant,
        profile=profile,
        commit_sha=runtime.commit_sha,
        provider=runtime.provider,
        quick_model=runtime.quick_model,
        deep_model=runtime.deep_model,
        quick_reasoning_effort=runtime.quick_reasoning_effort,
        deep_reasoning_effort=runtime.deep_reasoning_effort,
        output_language=runtime.output_language,
        temperature=runtime.temperature,
        prompt_hash=record.prompt_hash,
        runtime_prompt_hash=record.runtime_prompt_hash,
        evidence_hash=record.evidence_hash,
        output_hash=record.output_hash,
        artifact_path=artifact_path,
        case_id=record.case_id,
        repetition=record.repetition,
        quality=review.quality,
        reviewer=review.reviewer,
        rubric_version=review.rubric_version,
        llm_calls=record.metrics.llm_calls,
        input_tokens=record.metrics.input_tokens,
        output_tokens=record.metrics.output_tokens,
        wall_time_seconds=record.metrics.wall_time_seconds,
        risk_recall=record.risk_recall,
        severe_issues=record.severe_issues,
    )


def load_suite(path: Path) -> FrozenEvaluationSuite:
    return FrozenEvaluationSuite.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def prepare_contract_fixture_suite(
    fixture_root: Path,
) -> FrozenEvaluationSuite:
    """Convert checked-in deterministic contract fixtures into prompt inputs."""

    cases = []
    for path in sorted(fixture_root.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("version") != "1":
            raise ValueError(f"unsupported fixture version in {path}")
        market = str(document["market"])
        analyst = "market" if market == "CRYPTO" else "fundamentals"
        for raw_case in document["cases"]:
            analysis_date = date.fromisoformat(raw_case["analysis_date"])
            items = []
            for raw in raw_case["evidence"]:
                available_at = raw.get("available_at")
                items.append(
                    EvidenceItem.create(
                        source=raw["source"],
                        evidence_type=raw["key"],
                        requested_date=analysis_date,
                        effective_date=(
                            date.fromisoformat(raw["effective_date"])
                            if raw.get("effective_date")
                            else None
                        ),
                        available_at=(
                            datetime.fromisoformat(
                                available_at.replace("Z", "+00:00")
                            )
                            if available_at
                            else None
                        ),
                        content=raw.get("content"),
                        value=raw.get("value"),
                        quality=EvidenceQuality(raw["quality"]),
                        fallback=bool(raw.get("fallback")),
                        provenance=dict(raw.get("provenance") or {}),
                    )
                )
            evidence = EvidenceBundle(
                instrument=document["ticker"],
                analysis_date=analysis_date,
                items=tuple(items),
            )
            usable_values = sum(
                item.quality is not EvidenceQuality.UNAVAILABLE
                and item.value is not None
                for item in items
            )
            tool_response = json.dumps(
                {
                    "market": market,
                    "scenario": raw_case["scenario"],
                    "evidence": raw_case["evidence"],
                    "tool_claims": raw_case["claims"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            cases.append(
                FrozenEvaluationCase(
                    case_id=f"{market}-{raw_case['scenario']}",
                    evidence=evidence,
                    analyst_inputs=(
                        FrozenAnalystInput(
                            analyst=analyst,
                            tool_response=tool_response,
                            evidence=evidence,
                            table_expected=usable_values >= 2,
                        ),
                    ),
                    expected_rating=ResearchRating(raw_case["rating"]),
                    expected_risk_terms=tuple(raw_case["risks"]),
                    table_expected=usable_values >= 2,
                )
            )
    return FrozenEvaluationSuite(cases=tuple(cases))


def prepare_quality_fixture_suite(spec_path: Path) -> FrozenEvaluationSuite:
    """Expand the checked-in cross-market quality spec into sealed role inputs."""

    specification = json.loads(spec_path.read_text(encoding="utf-8"))
    if specification.get("version") != "1":
        raise ValueError("unsupported quality-fixture version")
    cases = []
    for market in specification["markets"]:
        for scenario in specification["scenarios"]:
            cutoff = date.fromisoformat(scenario["analysis_date"])
            roles = ("market", "social", "news")
            if market["market"] != "CRYPTO":
                roles = (*roles, "fundamentals")
            analyst_inputs = tuple(
                _quality_analyst_input(
                    market=market,
                    scenario=scenario,
                    cutoff=cutoff,
                    analyst=analyst,
                )
                for analyst in roles
            )
            combined_items = tuple(
                item
                for analyst_input in analyst_inputs
                for item in analyst_input.evidence.items
            )
            combined_tables = tuple(
                table
                for analyst_input in analyst_inputs
                for table in analyst_input.evidence.tables
            )
            evidence = EvidenceBundle(
                instrument=market["ticker"],
                analysis_date=cutoff,
                items=combined_items,
                tables=combined_tables,
            )
            cases.append(
                FrozenEvaluationCase(
                    case_id=f"{market['market']}-{scenario['name']}",
                    evidence=evidence,
                    analyst_inputs=analyst_inputs,
                    expected_rating=ResearchRating(scenario["rating"]),
                    expected_risk_terms=tuple(scenario["risks"]),
                    table_expected=True,
                )
            )
    return FrozenEvaluationSuite(cases=tuple(cases))


def _quality_analyst_input(
    *,
    market: dict[str, Any],
    scenario: dict[str, Any],
    cutoff: date,
    analyst: Literal["market", "social", "news", "fundamentals"],
) -> FrozenAnalystInput:
    source_key = {
        "market": "market_source",
        "social": "sentiment_source",
        "news": "news_source",
        "fundamentals": "fundamentals_source",
    }[analyst]
    source = market[source_key]
    raw = (
        scenario["sentiment"]
        if analyst == "social"
        else scenario[analyst]
    )
    if raw is None:
        item = EvidenceItem.create(
            source=source,
            evidence_type=f"{analyst}.coverage",
            requested_date=cutoff,
            content=None,
            quality=EvidenceQuality.UNAVAILABLE,
            provenance={"timing": "no usable point-in-time coverage"},
        )
        table = _quality_table(
            analyst=analyst,
            source=source,
            rows=(
                (
                    "coverage",
                    "Coverage",
                    "No usable point-in-time coverage",
                    item,
                ),
            ),
        )
        bundle = EvidenceBundle(
            instrument=market["ticker"],
            analysis_date=cutoff,
            items=(item,),
            tables=(table,),
        )
        return FrozenAnalystInput(
            analyst=analyst,
            tool_response=canonical_json(
                {
                    "status": "unavailable",
                    "source": source,
                    "evidence_ref": item.ref,
                    "table": table.model_dump(mode="json"),
                }
            ),
            evidence=bundle,
            table_expected=True,
        )

    effective = cutoff - timedelta(
        days={
            "market": 1,
            "social": 2,
            "news": 3,
            "fundamentals": 14,
        }[analyst]
    )
    available_at = datetime.combine(
        effective,
        time(hour=12),
        tzinfo=timezone.utc,
    )
    rows: list[tuple[str, str, Any, str | None]] = []
    if analyst == "market":
        rows = [
            ("close", "Completed close", raw["close"], market["currency"]),
            ("sma_50", "50-session SMA", raw["sma_50"], market["currency"]),
            ("rsi", "RSI", raw["rsi"], None),
            (
                "volume_change",
                "Volume change",
                raw["volume_change_pct"],
                "%",
            ),
        ]
    elif analyst == "fundamentals":
        rows = [
            (
                "revenue_growth",
                "Revenue growth",
                raw["revenue_growth_pct"],
                "%",
            ),
            (
                "operating_margin",
                "Operating margin",
                raw["operating_margin_pct"],
                "%",
            ),
            (
                "prior_operating_margin",
                "Prior operating margin",
                raw["prior_operating_margin_pct"],
                "%",
            ),
            (
                "free_cash_flow_margin",
                "Free-cash-flow margin",
                raw["free_cash_flow_margin_pct"],
                "%",
            ),
            (
                "valuation_multiple",
                "Observed valuation multiple",
                raw["valuation_multiple"],
                "x",
            ),
        ]
    elif analyst == "news":
        rows = [
            (
                "company_event",
                "Direct company event",
                raw["company_event"],
                None,
            ),
            (
                "context_event",
                "Industry or macro context",
                raw["context_event"],
                None,
            ),
            ("impact", "Impact path", raw["impact"], None),
        ]
    else:
        rows = [
            ("sentiment_score", "Sentiment score", raw["score"], None),
            (
                "positioning_change",
                "Positioning change",
                raw["positioning_change_pct"],
                "%",
            ),
            ("direction", "Source direction", raw["direction"], None),
        ]

    items = []
    table_rows = []
    for key, label, value, unit in rows:
        display = f"{value}{unit or ''}"
        content = (
            f"{label} was {display} as of {effective.isoformat()} "
            f"according to {source}."
        )
        item = EvidenceItem.create(
            source=source,
            evidence_type=f"{analyst}.{key}",
            requested_date=cutoff,
            effective_date=effective,
            available_at=available_at,
            content=content,
            value=value,
            unit=unit,
            quality=EvidenceQuality.HIGH,
            provenance={
                "fixture": "quality-v1",
                "market": market["market"],
                "scenario": scenario["name"],
            },
        )
        items.append(item)
        table_rows.append((key, label, display, item))
    table = _quality_table(
        analyst=analyst,
        source=source,
        rows=tuple(table_rows),
    )
    bundle = EvidenceBundle(
        instrument=market["ticker"],
        analysis_date=cutoff,
        items=tuple(items),
        tables=(table,),
    )
    return FrozenAnalystInput(
        analyst=analyst,
        tool_response=canonical_json(
            {
                "instrument": market["ticker"],
                "market": market["market"],
                "scenario": scenario["name"],
                "cutoff": cutoff.isoformat(),
                "source": source,
                "items": [
                    item.model_dump(mode="json") for item in items
                ],
                "tables": [table.model_dump(mode="json")],
            }
        ),
        evidence=bundle,
        table_expected=True,
    )


def _quality_table(
    *,
    analyst: str,
    source: str,
    rows: tuple[tuple[str, str, Any, EvidenceItem], ...],
) -> EvidenceTable:
    columns = (
        EvidenceTableColumn(key="metric", label="Metric"),
        EvidenceTableColumn(key="value", label="Observed value"),
        EvidenceTableColumn(key="source", label="Source"),
    )
    table_rows = tuple(
        EvidenceTableRow(
            id=key,
            cells={
                "metric": EvidenceTableCell(
                    raw_value=label,
                    source_refs=(item.ref,),
                ),
                "value": EvidenceTableCell(
                    raw_value=item.value,
                    source_refs=(item.ref,),
                ),
                "source": EvidenceTableCell(
                    raw_value=source,
                    source_refs=(item.ref,),
                ),
            },
            source_refs=(item.ref,),
        )
        for key, label, display, item in rows
    )
    return EvidenceTable.create(
        title=f"{analyst.title()} evidence table",
        purpose="Expose the complete frozen observations used by this Analyst.",
        columns=columns,
        rows=table_rows,
        evidence_refs=tuple(
            item.ref for _key, _label, _display, item in rows
        ),
        source_format="structured",
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def load_records(path: Path) -> tuple[RecordedEvalOutput, ...]:
    return tuple(
        RecordedEvalOutput.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def load_reviews(path: Path) -> tuple[EvalReview, ...]:
    return tuple(
        EvalReview.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def write_jsonl(path: Path, values: list[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(value.model_dump_json() for value in values) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
