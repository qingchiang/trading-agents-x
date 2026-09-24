"""Accept optional research references without recomputing model numbers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from tradingagents.data.lookahead import is_near_live
from tradingagents.domain.decision import (
    MarketReferenceLevel,
    NumericTemporalBasis,
    ResearchDecision,
    ScenarioReferenceRange,
)
from tradingagents.domain.evidence import EvidenceBundle, EvidenceTemporalScope
from tradingagents.domain.instruments import market_timezone
from tradingagents.research.synthesis.drafts import EventWriter, ResearchDecisionDraft
from tradingagents.research.synthesis.output_validation import OutputValidationError

REFERENCE_GUIDANCE = (
    "Serialize the adopted research conclusion, including optional scenario reference_ranges "
    "and market_reference_levels only when the research already uses them. Do not invent "
    "numbers or fill every scenario with a range. Retain the original values, units, "
    "assumptions, interpretation, limitations and Evidence refs. Distinguish conditional "
    "fundamental valuations from technical bands, historical ranges and analyst consensus. "
    "A range requires low strictly less than high; a single numeric level belongs in "
    "market_reference_levels. Use observed only for a direct source value with an Evidence "
    "locator, interpreted for a judgment, and derived for a model calculation; none means "
    "program-verified. Do not supply calculation IDs or a separate valuation card. "
    "as_of_date describes supporting data, never a forecast horizon. date_evidence_refs "
    "must be a subset of evidence_refs. Label live-only observations as live_snapshot, "
    "not point_in_time. Omit optional references when their supporting metadata is unknown. "
    "Confidence must be low, medium or high, not a number or probability."
)


def assemble_decision(
    draft: ResearchDecisionDraft,
    *,
    bundle: EvidenceBundle,
    allowed_evidence_refs: set[str],
    baseline_bundle: EvidenceBundle | None = None,
    node: str,
    event_writer: EventWriter | None = None,
) -> ResearchDecision:
    """Keep the strict core and drop only invalid optional reference candidates."""

    sources = {
        item.ref: (item, source_bundle)
        for source_bundle in (baseline_bundle, bundle)
        if source_bundle is not None
        for item in source_bundle.items
    }

    def omit(path: str, codes: list[str]) -> None:
        if event_writer is not None:
            event_writer(
                {
                    "event_type": "decision.reference_omitted",
                    "node": node,
                    "payload": {"field_path": path, "validation_issues": codes[:8]},
                }
            )

    def validate_reference(value: Any) -> None:
        endpoints = (
            (value.low, value.high) if isinstance(value, ScenarioReferenceRange) else (value,)
        )
        for endpoint in endpoints:
            if not set(endpoint.evidence_refs).issubset(allowed_evidence_refs):
                raise OutputValidationError("reference.refs_invalid")
            if endpoint.temporal_basis is NumericTemporalBasis.POINT_IN_TIME:
                if endpoint.as_of_date > bundle.analysis_date:
                    raise OutputValidationError("reference.future_date")
            elif (
                endpoint.as_of_date
                > bundle.sealed_at.astimezone(market_timezone(bundle.instrument)).date()
            ):
                raise OutputValidationError("reference.future_snapshot")
            live_dates = []
            for ref in endpoint.evidence_refs:
                if ref not in sources:
                    raise OutputValidationError("reference.source_unavailable")
                item, source_bundle = sources[ref]
                live_origins = [
                    origin
                    for origin in item.origins
                    if origin.temporal_scope is EvidenceTemporalScope.LIVE_ONLY
                ]
                if not live_origins:
                    continue
                if endpoint.temporal_basis is not NumericTemporalBasis.LIVE_SNAPSHOT:
                    raise OutputValidationError("reference.temporal_basis_mismatch")
                for origin in live_origins:
                    try:
                        retrieved = datetime.fromisoformat(origin.retrieved_at or "")
                    except ValueError as exc:
                        raise OutputValidationError("reference.snapshot_date_unavailable") from exc
                    if retrieved.utcoffset() is None or retrieved > source_bundle.sealed_at:
                        raise OutputValidationError("reference.snapshot_date_invalid")
                    if not is_near_live(
                        source_bundle.analysis_date.isoformat(),
                        source_bundle.instrument,
                        now=retrieved,
                    ):
                        raise OutputValidationError("reference.snapshot_outside_window")
                    live_dates.append(
                        retrieved.astimezone(market_timezone(bundle.instrument)).date()
                    )
            if live_dates and endpoint.as_of_date != max(live_dates):
                raise OutputValidationError("reference.snapshot_date_mismatch")
            locator = endpoint.source_locator
            if locator and locator.table_id:
                source_bundle = sources[locator.evidence_ref][1]
                table = next((t for t in source_bundle.tables if t.id == locator.table_id), None)
                row = (
                    next((r for r in table.rows if r.id == locator.row_id), None) if table else None
                )
                if (
                    row is None
                    or locator.column not in row.cells
                    or locator.evidence_ref
                    not in (
                        row.cells[locator.column].source_refs
                        or row.source_refs
                        or table.evidence_refs
                    )
                ):
                    raise OutputValidationError("reference.locator_invalid")

    def accepted(candidates: Any, schema: type, path: str) -> list:
        if not isinstance(candidates, (list, tuple)):
            omit(path, ["reference.expected_array"])
            return []
        values = []
        for index, candidate in enumerate(candidates):
            try:
                value = schema.model_validate(candidate)
                validate_reference(value)
            except ValidationError as exc:
                omit(
                    f"{path}.{index}",
                    [str(e["type"]) for e in exc.errors(include_input=False, include_url=False)],
                )
            except OutputValidationError as exc:
                omit(f"{path}.{index}", [str(exc)])
            else:
                values.append(value)
        return values

    payload = draft.model_dump(mode="python", warnings=False)
    for index, scenario in enumerate(draft.scenarios):
        payload["scenarios"][index]["reference_ranges"] = accepted(
            scenario.reference_ranges,
            ScenarioReferenceRange,
            f"scenarios.{scenario.kind.value}.reference_ranges",
        )
    payload["market_reference_levels"] = accepted(
        draft.market_reference_levels, MarketReferenceLevel, "market_reference_levels"
    )
    decision = ResearchDecision.model_validate(payload)
    if not set(decision.evidence_refs).issubset(allowed_evidence_refs):
        raise OutputValidationError("decision.refs_invalid")
    return decision
