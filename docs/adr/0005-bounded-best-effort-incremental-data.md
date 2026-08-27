# ADR 0005: Bounded Best-Effort Incremental Data

- Status: Accepted
- Date: 2026-08-23

## Context

TradingAgentsX inherits experimental historical-analysis paths whose providers
do not all expose point-in-time snapshots, exhaustive feeds, or proof that a
requested interval was scanned completely. Requiring every configured source
to produce audit-complete attempts, Coverage proof, and historical-empty proof
turned the first Incremental market path into a second provider platform whose
contract exceeded both the source capabilities and the product's research-
scaffold positioning.

## Decision

Incremental Research remains a durable Run-backed Research Node with a complete
current Research Decision, Node-local Evidence, sibling isolation, and atomic
commit. Its data collection is bounded best-effort rather than a certified
historical backtest: it reuses configured routers and assemblers, may truncate a
broader provider response locally, records only actual results and provenance,
and never interprets a non-exhaustive empty response as proof of historical
absence.

Strict point-in-time Evidence remains preferred. Retrieval-time snapshots may
enter only as explicitly non-PIT Near-live Advisory Evidence for the existing
inclusive zero-to-five market-local-day window; they may inform synthesis but
cannot prove historical completeness or absence. Older live-only snapshots are
omitted. Missing optional inputs reduce the disclosed availability of the Run
instead of forcing every market into an identical source-completeness product.

The stock Performance component is the v1 deterministic requirement, although
its truthful result may be Not Yet Observable or unavailable. Benchmarks are
independent optional context, and optional Outcome Review is deferred beyond
v1. This decision supersedes only the complete-empty advancement,
mandatory source-level Coverage audit, required dual-benchmark, and v1 Outcome
Review parts of ADR 0002, ADR 0003, and the original Incremental redesign;
their Full-rooted Cycle, legacy-retirement, Evidence-ownership, and atomic-
lifecycle decisions remain accepted.

## Consequences

- Historical and Near-live limitations remain visible instead of being
  overstated as replay or completeness guarantees.
- A useful Incremental Node may commit with partial source availability when it
  has genuine new information and a complete current Decision.
- Provider-specific certification, exact benchmark parity, and exhaustive
  historical backtesting require separately justified future work.
- Existing Batch 3 request, ownership, closure, and transaction invariants are
  retained, while its collection-state and complete-empty contracts require a
  focused transition.
- ADR 0002 and ADR 0003 retain Accepted status and point here for the narrowly
  superseded clauses rather than being rewritten as though this later trade-off
  had existed originally.
