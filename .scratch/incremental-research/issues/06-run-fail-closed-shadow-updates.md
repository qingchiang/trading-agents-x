# 06 — Run fail-closed Incremental Execution in Shadow mode

**What to build:** Let the maintainer exercise bounded Japanese updates safely.
Deterministic gates either request Full Analysis immediately or retain a No
Material Change candidate and pair it with an authoritative Full Analysis for
comparison.

**Blocked by:** 04 — Track Japanese disclosure changes with durable provenance;
05 — Detect Japanese fundamental and market-data changes.

**Status:** resolved

- [x] Required Domains are derived from active Claims and open Questions, while
  contextual domains remain Advisory and are labeled as such.
- [x] Instrument/cutoff validity, source identity and version changes,
  corrections/withdrawals, provider or adjustment-semantic changes, numeric
  threshold crossings, coverage gaps, and schema validity run before any
  semantic model call.
- [x] Missing, partial, stale, live-only, incompatible, or truncated Required
  coverage produces a stable Full-escalation reason and cannot yield a No
  Material Change candidate.
- [x] When a deterministic gate requests Full Analysis, incremental work stops
  immediately after recording its result and the same update continues through
  the Full path without completing unnecessary incremental stages.
- [x] When deterministic gates propose No Material Change, Shadow mode retains
  the candidate and runs Full Analysis; only the Full result may create and
  advance the authoritative Revision.
- [x] Shadow comparison records agreement or disagreement as an experimental
  finding rather than an execution failure.
- [x] The Update Summary, API, reader, events, and persisted audit data show the
  checked windows, Coverage Attestation, candidate outcome, authoritative
  strategy, escalation reason, Evidence lineage, and Full artifacts.
- [x] Existing metrics attribute calls, tokens, cache details, cost when
  available, and elapsed time separately to bounded assessment and Full work;
  short-circuited paths do not report work that never ran.
- [x] Service-level acceptance tests cover quiet Evidence, correction,
  withdrawal, missing coverage, incompatible semantics, threshold crossing,
  duplicate submission, cancellation, and failure without real LLM or network
  calls.

## Answer

Implemented a deterministic Japanese-equity bounded collection and gate before
LLM construction, with fail-closed coverage/version/semantic/threshold checks,
durable partial-progress audit, and immediate short-circuiting. Shadow retains
its candidate or escalation finding while the existing Full path remains the
only authoritative Revision writer. Run/Revision persistence, API/events,
reader presentation, phase-attributed metrics, migrations, and offline
acceptance coverage were updated together.
