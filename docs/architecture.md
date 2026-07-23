# Project Architecture

This document records durable subsystem boundaries and data-quality contracts.
Code and tests remain authoritative for exact schemas, limits, and retry values.

## Runtime flow

`TradingAgentsGraph` in `tradingagents/graph/trading_graph.py` is the public
Python API. `propagate(ticker, trade_date, asset_type="stock")` normalizes the
instrument and returns `(final_state, decision)`.

The interactive CLI also runs through `propagate()`, using its optional stream
callback to render full-state chunks. This keeps memory-log preparation and
commit, checkpoint resume and cleanup, instrument context, and graph callbacks
under one lifecycle. A resumed invocation submits no new graph input; its first
streamed value is the restored checkpoint snapshot.

```text
analysts → bull/bear debate → research manager → trader
         → risk debate → portfolio manager → reflection
```

The graph is assembled in `tradingagents/graph/setup.py`. Market, News,
Sentiment, and Fundamentals analysts are independently configurable. Sentiment
prefetches evidence instead of tool-calling; News prefetches the cross-region
macro panel.

### Decision memory

Successful graph runs append a pending decision to the shared Markdown memory
log. A later fresh run for the same ticker settles eligible pending decisions
with realized and benchmark-relative returns, then injects memory into the
portfolio-manager context. Pending entries are retained without a count or age
limit. Resolved entries have a configurable global cap (1,000 by default);
rotation removes the oldest resolved blocks in file order and never removes
pending blocks.

Context selection keeps up to five full resolved entries for the same ticker.
Cross-ticker context is reflection-only and defaults to the three most recent
resolved entries whose asset type and regional market both match. Regional
markets use the existing `market_timezone()` identity; crypto uses one
`CRYPTO` bucket. New entries persist this identity in an optional `META` line.
Legacy entries without metadata remain unchanged on disk and infer asset type
and market from their canonical ticker when read.

## Cross-cutting contracts

### Symbols and markets

`normalize_symbol` converts supported aliases to the Yahoo-compatible canonical
symbols used internally. It handles broker aliases, forex/crypto forms, bare
A-share codes, and `CODE.SH` → `CODE.SS` before vendor routing.

The initial China scope supports Shanghai and Shenzhen individual A-share
equities. Ambiguous six-digit securities and Beijing `.BJ` fail loudly. A
suffix in `benchmark_map` does not imply a dedicated vendor route: `.HK`, for
example, has benchmark/calendar-aware Yahoo behavior but no HK dataflow.

One longest-match suffix helper drives vendor routing and benchmark selection,
so `BRK.B` is not mistaken for an exchange suffix. `benchmark_ticker`, when set,
overrides the regional `benchmark_map`.

### Analysis date and point-in-time safety

Direct tools retain their public signatures. Graph-facing variants inject
`trade_date` from workflow state, hiding the cutoff from the LLM. All sources
must truncate observations to it; sources with disclosure/update timestamps use
the later visibility date conservatively.

Live-only snapshots are withheld from historical runs. A provider without
strict historical PIT support must fail closed or label the limitation. US
yfinance statements are period-end-filtered and marked non-PIT; historical JP
statements do not use current yfinance frames. Historical identity uses
exact-symbol search metadata rather than current yfinance `.info`.

### Routing, assemblers, and failures

Agent tools call `route_to_vendor` in `tradingagents/dataflows/interface.py`.
Configuration resolves in this order:

1. `tool_vendors[method]`
2. `data_vendors_by_market[suffix][category]`
3. `data_vendors[category]`

Comma-separated values are exact ordered fallback chains; the router never adds
an unconfigured vendor. `default` selects every implementation registered for
the method. The router is first-success: multi-source composition belongs in an
assembler, which owns fault isolation, deduplication, and final caps. JP/CN news
and market-specific fundamentals use this pattern.

Global news, macro, and prediction markets are ticker-less and always use the
default category route. No per-run market `ContextVar` exists.

Adapters normalize missing configuration, throttling, empty/stale results,
request failures, and schema failures. The router logs a failed leg and tries
the next configured vendor. Exhausted clean no-data results become
`NO_DATA_AVAILABLE`; optional macro/prediction retrieval errors degrade to an
unavailable sentinel. Market prefetchers must also never abort the graph.
Retries, timeouts, caches, and HTTP behavior remain local to each subsystem.

### Provenance and quality warnings

Vendor results carry structured provenance in versioned HTML comments. Analyst
nodes extract it from tool messages; provenance is never inferred from LLM
prose. Records identify evidence, actual source, requested/effective dates, and
timing or fallback status.

Material fallback, missing/partial coverage, truncation, stale data, and
non-PIT/non-vintage limitations always render under `Data Quality Warnings`.
Successful empty news windows do not warn. `provenance_appendix` controls only
the detailed `Data Provenance` table.

### Configuration

`tradingagents/default_config.py` defines defaults and the centralized
`TRADINGAGENTS_*` environment mapping. Runtime `set_config` calls replace
scalars but merge dict-valued top-level keys one level deep. Tests reset with a
deep copy of `DEFAULT_CONFIG` rather than another merge.

## Market dataflows

Parentheses denote sources composed inside an assembler; arrows are ordered
fallback. Alpha Vantage remains available only through explicit configuration.

