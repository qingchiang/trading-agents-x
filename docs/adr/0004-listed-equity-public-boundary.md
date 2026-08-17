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
