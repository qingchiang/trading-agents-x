# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"   # install with dev extras (pytest + ruff)

pytest -q                                 # full test suite
pytest tests/test_market_routing.py       # one file
pytest tests/test_x.py::Cls::test_y       # one test
ruff check .                              # lint (CI runs this repo-wide, strict)

python main.py        # scripted single-ticker run (edit ticker/date in main.py)
tradingagents         # interactive CLI (also: python -m cli.main)
```

CI (`.github/workflows/ci.yml`) runs three jobs on push/PR: `test` (pytest on Python 3.10–3.13), `smoke-install` (bare `pip install .` then import — catches undeclared runtime deps), and `lint` (`ruff check .`). Pytest markers: `unit`, `integration`, `smoke`.

`pyproject.toml` uses PEP 621 extras. **Declare runtime deps in `[project.dependencies]`** — the `smoke-install` job bare-imports the package, so an undeclared runtime import fails CI — and dev/test tools in `[project.optional-dependencies].dev`. **Ship non-code data files (e.g. JSON snapshots) via `[tool.setuptools.package-data]`** and load them with `importlib.resources`, not a relative path — an editable install sees the file on disk but a real `pip install .` only bundles registered package-data, and `smoke-install` won't catch the gap because it imports without reading the file.

## Architecture

**Entry point.** `TradingAgentsGraph` (`tradingagents/graph/trading_graph.py`) is the public API; `.propagate(ticker, date)` returns `(final_state, decision)`. Tickers are Yahoo-style exchange-suffixed (`AAPL`, `7203.T`, `0700.HK`); company identity and the alpha benchmark resolve automatically per market via `benchmark_map` in `default_config.py`.

**Agent graph (LangGraph).** Built in `graph/setup.py`, transitions in `graph/conditional_logic.py`, initial state in `graph/propagation.py`, optional resume via `graph/checkpointer.py`. The pipeline mirrors a trading firm: **analysts** (`agents/analysts/`: market, news, sentiment+social, fundamentals) → **researchers** bull/bear debate (`agents/researchers/`) → **research_manager** → **trader** (`agents/trader/`) → **risk debate** aggressive/conservative/neutral (`agents/risk_mgmt/`) → **portfolio_manager** → **reflection** (`graph/reflection.py`, `signal_processing.py`). Analysts run as a configurable subset; the sentiment analyst is special — it does **not** use tool-calling, it prefetches its data sources directly before the LLM call.

**Data vendor layer (`tradingagents/dataflows/`).** This is the most important subsystem to understand before touching data code. Analyst tools (`agents/utils/*_tools.py`, `@tool`-wrapped) call `route_to_vendor(method, *args)` in `interface.py`, which routes by config:
- `TOOLS_CATEGORIES` groups tools into categories (`core_stock_apis`, `technical_indicators`, `fundamental_data`, `news_data`, `macro_data`, `prediction_markets`).
- `VENDOR_METHODS` maps each method → `{vendor_name: impl_func}`. A method is only servable by a vendor that appears here for it.
- `get_vendor(category, method, market)` resolves the vendor chain: `tool_vendors[method]` → `data_vendors_by_market[market][category]` (suffix routing, see below) → `data_vendors[category]`.
- The configured vendor string **is** the chain (comma-separated for ordered fallback). The router does **NOT** silently fall back to unconfigured vendors (regressions #988/#289). Errors use a typed taxonomy in `errors.py`: vendors raise `VendorNotConfiguredError` (missing key), `VendorRateLimitError`, or `NoMarketDataError` (empty/stale); the router tries the next vendor on each, surfaces `NO_DATA_AVAILABLE` when all report no data, and degrades `OPTIONAL_CATEGORIES` (macro, prediction markets) to a sentinel instead of crashing the run. **When adding a vendor**: write impl functions matching the existing return shapes (stock=CSV str, fundamentals=JSON or formatted str, indicators/macro=markdown str), register them in `VENDOR_METHODS`, and raise the typed errors — do not invent new fallback logic.

**Suffix-based market routing (`dataflows/market_context.py`).** A ticker's exchange suffix (e.g. `.T`, `.SS`) can select a market-specific vendor chain via `data_vendors_by_market` (empty by default = behavior unchanged). Only **ticker-bearing** methods are routed: `infer_market` reads the market from their first positional arg. **Ticker-less** methods (`get_global_news`, `get_macro_indicators`, `get_prediction_markets` — listed in `TICKERLESS_METHODS`) are deliberately **market-agnostic** and always use the default chain — macro is cross-border, so there is no per-run market state and no ContextVar. Suffix detection is the shared `symbol_utils.match_exchange_suffix` (longest-match), also used by `_resolve_benchmark`, so only configured suffixes match and `BRK.B` is never misread. A consequence to keep in mind: routing `news_data` for a suffix sends per-ticker `get_news`/`get_insider_transactions` to that vendor while `get_global_news` stays global.

**Config system.** `default_config.py` is the single source of truth. `DEFAULT_CONFIG` is built through `_apply_env_overrides`, which maps `TRADINGAGENTS_*` env vars (table `_ENV_OVERRIDES`) onto config keys with type coercion. Runtime overrides go through `dataflows/config.py::set_config`, which **deep-merges dict-valued keys one level** (so a partial `data_vendors` update keeps sibling keys) but replaces scalars. Tests hard-reset via `config_module._config = deepcopy(DEFAULT_CONFIG)` because `set_config` merges.

**LLM clients (`tradingagents/llm_clients/`).** `factory.py` builds a provider client from config; providers (OpenAI, Anthropic, Google, Azure, Bedrock, and OpenAI-compatible servers for xAI/DeepSeek/Qwen/GLM/MiniMax/OpenRouter/Ollama) share `base_client.py`. `model_catalog.py` + `capabilities.py` describe known models and per-provider reasoning/thinking knobs; an unknown model warns but proceeds.
