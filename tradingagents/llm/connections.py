"""Resolve model behavior and authentication for one retained role binding."""

from tradingagents.credentials import credential
from tradingagents.llm.factory import create_llm_client
from tradingagents.llm.models import credential_name
from tradingagents.llm.reasoning_effort import RESOLVED_MARKER, resolve_reasoning_effort


def serializer_overrides(provider, model):
    if provider == "deepseek" and model in {"deepseek-v4-flash", "deepseek-v4-pro"}:
        return {"temperature": 0.0, "extra_body": {"thinking": {"type": "disabled"}}}
    return None


def build_model(binding, *, temperature=None, max_retries=None, callbacks=None, serializer=False):
    connection = binding.connection
    auth = {
        field: credential(credential_name(connection.id, field))
        for field in connection.credential_fields()
    }
    missing = connection.missing_fields(auth)
    if missing:
        raise ValueError("Connection requires: " + ", ".join(missing))
    kwargs = {
        "connection": connection.transport.model_dump(exclude={"kind"}),
        "auth": auth,
        "api_key": auth.get("api_key") or "EMPTY",
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    if callbacks:
        kwargs["callbacks"] = callbacks
    config = {
        **connection.reasoning_defaults,
        "llm_provider": connection.compatibility,
        "quick_think_llm": binding.model,
        "quick_reasoning_effort": binding.reasoning_effort,
    }
    resolution = resolve_reasoning_effort(config, "quick")
    kwargs.update(resolution.kwargs)
    kwargs[RESOLVED_MARKER] = True
    if connection.transport.kind in {"chat_completions", "responses"}:
        kwargs["use_responses_api"] = connection.transport.kind == "responses"
    if serializer:
        overrides = serializer_overrides(connection.compatibility, binding.model)
        if overrides:
            for key in resolution.kwargs:
                kwargs.pop(key, None)
            kwargs.update(overrides)
    return create_llm_client(
        connection.compatibility,
        binding.model,
        getattr(connection.transport, "base_url", None),
        **kwargs,
    ).get_llm()


def aws_client(connection, auth, service):
    import boto3

    if connection.get("auth_mode") == "bearer":
        raise ValueError(
            "Model discovery is unavailable for bearer authentication; enter a model ID"
        )
    if connection.get("auth_mode") == "static":
        if not auth.get("access_key_id") or not auth.get("secret_access_key"):
            raise ValueError("Configure AWS access credentials")
        kwargs = {
            "aws_access_key_id": auth["access_key_id"],
            "aws_secret_access_key": auth["secret_access_key"],
            "aws_session_token": auth.get("session_token"),
        }
    else:
        kwargs = {"profile_name": connection.get("aws_profile")}
    return boto3.Session(**kwargs).client(
        service, region_name=connection.get("region", "us-west-2")
    )
