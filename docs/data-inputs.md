# Full and Incremental data inputs

The four enabled research roles determine which inputs are requested. Full and
Incremental use the same configured producers; enabling Incremental does not
create new vendor routes or additional research roles. These are bounded,
best-effort inputs for live research, not completeness certificates.

## Core inputs and time semantics

| Role | Shared inputs | Incremental interpretation |
| --- | --- | --- |
| Fundamentals | Overview and compact quarterly income, balance, and cash-flow summaries; US/JP four periods, CN up to eight | JP/CN disclosure/update dates admit releases; older comparison periods remain context. Yahoo statements are near-live, never dated by fiscal period end as if it were publication. |
| Sentiment | US StockTwits; JP ownership/TOB, four-week margin, short disclosures and consensus; CN margin, ownership changes, institutional research and important announcements | Actual publication dates where known; otherwise explicitly near-live. JP margin uses an inferred T+2 exchange-session visibility date. Empty feeds do not establish neutral sentiment or absence. |
| News | Company news, global news, the existing global macro panel and applicable JP aggregate investor flows | Macro observation dates are not release timestamps. JP flows describe the whole market. |
| Market | Configured verified OHLCV/indicator snapshot | Also computes close change, close range and maximum drawdown over actual completed interval rows from one price source and adjustment basis. Snapshot failure does not erase the underlying return series. |

Full fetches the four fundamental methods once on first entering the role. Its
subsequent quarterly tool calls reuse the Run's responses. The producer-owned
structured observations enter model inputs and sealed Evidence; public tool
signatures stay unchanged. A source failure preserves earlier successes, and a
bounded financial collection stops further statement requests after a rate limit.

Financial observations retain report periods, disclosure/update dates, units,
currency and cumulative YTD versus instant balances. Missing mapped fields stay
unknown. Sina cash outflow fields retain Sina's direction convention; they are
not converted to Yahoo's capital-expenditure sign convention. Yahoo's current
currency annotation is explicitly a market convention unless the source supplies
stronger metadata; it must not be treated as verified issuer reporting currency.
Indicator histories shorter than the configured indicator's minimum warm-up
produce unavailable values rather than plausible-looking partial calculations.

The macro panel retains its 60-minute `SeriesCache`. Producer retrieval time is
stored with each refreshed series and survives cache hits. Near-live admission
uses the existing zero-to-five market-local-day rule, not the age of the economic
observation. Information identity excludes retrieval time and presentation order;
macro display-window changes alone do not advance the information frontier.
Yahoo statements use this same near-live boundary in Full prefetch and statement
tools; older dated requests do not fetch current Yahoo statement frames. Configured
JP/CN point-in-time statement providers retain their disclosure-date behavior.

## News budgets and selection

The default final budgets remain 30 company articles and 10 global articles.
Yahoo's company candidate budget is 200 (configurable down to 100 or less).
CNINFO and Eastmoney each use one page, at most 100 candidates. EDINET's 90-day
scan, TDnet's service archive and Google's existing request boundaries remain.
Global queries request 10 candidates each and execute at most the existing five
queries. The candidate target and query budget are independent of the final
output budget. Date filtering and deduplication happen before deciding whether
the candidate target has been reached.

After candidate filtering and cache merge, deduplication uses source record IDs,
URLs and normalized titles. JP retains official-source priority. CN starts with
15/7/8 soft slots for announcements/research/media at a 30-item budget; unused
slots are lent in that order without fetching another page. Within a source,
roughly two thirds of slots favor the last seven dates. The remaining third
rotates across three earlier time bands; unused slots are filled from other
eligible candidates. Small candidate sets retain every eligible distinct item.
CN announcement observations duplicated between news and sentiment share one
source article in sealed inputs, with references kept closed.

Yahoo, CNINFO, Eastmoney, Google, EDINET and TDnet outputs report upstream rows,
date and relevance losses, invalid records, duplicates and source truncation separately; assemblers report final duplicates,
kept and truncated counts. CN source memory caches retain the original upstream
counts before applying a later request's window. Cache merge reports saved
candidates and cache additions. These describe observed candidates, not articles
that the upstream service omitted before responding.

## Request-triggered news source cache

The independent SQLite file is `data_cache_dir/news/sources.sqlite3`. It stores
relevant source candidates before the final model cap, including title, source,
summary/body, link or source record ID, publication time and original retrieval
time. It never reads or writes Run conclusions or the application database.

| Data configuration | Default |
| --- | --- |
| `news_cache_enabled` | `true` |
| `news_cache_refresh_seconds` | `900` |
| `news_cache_retention_days` | `90` |
| `news_cache_scope_limit` | `2000` |
| `news_cache_total_limit` | `50000` |
| `news_article_limit` / `global_news_article_limit` | `30` / `10` |
| `yahoo_news_candidate_limit` / `cn_news_candidate_limit` | `200` / `100` |
| `global_news_candidate_limit` / `global_news_query_limit` | `10` / `5` |
| `news_selection_version` | `2-temporal` |

A successful exact refresh can be reused for 15 minutes. Its signature includes
the requested window, candidate budget and relevant routing/selection settings;
a narrow or smaller fetch cannot certify a wider request. Only currently invoked,
configured sources can read their cache scope. Cached candidates are reselected
for each Full or Incremental request's window and output cap.
Company feeds use the instrument's market calendar; ticker-less global feeds use
UTC, including cached revisions, so they do not inherit a US market date.

Content revisions with recognizable source identity are separate versions. When
a revision has no reliable public timestamp, it is a near-live observation at
first retrieval; the old publication date is context, not proof that the new
text existed then. Historical selection can retain the earlier stored version.
Refresh failure may use eligible saved material while reporting the failure and
original retrieval times. Cache read/write errors fall back to ordinary fetching.
Writes prune expired and oldest excess material transactionally; concurrent
writers use SQLite's local locking. Deleting the cache cannot change sealed Runs.

There is no background polling or historical backfill. The cache accumulates
only material actually seen after activation and cannot prove complete interval
coverage. Settings and selection version are captured in new Run/method
snapshots; existing Runs and public request/result structures are not migrated.
Announcement PDFs, independent SEC feeds, expanded Reddit, prediction-market
changes and additional benchmark indices are outside this capability.
