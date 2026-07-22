# TradingAgentsX

<div align="center">

**English** · [简体中文](docs/i18n/README.zh-CN.md) ·
[日本語](docs/i18n/README.ja.md)

</div>

<div align="center" style="line-height: 1;">
  <a href="https://arxiv.org/abs/2412.20138" target="_blank"><img alt="arXiv" src="https://img.shields.io/badge/arXiv-2412.20138-B31B1B?logo=arxiv"/></a>
  <img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg"/>
</div>

A multi-agent LLM trading framework with first-class dataflows for US-listed
securities, Japanese equities (`.T`), and Shanghai/Shenzhen A-shares
(`.SS`/`.SZ`). Market-specific sources feed one common agent graph with explicit
analysis-date boundaries, source provenance, and auditable fallbacks.

> **Fork notice.** This is an independently maintained fork of
> [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
> (Apache-2.0). It does not track upstream releases one-to-one. Upstream
> attribution and licensing are retained in [LICENSE](LICENSE) and
> [NOTICE](NOTICE).

<div align="center">

[Overview](#overview) · [Markets](#market-support) ·
[Installation](#installation) · [CLI](#cli-usage) ·
[Japan](#japanese-market) · [China](#china-a-shares) ·
[Python API](#python-api) · [Development](#development) ·
[Architecture](docs/architecture.md) · [Changelog](CHANGELOG.md)

</div>

## Overview

TradingAgentsX models a small investment team: four analysts prepare market,
fundamental, news, and sentiment evidence; bull and bear researchers debate it;
a trader proposes a position; and a risk team and portfolio manager produce the
final decision.

```text
analysts → bull/bear debate → research manager → trader
         → risk debate → portfolio manager → reflection
```

<p align="center">
  <img src="assets/schema.png" style="width: 100%; height: auto;">
</p>

The framework supports interactive CLI runs and direct Python use. Analysts can
be selected independently, LLM providers are configurable, completed decisions
can feed a cross-run reflection log, and optional LangGraph checkpoints can
resume interrupted analyses.

> TradingAgentsX is a research framework. Its output is not financial,
> investment, or trading advice. Results depend on model behavior, data quality,
> timing, and configuration.

## Market support

Internally, instruments use Yahoo-compatible canonical symbols. Supported
aliases are normalized before market routing, so `600519` becomes
`600519.SS`, `000001` becomes `000001.SZ`, and `600519.SH` becomes
`600519.SS`. Unsupported or ambiguous six-digit mainland securities fail
loudly instead of falling through to the US route.

| Market | Example | Dataflow |
| --- | --- | --- |
| US/default | `NVDA`, `SPY` | yfinance-based default route |
| Japan | `7203.T` | J-Quants and Japanese disclosure sources, with configured fallbacks |
| China A-shares | `600519.SS`, `000001.SZ` | Tencent/AkShare plus Chinese fundamentals, news, and macro sources |
| Other Yahoo markets | `0700.HK`, `AZN.L`, `RELIANCE.NS` | Default Yahoo-compatible behavior; no dedicated local-market dataflow |
| Crypto/FX | `BTC-USD`, `EURUSD=X` | Yahoo-compatible default route and supported aliases |

The dedicated China scope currently covers Shanghai and Shenzhen individual
A-share equities. Beijing `.BJ`, Hong Kong `.HK`, ETFs, funds, options, and
intraday/high-frequency China data are outside this phase.

### Default source routing

Arrows denote ordered fallback; sources in parentheses are assembled together.

| Market | Prices and indicators | Fundamentals and statements | Ticker news |
| --- | --- | --- | --- |
| US/default | yfinance | yfinance | yfinance |
| Japan `.T` | J-Quants → yfinance | JP assemblers → J-Quants → yfinance | (EDINET + TDnet + Google News) → yfinance |
| China `.SS`/`.SZ` | Tencent qfq → Eastmoney qfq → yfinance | (CNINFO + Sina) → yfinance | (CNINFO + Eastmoney Research + Google News) → yfinance |

The global news analyst also receives a cross-region macro panel. US/global
cells use FRED; Japanese cells use BOJ, e-Stat, Ministry of Finance, and FRED;
Chinese cells use NBS, Eastmoney, SAFE, and a deliberately limited ChinaMoney
fallback. A missing source disables only the cells it owns.

See [docs/architecture.md](docs/architecture.md) for routing, caching,
point-in-time, and failure contracts.

### Data integrity and provenance

- Graph-facing tools receive the analysis date from workflow state. Historical
  runs do not silently inject current snapshots from live-only sources.
- Vendor chains are explicit. The router never adds an unconfigured fallback,
  and assemblers own intentional multi-source composition.
- Results retain requested and effective dates, actual sources, and timing or
  fallback status as structured provenance.
- Material fallback, stale data, missing or partial coverage, truncation, and
  non-PIT/non-vintage limitations appear under `Data Quality Warnings`.
  Successful empty news windows do not generate a warning.
- Set `provenance_appendix = True` or
  `TRADINGAGENTS_PROVENANCE_APPENDIX=true` to add the detailed English
  `Data Provenance` table. Important warnings remain visible when it is off.

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/qingchiang/trading-agents-x.git
cd trading-agents-x

python -m venv .venv
source .venv/bin/activate
pip install .
```

For development, install the `dev` extra:

```bash
pip install -e ".[dev]"
```

### Docker

```bash
cp .env.example .env  # add the keys you use
docker compose run --rm tradingagents
```

For the bundled Ollama service:

```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

## Configuration

Copy the environment template and configure one LLM provider:

```bash
cp .env.example .env
```

Native clients are available for OpenAI, Anthropic, Google, Azure OpenAI, and
Amazon Bedrock. The OpenAI-compatible registry covers xAI, DeepSeek, Qwen, GLM,
MiniMax, OpenRouter, Mistral, Kimi, Groq, NVIDIA NIM, Ollama, and arbitrary
compatible endpoints such as vLLM or LM Studio. See [.env.example](.env.example)
for provider-specific variables.

Amazon Bedrock requires `pip install ".[bedrock]"`. Azure users can start from
`.env.enterprise.example`. Ollama defaults to `http://localhost:11434/v1` and
can be redirected with `OLLAMA_BASE_URL`.

Common examples:

```dotenv
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
DEEPSEEK_API_KEY=...
OPENROUTER_API_KEY=...
```

For an arbitrary compatible endpoint, use `llm_provider =
"openai_compatible"`, set `backend_url` (or
`TRADINGAGENTS_LLM_BACKEND_URL`), and provide
`OPENAI_COMPATIBLE_API_KEY` only when the endpoint requires one.

### Optional market-data keys

China's initial dataflow is keyless. Japanese sources degrade independently, so
none of the following keys is mandatory:

```dotenv
JQUANTS_API_KEY=...  # prices, summaries, and plan-dependent positioning data
EDINET_API_KEY=...   # statutory filings, holdings, and tender offers
ESTAT_APP_ID=...     # Japanese CPI series
FRED_API_KEY=...     # US/global macro and selected Japanese fallback series
```

## CLI usage

```bash
tradingagents
# or
python -m cli.main
```

The CLI lets you select a ticker, analysis date, analysts, research depth, and
LLM provider. The same canonical symbol and market route are used by the CLI and
the Python graph.

<p align="center">
  <img src="assets/cli/cli_init.png" width="100%" style="display: inline-block;">
</p>

## Japanese market

Tokyo tickers use local sources across all four analysts instead of relying on
Yahoo's thinner English-language coverage.

| Area | Primary evidence |
| --- | --- |
| Market/technicals | J-Quants v2 adjusted daily bars and verified snapshots |
| Fundamentals | J-Quants summaries, disclosure-safe ratios, TOPIX-weekly beta, curated near-live statement detail |
| News | EDINET filings, TDnet timely disclosures, Google News Japan, and labelled exchange-section context |
| Sentiment | Per-stock margin/short data, EDINET large-shareholding and tender-offer filings, live-only ratings |
| Macro | BOJ policy/Tankan, e-Stat CPI, MOF daily JGB yields, and FRED fallbacks |

J-Quants Light covers prices, summaries, and exchange-section flows. Standard
also unlocks per-ticker margin and short-position signals; Premium is not
required. Without a J-Quants key, prices and near-live fundamentals can fall
back to yfinance. Historical statements fail closed when the fallback lacks
filing timestamps. Without EDINET, other news sources continue independently.

Japanese historical paths enforce publication dates where available. The
current-day EDINET list stays short-lived, settled filing lists use bounded disk
caches, and Ministry of Finance yield data observes its next-business-day
09:30 JST publication boundary before entering an analysis.

## China A-shares

The current China scope targets low-frequency analysis of Shanghai and Shenzhen
individual stocks.

| Area | Primary evidence |
| --- | --- |
| Market/technicals | Tencent forward-adjusted (`qfq`) OHLCV; Eastmoney then yfinance fallback |
| Fundamentals | CNINFO company profile plus disclosure-filtered Sina abstracts and statements |
| News | Exact-code CNINFO announcements, Eastmoney research, and Chinese Google News |
| Sentiment | SSE/SZSE margin data, holding changes, ratings/targets, and important announcements |
| Macro | 1Y LPR, China 10Y, CPI, GDP, unemployment, manufacturing PMI, and USD/CNY parity |

Prices, verified snapshots, and indicators share one qfq history. The normal
technical warm-up fits in one bounded Tencent request; longer histories paginate
only when needed. A fallback replaces the whole requested window because
adjustment factors can differ between providers.

Company evidence is assembled per source and keeps separate provenance.
Historical data without defensible visibility metadata fails closed or is
labelled non-PIT. Low-frequency CNINFO and Eastmoney candidates share a bounded,
same-cutoff cache so News and Sentiment can reuse results without crossing an
analysis-date boundary.

China macro timing varies by source. NBS releases preserve both publication date
and observation period; GDP is cumulative year-to-date YoY. CPI, GDP, and PMI
fall back to observation-period-filtered Eastmoney series only when an eligible
recent NBS release is unavailable, and that fallback is explicitly non-vintage.
SAFE is primary for USD/CNY central parity. ChinaMoney is limited to a latest
curve snapshot fallback and is not expanded into a historical crawler.

AkShare and the keyless China assemblers depend on public web endpoints. Their
schemas, pagination, and anti-bot behavior can change without notice. Production
users should monitor effective dates, actual sources, and quality warnings rather
than treating HTTP success as proof of freshness.

## Python API

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "openai"
config["quick_think_llm"] = "gpt-5.4-mini"
config["deep_think_llm"] = "gpt-5.5"
config["quick_reasoning_effort"] = "low"
config["deep_reasoning_effort"] = "high"

graph = TradingAgentsGraph(debug=True, config=config)
final_state, decision = graph.propagate("600519", "2026-07-17")
print(decision)
```

`propagate()` returns `(final_state, decision)`. See
`tradingagents/default_config.py` for all configuration options.

### Reasoning effort by role

`quick_reasoning_effort` and `deep_reasoning_effort` configure the two model
roles independently. Their environment equivalents are:

```dotenv
TRADINGAGENTS_QUICK_REASONING_EFFORT=low
TRADINGAGENTS_DEEP_REASONING_EFFORT=high
```

Role-specific values take precedence over legacy provider-wide settings. Use
`provider_default` to omit the native SDK parameter and block legacy fallback.
Supported levels remain provider- and model-specific; the CLI catalog is the
source of truth for curated choices.

## Persistence and recovery

Completed runs append decisions to
`~/.tradingagents/memory/trading_memory.md`. A later run for the same ticker can
compare realized raw and benchmark-relative returns and inject a short reflection
into the portfolio-manager context. Override the path with
`TRADINGAGENTS_MEMORY_LOG_PATH`.

Checkpoint resume is opt-in:

```bash
tradingagents analyze --checkpoint
tradingagents analyze --clear-checkpoints
```

Per-ticker SQLite checkpoints live under
`~/.tradingagents/cache/checkpoints/`; override the base with
`TRADINGAGENTS_CACHE_DIR`. Successful runs clear their checkpoints.

## Reproducibility

LLM sampling and live data make byte-identical reruns unlikely. Historical runs
reduce one major source of drift by excluding live-only social, identity, and
statement snapshots. Exact company identity, canonical symbols, verified market
snapshots, source provenance, and date cutoffs are deterministic for a given
retrieved payload.

Lowering `temperature` can help only for models that honor it; reasoning models
often do not. Treat the framework as a research scaffold rather than a strategy
with a fixed, reproducible return.

## Development

The default suite disables project dotenv loading, substitutes placeholder
credentials, and skips all live network contracts.

```bash
PYTHON_DOTENV_DISABLED=1 uv run --extra dev pytest -q
PYTHON_DOTENV_DISABLED=1 uv run --extra dev ruff check .
```

Cross-market live-data contracts are opt-in and serial:

```bash
RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
  uv run --extra dev pytest -q -m live_data
```

The live suite validates schemas, completed-date cutoffs, broad value ranges,
actual sources, and audited fallbacks without pinning exact prices or row counts.
Default pytest and CI collect but skip it.

The DeepSeek wire-level integration test is separately opt-in:

```bash
RUN_LIVE_LLM_TESTS=1 DEEPSEEK_API_KEY=... \
  uv run --extra dev pytest -q tests/test_deepseek_reasoning.py -m integration
```

Contributions are welcome. See [AGENTS.md](AGENTS.md) for shared development
rules, [docs/architecture.md](docs/architecture.md) for durable design
contracts, and [CHANGELOG.md](CHANGELOG.md) for release history.

## Citation

Please cite the original TradingAgents paper when this framework supports your
work:

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138},
}
```
