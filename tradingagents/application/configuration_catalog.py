"""Discoverable configuration metadata; runtime capabilities keep their registries."""

from .configuration import credential_owners, effective_connection
from .configuration_models import ConfigurationField, ConfigurationSchema, ConfigurationValues

# English, simplified Chinese, Japanese. Technical names remain searchable.
LABELS = {
    "profile": ("Research depth", "研究档位", "リサーチの深さ"),
    "analysts": ("Analysts", "分析师", "アナリスト"),
    "llm_provider": ("Default provider", "默认模型服务", "既定のプロバイダー"),
    "quick_think_llm": ("Quick model", "快速模型", "高速モデル"),
    "deep_think_llm": ("Deep model", "深入模型", "詳細モデル"),
    "quick_reasoning_effort": (
        "Quick reasoning effort",
        "快速模型推理强度",
        "高速モデルの推論強度",
    ),
    "deep_reasoning_effort": ("Deep reasoning effort", "深入模型推理强度", "詳細モデルの推論強度"),
    "google_thinking_level": (
        "Google shared thinking level",
        "Google 共享思考强度",
        "Google 共通思考強度",
    ),
    "openai_reasoning_effort": (
        "OpenAI shared reasoning effort",
        "OpenAI 共享推理强度",
        "OpenAI 共通推論強度",
    ),
    "anthropic_effort": (
        "Anthropic shared effort",
        "Anthropic 共享推理强度",
        "Anthropic 共通推論強度",
    ),
    "temperature": ("Sampling temperature", "采样温度", "サンプリング温度"),
    "llm_max_retries": ("LLM retry budget", "模型请求重试次数", "モデルの再試行回数"),
    "output_language": ("Report language", "报告语言", "レポート言語"),
    "news_article_limit": (
        "Ticker news output limit",
        "个股新闻输出条数",
        "銘柄ニュースの出力件数",
    ),
    "yahoo_news_candidate_limit": (
        "Yahoo news candidate limit",
        "Yahoo 新闻候选条数",
        "Yahoo ニュース候補件数",
    ),
    "cn_news_candidate_limit": (
        "China news candidate limit",
        "中国新闻候选条数",
        "中国ニュース候補件数",
    ),
    "sentiment_filing_limit": (
        "Ownership filing output limit",
        "持股与收购披露输出条数",
        "保有・買収開示の出力件数",
    ),
    "ticker_news_lookback_days": (
        "Ticker news lookback offset (days)",
        "个股新闻回溯偏移（天）",
        "銘柄ニュース遡及日数",
    ),
    "social_lookback_days": (
        "Social lookback offset (days)",
        "社交信息回溯偏移（天）",
        "ソーシャル情報遡及日数",
    ),
    "global_news_article_limit": (
        "Global news output limit",
        "全球新闻输出条数",
        "世界ニュースの出力件数",
    ),
    "global_news_candidate_limit": (
        "Global news candidates per query",
        "全球新闻每次查询候选条数",
        "世界ニュースのクエリ当たり候補件数",
    ),
    "global_news_query_limit": (
        "Global news query budget",
        "全球新闻查询预算",
        "世界ニュースのクエリ上限",
    ),
    "global_news_lookback_days": (
        "Global news lookback offset (days)",
        "全球新闻回溯偏移（天）",
        "世界ニュース遡及日数",
    ),
    "global_news_queries": ("Global news search queries", "全球新闻查询词", "世界ニュースの検索語"),
    "news_cache_enabled": ("Reuse observed news", "复用已观察的新闻", "取得済みニュースの再利用"),
    "news_cache_refresh_seconds": (
        "News refresh interval (seconds)",
        "新闻刷新间隔（秒）",
        "ニュース更新間隔（秒）",
    ),
    "news_cache_retention_days": (
        "News cache retention (days)",
        "新闻缓存保留天数",
        "ニュースキャッシュ保持日数",
    ),
    "news_cache_scope_limit": (
        "Cached items per scope",
        "每个范围的缓存条数",
        "範囲ごとのキャッシュ件数",
    ),
    "news_cache_total_limit": ("Total cached items", "缓存总条数", "キャッシュ総件数"),
    "trash_retention_days": ("Trash retention (days)", "回收站保留天数", "ゴミ箱保持日数"),
    "data_vendors": ("Default source chains", "默认数据源顺序", "既定のデータソース順序"),
    "tool_vendors": ("Tool source overrides", "工具数据源覆盖", "ツール別ソース設定"),
    "data_vendors_by_market": ("Market source overrides", "市场数据源覆盖", "市場別ソース設定"),
    "providers": ("Model service connections", "模型服务连接", "モデルサービス接続"),
}

GROUP_DESCRIPTIONS = {
    "research": (
        "Applies to newly created research. A Run's explicit selection takes precedence.",
        "对新建研究生效；本次研究的显式选择优先。",
        "新規リサーチに適用。各リサーチの明示的な指定が優先されます。",
    ),
    "news": (
        "Controls requested coverage or output, not historical completeness. Source limits still apply.",
        "控制请求范围或输出量，不代表历史覆盖完整；仍受数据源限制。",
        "要求範囲・出力を制御します。過去データの網羅性は保証せず、取得元の制限が適用されます。",
    ),
    "cache": (
        "Changes apply to new research; the cache retains only previously observed material.",
        "对新建研究生效；缓存只保留曾经观察到的资料。",
        "新規リサーチに適用。キャッシュは取得済み資料のみを保持します。",
    ),
    "sources": (
        "Ordered fallback: tool override, then market override, then default category. Only selected sources are used.",
        "按工具覆盖、市场覆盖、默认类别解析；仅使用选中的来源，按顺序回退。",
        "ツール、市場、既定カテゴリの優先順で解決し、選択したソースのみを順に使用します。",
    ),
    "compatibility": (
        "Used when role-specific reasoning is unset. provider_default explicitly omits the native parameter.",
        "仅在角色推理强度未设置时使用；provider_default 明确省略服务原生参数。",
        "役割別推論が未設定の場合に使用。provider_default はネイティブパラメータを明示的に省略します。",
    ),
    "providers": (
        "One connection per provider. Keys are managed separately and read at execution start.",
        "每个服务一套连接；凭据单独管理，在执行开始时读取。",
        "プロバイダーごとに一つの接続。認証情報は別管理し、実行開始時に読み込みます。",
    ),
}


