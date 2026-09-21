"""Stable submission identity, independent of mutable configuration defaults."""

from typing import Any

from tradingagents.domain.runs import AnalysisRequest


def submission_identity(request: AnalysisRequest, source_run_id: str | None = None) -> dict[str, Any]:
    payload = request.model_dump(mode='json', exclude_none=True)
    for field in ('profile', 'analysts', 'output_language'):
        if field not in request.model_fields_set:
            payload.pop(field, None)
    models = {role: selection for role, selection in payload.pop('models', {}).items() if selection}
    if models:
        payload['models'] = models
    return {'version': 2, 'request': payload, 'source_run_id': source_run_id}
