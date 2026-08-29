from __future__ import annotations

from tradingagents.application.llms import create_run_llms


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

    def create_client(*, model: str, **_kwargs):
        created.append(model)
        return _Client(model)

    monkeypatch.setattr(
        "tradingagents.application.llms.create_llm_client",
        create_client,
    )
    settings = app_settings.default_run_settings.model_copy(
        update={"quick_model": "invalid-quick", "deep_model": "valid-deep"}
    )

    llms = create_run_llms(settings, purpose="incremental")

    assert created == ["valid-deep"]
    assert llms.deep.model == "valid-deep"
    assert llms.quick is llms.deep