def translated(values):
    return dict(zip(("en", "zh-CN", "ja"), values, strict=True))


def configuration_schema() -> ConfigurationSchema:
    from tradingagents.dataflows.interface import TOOLS_CATEGORIES, VENDOR_METHODS
    from tradingagents.default_config import _ENV_OVERRIDES
    from tradingagents.llm_clients.provider_registry import PROVIDER_REGISTRY
    from tradingagents.llm_clients.reasoning_effort import provider_effort_levels

    defaults = ConfigurationValues().model_dump()
    properties = ConfigurationValues.model_json_schema()["properties"]
    fields = []
    for key, default in defaults.items():
        group = "research"
        if key.startswith("news_cache") or key == "trash_retention_days":
            group = "cache"
        elif key in {"data_vendors", "tool_vendors", "data_vendors_by_market"}:
            group = "sources"
        elif "news" in key or key in {"sentiment_filing_limit", "social_lookback_days"}:
            group = "news"
        elif key in {"google_thinking_level", "openai_reasoning_effort", "anthropic_effort"}:
            group = "compatibility"
        elif key == "providers":
            group = "providers"
        prop = properties[key]
        nullable = any(item.get("type") == "null" for item in prop.get("anyOf", []))
        scalar = next((item for item in prop.get("anyOf", []) if item.get("type") != "null"), prop)
        kind = {"integer": "number", "number": "number", "boolean": "boolean", "array": "list"}.get(
            scalar.get("type"), "text"
        )
        options = prop.get("enum", [])
        if key == "llm_provider":
            options = list(PROVIDER_REGISTRY)
        elif key == "analysts":
            options = ["market", "social", "news", "fundamentals"]
        elif key in {
            "quick_reasoning_effort",
            "deep_reasoning_effort",
            "openai_reasoning_effort",
            "google_thinking_level",
            "anthropic_effort",
        }:
            providers = {
                "openai_reasoning_effort": "openai",
                "google_thinking_level": "google",
                "anthropic_effort": "anthropic",
            }
            levels = (
                provider_effort_levels(providers[key])
                if key in providers
                else ("none", "minimal", "low", "medium", "high", "xhigh", "max")
            )
            options = ["provider_default", *levels]
        if options and kind != "list":
            kind = "choice"
        if group == "sources":
            kind = "routes"
        if key == "providers":
            kind = "providers"
        description = GROUP_DESCRIPTIONS[group]
        if key.endswith("lookback_days"):
            description = (
                "Calendar offset before the cutoff, inclusive at both ends: 14 means 15 dates. Does not guarantee available coverage.",
                "截止日前的日历偏移，含首尾：14 表示 15 个日期；不保证来源具备该范围。",
                "基準日からの暦日オフセット。両端を含み、14 は 15 日付を意味します。取得範囲は保証されません。",
            )
        elif key == "trash_retention_days":
            description = (
                "0 disables automatic deletion. A shorter period affects existing trash at the next cleanup.",
                "0 关闭自动清理；缩短期限会在下次清理时作用于已有回收站记录。",
                "0 は自動削除を無効化。短縮すると次回の清掃時に既存のゴミ箱にも適用されます。",
            )
        elif key == "temperature":
            description = (
                "Unset uses the provider default. Some reasoning models ignore temperature.",
                "留空使用服务默认值；部分推理模型会忽略温度。",
                "未設定ではプロバイダーの既定値。一部の推論モデルは温度を無視します。",
            )
        env_names = [env for env, target in _ENV_OVERRIDES.items() if target == key]
        if key == "trash_retention_days":
            env_names.append("TRADINGAGENTS_TRASH_RETENTION_DAYS")
        if key == "providers":
            env_names.extend(
                [
                    "TRADINGAGENTS_LLM_BACKEND_URL",
                    "AZURE_OPENAI_ENDPOINT",
                    "AZURE_OPENAI_DEPLOYMENT_NAME",
                    "OPENAI_API_VERSION",
                    "OLLAMA_BASE_URL",
                    "AWS_REGION",
                    "AWS_DEFAULT_REGION",
                    "AWS_PROFILE",
                ]
            )
        fields.append(
            ConfigurationField(
                key=key,
                group=group,
                label=translated(LABELS[key]),
                description=translated(description),
                kind=kind,
                default=default,
                nullable=nullable,
                minimum=scalar.get("minimum"),
                maximum=scalar.get("maximum"),
                options=options,
                env_names=env_names,
            )
        )
    return ConfigurationSchema(
        fields=fields,
        providers={key: value.label for key, value in PROVIDER_REGISTRY.items()},
        provider_defaults={
            key: effective_connection(ConfigurationValues(), key) for key in PROVIDER_REGISTRY
        },
        credential_owners=credential_owners(),
        route_options={
            key: sorted(
                {vendor for tool in data["tools"] for vendor in VENDOR_METHODS.get(tool, {})}
            )
            for key, data in TOOLS_CATEGORIES.items()
        },
        tool_options={key: sorted(value) for key, value in VENDOR_METHODS.items()},
    )