| Market | Prices/indicators | Fundamentals/statements | Ticker news |
| --- | --- | --- | --- |
| US/default | yfinance | yfinance | yfinance |
| Japan `.T` | J-Quants → yfinance | JP method-specific assemblers → J-Quants → yfinance | JP assembler (EDINET + TDnet + Google News) → yfinance |
| China `.SS`/`.SZ` | AkShare adapter (Tencent qfq → Eastmoney qfq) → yfinance | CN assemblers (CNINFO + Sina) → yfinance | CN assembler (CNINFO + Eastmoney Research + Google News) → yfinance |

### US/default

Unsuffixed instruments use yfinance unless configured otherwise. StockTwits and
Reddit are near-live sentiment sources and are not queried for historical runs.
Generic yfinance statements carry explicit period-end-only/non-PIT labels.

### Japan

- J-Quants API v2 supplies adjusted OHLCV, indicators, summaries, TOPIX, and
  optional positioning signals; yfinance is the configured keyless fallback.
- `jp_fundamentals` computes disclosure-safe ratios and TOPIX-weekly beta.
  `jp_statements` permits curated yfinance detail only near-live.
- `jp_news` keeps EDINET filings, TDnet disclosures, and Google News together,
  then deduplicates under one article budget.
- Sentiment uses per-name J-Quants margin/short data, EDINET holdings/TOB
  filings, and live-only yfinance ratings. Exchange-section flows are News-level
  context, not ticker sentiment.
- EDINET date lists share bounded memory and gzip disk caches. Current-day data
  remains short-lived and is not persisted as settled history.

### China A-shares

- Prices, snapshots, and indicators share forward-adjusted (`qfq`) OHLCV.
  Tencent precedes Eastmoney; router-level yfinance fallback is flagged because
  the adjustment provider changed.
- `cn_fundamentals` combines a current-reference CNINFO profile with
  disclosure-filtered Sina abstracts; `cn_statements` serves the Sina statements.
  Financial/general mappings are separate. Current yfinance valuation is
  omitted historically; any needed yfinance statement supplement is labeled
  non-strict PIT.
- `cn_news` fetches exact-code CNINFO announcements, Eastmoney research, and
  Chinese Google News independently, then deduplicates under one cap. A bounded
  exact-cutoff cache lets News and Sentiment reuse low-frequency candidates.
- Sentiment covers SSE/SZSE margin data, Eastmoney holding changes with
  conditional CNINFO fallback, Sina ratings with Eastmoney Research fallback,
  and important CNINFO announcements. Missing coverage is unknown, not neutral.
  StockTwits and Reddit are not queried for routed A-shares.

## Cross-region macro

Macro is ticker-less. News receives a never-raising US/Japan/China panel;
`get_macro_indicators` exposes the underlying series through content dispatch.

| Series | Source chain |
| --- | --- |
| US macro, USD/JPY, dollar index, VIX | FRED |
| Japan policy rate and Tankan | BOJ |
| Japan CPI/core CPI | e-Stat |
| Japan 10Y | Ministry of Finance daily → FRED monthly |
| Japan GDP/unemployment | FRED |
| China 1Y LPR | Eastmoney |
| China CPI/GDP/official PMI | recent NBS release → non-vintage Eastmoney |
| China unemployment | latest eligible NBS release |
| China 10Y | Eastmoney → limited latest ChinaMoney curve snapshot |
| USD/CNY central parity | SAFE → Eastmoney |

US/Japan CPI and GDP panel cells require the exact prior-year calendar point;
missing counterparts render `n/a`. China CPI/GDP preserve source-provided YoY.
Microscope reports retain the raw observation series.

Panel cells fail independently, so a missing FRED key does not hide free BOJ,
MOF, China, or configured e-Stat data. Audited fallback series retain actual
source, frequency, effective observation date, and sanitized reason.

Macro vendors share a bounded memory cache and namespaced best-effort disk
cache. Today, T-1, and T-2 use 60-minute recent entries; T-3 and older dates use
30-day settled entries. Failures and empty results are not cached. MOF raw CSVs
refresh around the next government-business-day 09:30 JST publication boundary;
settled history has a 30-day maximum age.

## LLM clients

`llm_clients/factory.py` lazily creates native Anthropic, Google, Azure, and
Bedrock clients. OpenAI and other supported compatible services use a
registry-driven client that centralizes endpoints, credentials, and wire quirks.
Model catalog/capability modules define known models and reasoning controls.
Unknown model IDs for supported providers may warn and proceed; unsupported
providers fail loudly.

## Implementation references

- Graph: `tradingagents/graph/`
- Injected tool wrappers: `tradingagents/agents/utils/`
- Routing: `tradingagents/dataflows/interface.py`
- Symbols: `tradingagents/dataflows/symbol_utils.py`
- Provenance: `tradingagents/provenance.py`
- Japan: `tradingagents/dataflows/jp/`
- China: `tradingagents/dataflows/cn/`
- Macro: `tradingagents/dataflows/macro_panel.py`, `macro_common.py`,
  `jp_macro.py`, and `cn_macro.py`
- Contracts: `tests/`, `tests/jp/`, `tests/cn/`, and `tests/live/`
