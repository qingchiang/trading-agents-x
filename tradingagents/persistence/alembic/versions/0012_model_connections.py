"""Persist model connections independently of vendor names.

Preset data is frozen here so future application changes cannot alter migration.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0012_model_connections"
down_revision = "0011_application_configuration"
branch_labels = None
depends_on = None

PRESETS = {
    "anthropic": {
        "compatibility": "anthropic",
        "deleted": False,
        "discovery": "anthropic",
        "enabled": True,
        "id": "5b71fe60-aace-559f-9aa9-3493ee3f7e18",
        "key_required": True,
        "name": "Anthropic",
        "preset": "anthropic",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "anthropic",
            "discovery": "anthropic",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.anthropic.com", "kind": "anthropic"},
        },
        "transport": {"base_url": "https://api.anthropic.com", "kind": "anthropic"},
    },
    "azure": {
        "compatibility": "azure",
        "deleted": False,
        "discovery": "custom",
        "enabled": True,
        "id": "61c003fa-6fdb-5294-8dbf-6476e7aaa4fa",
        "key_required": True,
        "name": "Azure OpenAI",
        "preset": "azure",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "azure",
            "discovery": "custom",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {
                "api_version": None,
                "base_url": None,
                "deployment": None,
                "kind": "azure",
            },
        },
        "transport": {"api_version": None, "base_url": None, "deployment": None, "kind": "azure"},
    },
    "bedrock": {
        "compatibility": "bedrock",
        "deleted": False,
        "discovery": "bedrock",
        "enabled": True,
        "id": "32dd0801-9e5c-50fb-ace9-732b439a581e",
        "key_required": False,
        "name": "Amazon Bedrock",
        "preset": "bedrock",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "bedrock",
            "discovery": "bedrock",
            "key_required": False,
            "reasoning_defaults": {},
            "transport": {
                "auth_mode": "system",
                "aws_profile": None,
                "kind": "bedrock",
                "region": "us-west-2",
            },
        },
        "transport": {
            "auth_mode": "system",
            "aws_profile": None,
            "kind": "bedrock",
            "region": "us-west-2",
        },
    },
    "deepseek": {
        "compatibility": "deepseek",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "835c21bf-9cdc-5609-9d61-f52a7169a5de",
        "key_required": True,
        "name": "DeepSeek",
        "preset": "deepseek",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "deepseek",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.deepseek.com", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.deepseek.com", "kind": "chat_completions"},
    },
    "glm": {
        "compatibility": "glm",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "724b3de2-54ed-5a62-8c8a-d8d070e2c4d2",
        "key_required": True,
        "name": "Z.AI GLM",
        "preset": "glm",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "glm",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.z.ai/api/paas/v4", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.z.ai/api/paas/v4", "kind": "chat_completions"},
    },
    "glm-cn": {
        "compatibility": "glm-cn",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "0cf8a22c-4ebe-5111-87c4-44f109d31e6c",
        "key_required": True,
        "name": "BigModel GLM (China)",
        "preset": "glm-cn",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "glm-cn",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "kind": "chat_completions",
            },
        },
        "transport": {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "kind": "chat_completions",
        },
    },
    "google": {
        "compatibility": "google",
        "deleted": False,
        "discovery": "google",
        "enabled": True,
        "id": "961e5e9a-9481-54b0-b086-c302b7f01397",
        "key_required": True,
        "name": "Google Gemini",
        "preset": "google",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "google",
            "discovery": "google",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {
                "base_url": "https://generativelanguage.googleapis.com",
                "kind": "google",
            },
        },
        "transport": {"base_url": "https://generativelanguage.googleapis.com", "kind": "google"},
    },
    "groq": {
        "compatibility": "groq",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "16fe6d47-fece-51aa-be34-c236ec9da1e3",
        "key_required": True,
        "name": "Groq",
        "preset": "groq",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "groq",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.groq.com/openai/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.groq.com/openai/v1", "kind": "chat_completions"},
    },
    "kimi": {
        "compatibility": "kimi",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "4e9ed1e7-de26-5db3-acd9-742a983ea3ec",
        "key_required": True,
        "name": "Kimi / Moonshot",
        "preset": "kimi",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "kimi",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.moonshot.ai/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.moonshot.ai/v1", "kind": "chat_completions"},
    },
    "minimax": {
        "compatibility": "minimax",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "25e93d8f-fb13-515c-aff5-4e81206786c9",
        "key_required": True,
        "name": "MiniMax (International)",
        "preset": "minimax",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "minimax",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.minimax.io/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.minimax.io/v1", "kind": "chat_completions"},
    },
    "minimax-cn": {
        "compatibility": "minimax-cn",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "4bd876b8-ded9-50ad-80a3-fbd4752c8941",
        "key_required": True,
        "name": "MiniMax (China)",
        "preset": "minimax-cn",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "minimax-cn",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.minimaxi.com/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.minimaxi.com/v1", "kind": "chat_completions"},
    },
    "mistral": {
        "compatibility": "mistral",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "97d11dea-af22-521a-8500-4375d2b770af",
        "key_required": True,
        "name": "Mistral",
        "preset": "mistral",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "mistral",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.mistral.ai/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.mistral.ai/v1", "kind": "chat_completions"},
    },
    "nvidia": {
        "compatibility": "nvidia",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "ed7aa4e1-f7ca-5836-8105-0a4562efc913",
        "key_required": True,
        "name": "NVIDIA NIM",
        "preset": "nvidia",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "nvidia",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "kind": "chat_completions",
            },
        },
        "transport": {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "kind": "chat_completions",
        },
    },
    "ollama": {
        "compatibility": "ollama",
        "deleted": False,
        "discovery": "ollama",
        "enabled": True,
        "id": "70ca418e-2c0e-552f-9393-bf96f4374006",
        "key_required": False,
        "name": "Ollama",
        "preset": "ollama",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "ollama",
            "discovery": "ollama",
            "key_required": False,
            "reasoning_defaults": {},
            "transport": {"base_url": "http://localhost:11434/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "http://localhost:11434/v1", "kind": "chat_completions"},
    },
    "openai": {
        "compatibility": "openai",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "200e8466-bf15-5c6f-a474-77dcc33f7f7a",
        "key_required": True,
        "name": "OpenAI",
        "preset": "openai",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "openai",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.openai.com/v1", "kind": "responses"},
        },
        "transport": {"base_url": "https://api.openai.com/v1", "kind": "responses"},
    },
    "openai_compatible": {
        "compatibility": "openai_compatible",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "0b85bee6-c046-51d0-bd77-0b15bc5bcd8a",
        "key_required": False,
        "name": "OpenAI-compatible",
        "preset": "openai_compatible",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "openai_compatible",
            "discovery": "openai_compatible",
            "key_required": False,
            "reasoning_defaults": {},
            "transport": {"base_url": None, "kind": "chat_completions"},
        },
        "transport": {"base_url": None, "kind": "chat_completions"},
    },
    "openrouter": {
        "compatibility": "openrouter",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "a90773dc-e40c-5d36-b0cc-c3876f27a8a5",
        "key_required": True,
        "name": "OpenRouter",
        "preset": "openrouter",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "openrouter",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://openrouter.ai/api/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://openrouter.ai/api/v1", "kind": "chat_completions"},
    },
    "qwen": {
        "compatibility": "qwen",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "639030c8-96d7-5be2-8d6c-ccf20d79505c",
        "key_required": True,
        "name": "Qwen (International)",
        "preset": "qwen",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "qwen",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {
                "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                "kind": "chat_completions",
            },
        },
        "transport": {
            "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "kind": "chat_completions",
        },
    },
    "qwen-cn": {
        "compatibility": "qwen-cn",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "4eab9ca5-f302-52e5-99d3-b302cfe83cf3",
        "key_required": True,
        "name": "Qwen (China)",
        "preset": "qwen-cn",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "qwen-cn",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "kind": "chat_completions",
            },
        },
        "transport": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "kind": "chat_completions",
        },
    },
    "xai": {
        "compatibility": "xai",
        "deleted": False,
        "discovery": "openai_compatible",
        "enabled": True,
        "id": "326d06e3-d782-54b1-9578-983e629484ad",
        "key_required": True,
        "name": "xAI",
        "preset": "xai",
        "reasoning_defaults": {},
        "revision": 1,
        "template": {
            "compatibility": "xai",
            "discovery": "openai_compatible",
            "key_required": True,
            "reasoning_defaults": {},
            "transport": {"base_url": "https://api.x.ai/v1", "kind": "chat_completions"},
        },
        "transport": {"base_url": "https://api.x.ai/v1", "kind": "chat_completions"},
    },
}
ALIASES = {
    "ANTHROPIC_API_KEY": ("anthropic", "api_key"),
    "AWS_ACCESS_KEY_ID": ("bedrock", "access_key_id"),
    "AWS_BEARER_TOKEN_BEDROCK": ("bedrock", "bearer_token"),
    "AWS_SECRET_ACCESS_KEY": ("bedrock", "secret_access_key"),
    "AWS_SESSION_TOKEN": ("bedrock", "session_token"),
    "AZURE_OPENAI_API_KEY": ("azure", "api_key"),
    "DASHSCOPE_API_KEY": ("qwen", "api_key"),
    "DASHSCOPE_CN_API_KEY": ("qwen-cn", "api_key"),
    "DEEPSEEK_API_KEY": ("deepseek", "api_key"),
    "GOOGLE_API_KEY": ("google", "api_key"),
    "GROQ_API_KEY": ("groq", "api_key"),
    "MINIMAX_API_KEY": ("minimax", "api_key"),
    "MINIMAX_CN_API_KEY": ("minimax-cn", "api_key"),
    "MISTRAL_API_KEY": ("mistral", "api_key"),
    "MOONSHOT_API_KEY": ("kimi", "api_key"),
    "NVIDIA_API_KEY": ("nvidia", "api_key"),
    "OPENAI_API_KEY": ("openai", "api_key"),
    "OPENAI_COMPATIBLE_API_KEY": ("openai_compatible", "api_key"),
    "OPENROUTER_API_KEY": ("openrouter", "api_key"),
    "XAI_API_KEY": ("xai", "api_key"),
    "ZHIPU_API_KEY": ("glm", "api_key"),
    "ZHIPU_CN_API_KEY": ("glm-cn", "api_key"),
}


def upgrade():
    op.create_table(
        "model_connections",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("legacy_provider", sa.String(80), unique=True),
    )
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT values_json FROM application_configuration WHERE id=1")
    ).first()
    raw = json.loads(row[0]) if row else {}
    secrets = dict(bind.execute(sa.text("SELECT name, value FROM configuration_credentials")).all())
    owners = {raw.get("llm_provider", "openai"), *raw.get("providers", {})}
    owners.update(ALIASES[name][0] for name in secrets if name in ALIASES)
    for (retained,) in bind.execute(sa.text("SELECT config_json FROM runs")):
        provider = json.loads(retained).get("llm_provider")
        if provider in PRESETS:
            owners.add(provider)
    for provider in owners:
        if provider not in PRESETS:
            continue
        conn = json.loads(json.dumps(PRESETS[provider]))
        override = raw.get("providers", {}).get(provider, {})
        for key in conn["transport"]:
            if key in override and override[key] is not None:
                conn["transport"][key] = override[key]
        if (
            provider == "openai"
            and conn["transport"].get("base_url") != "https://api.openai.com/v1"
        ):
            conn["transport"]["kind"] = "chat_completions"
        conn["reasoning_defaults"] = {
            key: raw[key]
            for key in ("openai_reasoning_effort", "google_thinking_level", "anthropic_effort")
            if key in raw
        }
        bind.execute(
            sa.text("INSERT INTO model_connections VALUES (:id, :definition, :provider)"),
            {"id": conn["id"], "definition": json.dumps(conn), "provider": provider},
        )
    for name, value in secrets.items():
        if name in ALIASES:
            provider, field = ALIASES[name]
            target = "connection:" + PRESETS[provider]["id"] + ":" + field
            bind.execute(
                sa.text("INSERT INTO configuration_credentials VALUES (:name, :value)"),
                {"name": target, "value": value},
            )
            bind.execute(
                sa.text("DELETE FROM configuration_credentials WHERE name=:name"), {"name": name}
            )
    if row:
        identity = PRESETS[raw.get("llm_provider", "openai")]["id"]
        raw.update(quick_connection_id=identity, deep_connection_id=identity)
        bind.execute(
            sa.text("UPDATE application_configuration SET values_json=:raw WHERE id=1"),
            {"raw": json.dumps(raw)},
        )


def downgrade():
    bind = op.get_bind()
    records = bind.execute(
        sa.text("SELECT id, definition, legacy_provider FROM model_connections")
    ).all()
    if any(provider is None for _, _, provider in records):
        raise RuntimeError("Custom connections cannot be downgraded; restore a backup")
    reverse = {
        (PRESETS[provider]["id"], field): name for name, (provider, field) in ALIASES.items()
    }
    for name, value in bind.execute(
        sa.text("SELECT name, value FROM configuration_credentials")
    ).all():
        if name.startswith("connection:"):
            _, identity, field = name.split(":", 2)
            alias = reverse.get((identity, field))
            if alias:
                bind.execute(
                    sa.text(
                        "INSERT OR REPLACE INTO configuration_credentials VALUES (:name, :value)"
                    ),
                    {"name": alias, "value": value},
                )
            bind.execute(
                sa.text("DELETE FROM configuration_credentials WHERE name=:name"), {"name": name}
            )
    row = bind.execute(
        sa.text("SELECT values_json FROM application_configuration WHERE id=1")
    ).first()
    if row:
        raw = json.loads(row[0])
        raw.pop("quick_connection_id", None)
        raw.pop("deep_connection_id", None)
        bind.execute(
            sa.text("UPDATE application_configuration SET values_json=:raw WHERE id=1"),
            {"raw": json.dumps(raw)},
        )
    op.drop_table("model_connections")
