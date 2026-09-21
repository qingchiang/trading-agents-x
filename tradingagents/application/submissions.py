"""Stable submission identity, independent of mutable configuration defaults."""

from typing import TYPE_CHECKING, Any

from tradingagents.domain.runs import AnalysisRequest

if TYPE_CHECKING:
    pass

_INHERITED = {
    "profile",
    "analysts",
    "llm_provider",
    "connection_id",
    "quick_connection_id",
    "deep_connection_id",
    "quick_model",
    "deep_model",
    "quick_reasoning_effort",
    "deep_reasoning_effort",
    "output_language",
}


def submission_identity(
    request: AnalysisRequest, source_run_id: str | None = None
) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    for field in _INHERITED:
        if field not in request.model_fields_set or payload.get(field) is None:
            payload.pop(field, None)
    if request.research_kind == "incremental":
        for field in ("quick_connection_id", "quick_model", "quick_reasoning_effort"):
            payload.pop(field, None)
    return {"version": 1, "request": payload, "source_run_id": source_run_id}
