"""Amazon Bedrock — first-class native client via the optional langchain-aws extra.

Auth uses the AWS credential chain (no single key env); the model is a Bedrock
model ID / inference profile ID; langchain-aws is imported lazily with a clear
install hint when the [bedrock] extra is absent.
"""
import sys
from types import SimpleNamespace

import pytest

from tradingagents.credentials import use_credentials
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.factory import create_llm_client
from tradingagents.llm_clients.validators import validate_model


@pytest.mark.unit
def test_factory_routes_bedrock():
    client = create_llm_client("bedrock", "us.anthropic.claude-opus-4-8-v1:0")
    assert type(client).__name__ == "BedrockClient"


@pytest.mark.unit
def test_bedrock_any_model_and_no_key_env():
    assert validate_model("bedrock", "any.model-id:0") is True
    # Bedrock uses the AWS credential chain, so there is no single key env.
    assert get_api_key_env("bedrock") is None


@pytest.mark.unit
def test_helpful_error_when_langchain_aws_absent(monkeypatch):
    import tradingagents.llm_clients.bedrock_client as bc
    monkeypatch.setattr(bc, "_BEDROCK_CLASS", None)
    monkeypatch.setitem(sys.modules, "langchain_aws", None)  # force ImportError on import
    with pytest.raises(ImportError, match=r"bedrock"):
        create_llm_client("bedrock", "m").get_llm()


def _capture_kwargs(monkeypatch):
    """Stub _bedrock_class so the constructor kwargs are testable without the
    optional langchain-aws extra installed."""
    import tradingagents.llm_clients.bedrock_client as bc
    captured = {}

    class _FakeChat:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(bc, "_bedrock_class", lambda: _FakeChat)
    return captured


@pytest.mark.unit
@use_credentials({"AWS_BEARER_TOKEN_BEDROCK": "bt-secret"})
def test_bearer_token_passed_as_api_key(monkeypatch):
    # #1103: a Bedrock API key authenticates without AWS access keys.
    captured = _capture_kwargs(monkeypatch)
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "bt-secret")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    create_llm_client("bedrock", "us.anthropic.claude-opus-4-8-v1:0", connection={"auth_mode": "bearer", "region": "us-east-1"}).get_llm()
    assert captured["api_key"] == "bt-secret"
    assert captured["region_name"] == "us-east-1"


@pytest.mark.unit
def test_no_bearer_token_omits_api_key(monkeypatch):
    # Without a token, fall back to the AWS credential chain (no api_key kwarg).
    captured = _capture_kwargs(monkeypatch)
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(Session=lambda **kwargs: SimpleNamespace(client=lambda *args, **kw: "system-client")))
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    create_llm_client("bedrock", "us.anthropic.claude-opus-4-8-v1:0").get_llm()
    assert "api_key" not in captured
    assert captured["client"] == "system-client"


@pytest.mark.unit
@pytest.mark.parametrize("explicit_auth", [False, True])
def test_static_credentials_include_session_token_without_environment_fallback(
    monkeypatch, explicit_auth
):
    captured = _capture_kwargs(monkeypatch)
    session_kwargs = {}

    def session(**kwargs):
        session_kwargs.update(kwargs)
        return SimpleNamespace(client=lambda *args, **kwargs: "static-client")

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(Session=session))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ambient-unused")
    auth = {"access_key_id": "saved-id", "secret_access_key": "saved-secret", "session_token": "saved-token"}
    with use_credentials({
        "AWS_ACCESS_KEY_ID": auth["access_key_id"],
        "AWS_SECRET_ACCESS_KEY": auth["secret_access_key"],
        "AWS_SESSION_TOKEN": auth["session_token"],
    }):
        create_llm_client(
            "bedrock", "model-id", connection={"auth_mode": "static", "region": "us-east-1"},
            **({"auth": auth} if explicit_auth else {}),
        ).get_llm()
    assert session_kwargs == {
        "aws_access_key_id": "saved-id",
        "aws_secret_access_key": "saved-secret",
        "aws_session_token": "saved-token",
    }
    assert captured["client"] == "static-client"


@pytest.mark.unit
def test_construction_when_extra_installed(monkeypatch):
    pytest.importorskip("langchain_aws")
    import tradingagents.llm_clients.bedrock_client as bc
    monkeypatch.setattr(bc, "_BEDROCK_CLASS", None)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    llm = create_llm_client("bedrock", "us.anthropic.claude-sonnet-5", connection={"auth_mode": "bearer", "region": "eu-west-1"}).get_llm()
    assert type(llm).__name__ == "NormalizedChatBedrockConverse"
    assert llm.region_name == "eu-west-1"
