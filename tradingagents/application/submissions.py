"""Stable submission identity, independent of mutable configuration defaults."""

from typing import TYPE_CHECKING, Any

from .contracts import AnalysisRequest
from .model_connections import legacy_connection_id

if TYPE_CHECKING:
    from .database import RunRecord

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


def matches_submission(record: "RunRecord", identity: dict[str, Any]) -> bool:
    if record.submission_json is not None:
        return record.submission_json == identity
    # Old records cannot recover omitted fields. Compare supplied choices using
    # retained values only, never today's defaults or current connection records.
    if record.source_run_id != identity["source_run_id"]:
        return False
    retained = dict(record.request_json)
    snapshot = record.config_json
    roles = ("deep",) if retained.get("research_kind") == "incremental" else ("quick", "deep")

    def connection_id(role):
        binding = snapshot.get(f"{role}_binding")
        return (
            binding["connection"]["id"]
            if binding
            else retained.get(f"{role}_connection_id")
            or (
                legacy_connection_id(snapshot["llm_provider"])
                if snapshot.get("llm_provider")
                else None
            )
        )

    for field, value in identity["request"].items():
        if field in ("connection_id", "llm_provider"):
            wanted = legacy_connection_id(value) if field == "llm_provider" else value
            if any(connection_id(role) != wanted for role in roles):
                return False
        elif field in ("quick_connection_id", "deep_connection_id"):
            if connection_id(field.split("_")[0]) != value:
                return False
        elif retained.get(field, snapshot.get(field)) != value:
            return False
    return True
