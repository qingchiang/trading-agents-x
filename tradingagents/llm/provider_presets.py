"""Lightweight presets shared by configuration, discovery and SDK adapters."""

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderSpec:
    label: str
    api_key_env: str | None
    base_url: str | None = None
    base_url_env: str | None = None
    key_optional: bool = False
    placeholder_key: str = "EMPTY"
    require_base_url: bool = False
    use_responses_api: bool = False
    chat_profile: str = "standard"

    @property
    def chat_class(self):
        # Only execution imports SDK classes; metadata remains lightweight.
        from tradingagents.llm.openai_client import (
            DeepSeekChatOpenAI,
            LocalCompatibleChatOpenAI,
            MinimaxChatOpenAI,
            NormalizedChatOpenAI,
        )

        return {
            "standard": NormalizedChatOpenAI,
            "deepseek": DeepSeekChatOpenAI,
            "minimax": MinimaxChatOpenAI,
            "local": LocalCompatibleChatOpenAI,
        }[self.chat_profile]


OPENAI_COMPATIBLE_PROVIDERS = {
    "openai": ProviderSpec(
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        use_responses_api=True,
    ),
    "xai": ProviderSpec(
        label="xAI",
        api_key_env="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
    ),
    "deepseek": ProviderSpec(
        label="DeepSeek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        chat_profile="deepseek",
    ),
    "qwen": ProviderSpec(
        label="Qwen (International)",
        api_key_env="DASHSCOPE_API_KEY",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ),
    "qwen-cn": ProviderSpec(
        label="Qwen (China)",
        api_key_env="DASHSCOPE_CN_API_KEY",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    "glm": ProviderSpec(
        label="Z.AI GLM",
        api_key_env="ZHIPU_API_KEY",
        base_url="https://api.z.ai/api/paas/v4/",
    ),
    "glm-cn": ProviderSpec(
        label="BigModel GLM (China)",
        api_key_env="ZHIPU_CN_API_KEY",
        base_url="https://open.bigmodel.cn/api/paas/v4/",
    ),
    "minimax": ProviderSpec(
        label="MiniMax (International)",
        api_key_env="MINIMAX_API_KEY",
        base_url="https://api.minimax.io/v1",
        chat_profile="minimax",
    ),
    "minimax-cn": ProviderSpec(
        label="MiniMax (China)",
        api_key_env="MINIMAX_CN_API_KEY",
        base_url="https://api.minimaxi.com/v1",
        chat_profile="minimax",
    ),
    "openrouter": ProviderSpec(
        label="OpenRouter",
        api_key_env="OPENROUTER_API_KEY",
        base_url="https://openrouter.ai/api/v1",
    ),
    "mistral": ProviderSpec(
        label="Mistral",
        api_key_env="MISTRAL_API_KEY",
        base_url="https://api.mistral.ai/v1",
    ),
    "kimi": ProviderSpec(
        label="Kimi / Moonshot",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
    ),
    "groq": ProviderSpec(
        label="Groq",
        api_key_env="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
    ),
    "nvidia": ProviderSpec(
        label="NVIDIA NIM",
        api_key_env="NVIDIA_API_KEY",
        base_url="https://integrate.api.nvidia.com/v1",
    ),
    "ollama": ProviderSpec(
        label="Ollama",
        api_key_env=None,
        base_url="http://localhost:11434/v1",
        base_url_env="OLLAMA_BASE_URL",
        key_optional=True,
        placeholder_key="ollama",
    ),
    "openai_compatible": ProviderSpec(
        label="OpenAI-compatible",
        api_key_env="OPENAI_COMPATIBLE_API_KEY",
        key_optional=True,
        require_base_url=True,
        chat_profile="local",
    ),
}


def _is_native_openai_base_url(base_url: str | None) -> bool:
    """True when ``base_url`` is unset or points at api.openai.com.

    This heuristic preserves legacy provider inputs. New connections explicitly
    choose their interface, including Responses for compatible gateways.
    """
    if not base_url:
        return True
    if "://" not in base_url:
        base_url = "https://" + base_url
    host = urlparse(base_url).hostname or ""
    return host == "api.openai.com" or host.endswith(".openai.com")
