from typing import Any

from tradingagents.credentials import credential

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

# Bedrock has no global default region; us-west-2 hosts the broadest model set.
_DEFAULT_REGION = "us-west-2"
_BEDROCK_CLASS = None


def _bedrock_class():
    """Lazily import langchain-aws (the optional ``[bedrock]`` extra) and return a
    ChatBedrockConverse subclass with normalized content output.

    Imported on demand so the optional dependency (and boto3) isn't required by
    the rest of the package; cached after the first call.
    """
    global _BEDROCK_CLASS
    if _BEDROCK_CLASS is not None:
        return _BEDROCK_CLASS

    try:
        from langchain_aws import ChatBedrockConverse
    except ImportError as exc:
        raise ImportError(
            "AWS Bedrock support requires the optional 'langchain-aws' dependency. "
            "Install it from the source checkout with: "
            "uv sync --locked --no-dev --extra bedrock"
        ) from exc

    class NormalizedChatBedrockConverse(ChatBedrockConverse):
        """ChatBedrockConverse with normalized (string) content output."""

        def invoke(self, input, config=None, **kwargs):
            return normalize_content(super().invoke(input, config, **kwargs))

    _BEDROCK_CLASS = NormalizedChatBedrockConverse
    return _BEDROCK_CLASS


class BedrockClient(BaseLLMClient):
    """Client for Amazon Bedrock via the Converse API (langchain-aws).

    The saved connection explicitly selects DB bearer/static credentials or the
    system AWS credential chain. Region and optional profile come from the
    retained connection snapshot. Model IDs may be inference profile IDs.
    """

    def get_llm(self) -> Any:
        """Return a configured ChatBedrockConverse instance."""
        self.warn_if_unknown_model()
        chat_cls = _bedrock_class()

        connection = self.kwargs.get("connection") or {}
        region = connection.get("region") or _DEFAULT_REGION
        mode = connection.get("auth_mode", "system")
        llm_kwargs = {"model": self.model, "region_name": region}
        if mode == "bearer":
            token = credential("AWS_BEARER_TOKEN_BEDROCK")
            if not token:
                raise ValueError("Configure the Bedrock bearer credential in Settings")
            llm_kwargs["api_key"] = token
        else:
            import boto3
            session_kwargs = {}
            if mode == "static":
                key, secret = credential("AWS_ACCESS_KEY_ID"), credential("AWS_SECRET_ACCESS_KEY")
                if not key or not secret:
                    raise ValueError("Configure AWS access credentials in Settings")
                session_kwargs = {"aws_access_key_id": key, "aws_secret_access_key": secret,
                                  "aws_session_token": credential("AWS_SESSION_TOKEN")}
            else:
                session_kwargs["profile_name"] = connection.get("aws_profile")
            session = boto3.Session(**session_kwargs)
            llm_kwargs["client"] = session.client("bedrock-runtime", region_name=region)
        for key in ("temperature", "max_tokens", "max_retries", "callbacks"):
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]
        return chat_cls(**llm_kwargs)

    def validate_model(self) -> bool:
        """Validate model for Bedrock (any model ID accepted)."""
        return validate_model("bedrock", self.model)
