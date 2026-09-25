from __future__ import annotations

from tradingagents.llm.runtime import create_run_llms


class _Client:
    def __init__(self, model: str) -> None:
        self.model = model

    def get_llm(self):
        return self


def test_incremental_llm_construction_does_not_initialize_the_quick_model(
    app_settings,
    monkeypatch,
) -> None:
    created: list[str] = []

    def create_client(_provider, model: str, _base_url=None, **_kwargs):
        created.append(model)
        return _Client(model)

    monkeypatch.setattr(
        "tradingagents.llm.connections.create_llm_client",
        create_client,
    )
    settings = app_settings.default_run_settings.model_copy(
        update={"quick_binding": None, "deep_binding": app_settings.default_run_settings.deep_binding.model_copy(update={"model": "valid-deep"}), "research_kind": "incremental"}
    )

    llms = create_run_llms(settings, purpose="incremental")

    assert created == ["valid-deep"]
    assert llms.deep.model == "valid-deep"
    assert llms.quick is None
    assert llms.quick_serializer is None
