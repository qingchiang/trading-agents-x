# ADR 0004: Listed-Equity Public Research Boundary

- Status: Accepted
- Date: 2026-08-17

## Context

The product's data adapters understand more vendor symbols than the research
product should admit. Treating a successful vendor normalization or a market
timezone suffix as product support allowed Crypto, derivatives, indices, and
unrelated exchanges to enter stock-oriented research.

## Decision

New research requests are stock-only and must use a canonical candidate from
one of these product markets:

- US/default equity notation, including supported one-letter share classes;
- four-character alphanumeric Tokyo symbols with `.T`;
- validated mainland A-share symbols with `.SS` or `.SZ`, including supported
  bare codes and `.SH` aliases.

`is_supported_equity_symbol` is the positive candidate predicate. It is
separate from vendor normalization, routing, timezone metadata, and internal
benchmark identities. Crypto pairs, Forex, futures, commodities, indices,
unsupported exchange suffixes, unsupported mainland security families, and
ambiguous symbols fail before Run persistence. Security-type verification is a
separate admission stage owned by `AnalysisService`.

Common bare index aliases (`DJI`, `GSPC`, `IXIC`, `NDX`, `RUT`, and `VIX`) are
rejected deterministically at the candidate boundary. `DOW` remains eligible
for verification because it is also an actual US listed-equity ticker.

The admission stage receives the canonical Instrument Key through one
injected eligibility resolver. It accepts only one exact identity result whose
security classification is affirmative equity. A confirmed ETF, fund, index,
future, Forex, Crypto, or other known non-equity raises the stable
`unsupported_instrument` application error. Empty, fuzzy, mismatched, unknown,
or failed resolver results raise the distinct
`instrument_eligibility_unavailable` error. The former is exposed as HTTP 422
and a CLI usage error; the latter is HTTP 503 and a retryable operational
failure. Neither result creates a Run or any child durable state.

The default resolver participates in the normal data-vendor routing contract
through the dedicated `instrument_eligibility` category. The shipped route is
`yfinance`; changing it to a vendor that does not implement eligibility fails
closed rather than bypassing configuration. Adapter transport and throttle
failures use the shared vendor-error taxonomy before the application maps them
to the stable eligibility-unavailable outcome.

`AssetType` exposes only `stock` for creation. A tolerant
`RunRequestSnapshot` may still represent a legacy `asset_type="crypto"` value
for read-only history and export; converting that snapshot back to an
`AnalysisRequest`, retrying it, using it as a source, or executing a queued
legacy request crosses the current creation boundary and fails.

## Consequences

- Python, CLI, HTTP, and Web creation share the same normalized request model.
- Low-level adapters may retain vendor aliases needed for supported equities or
  internal benchmarks without creating public research support for those forms.
- Current graph, prompt, social, Memory, and Outcome behavior has one stock
  product model; retained history remains inspectable without compatibility
  execution.
- OpenAPI and generated TypeScript creation contracts expose only `stock`.
- Eligibility metadata is used for admission and display only; it is not
  inserted into sealed research Evidence. Benchmark/provider identifiers stay
  on their internal adapter paths and do not invoke public admission.
- Execution repeats the strict check for queued Runs before graph construction
  or market routing. Retained unsupported history remains read-only and
  exportable, but cannot be retried or upgraded into active research.
