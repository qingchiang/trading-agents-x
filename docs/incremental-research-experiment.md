# Incremental Research Experiment

This experiment explores whether TradingAgentsX can maintain a useful live thesis
for an Instrument while spending fewer model tokens on repeated analysis. It is
an experimental open-source capability, not a promise of complete research or
an advisory service.

The durable domain language is defined in [CONTEXT.md](../CONTEXT.md). The two
hard-to-reverse choices are recorded in
[ADR 0002](adr/0002-maintain-research-as-revision-chains.md) and
[ADR 0003](adr/0003-fail-closed-incremental-coverage.md). Implemented Research
Chains, Full updates, Japanese source lineage/change detection, and the
deterministic Shadow gate are documented in
[architecture.md](architecture.md). Bounded semantic assessment and
authoritative No Material Change execution remain deferred experiment phases.

## Questions being tested

1. Can persistent Claims, Questions, scenarios, and invalidation conditions
   preserve a coherent thesis across multiple research cutoffs better than the
   current settled-outcome memory?
2. Can a bounded assessment of new or changed Evidence avoid regenerating the
   complete research process often enough to reduce tokens, cost, and latency?

Correctness mechanisms are included only where they make these experiments
interpretable. They are not intended to become a certification or operational
governance system.

## First vertical slice

The first slice covers manual updates of a small Japanese-equity whitelist:

1. An initial Full Analysis creates a Research Chain and its first Revision.
2. The Revision stores a compact Current Research State: Research Opinion,
   Primary Claims, open Questions, base/bull/bear scenarios with ordinal
   likelihoods, risks, catalysts, invalidation conditions, and Evidence links.
3. A later manual update starts from the current Revision and obtains new or
   changed source material. Source-specific overlap or snapshot retrieval may
   be used where a simple date boundary would miss corrections.
4. Deterministic gates compare source identity/version, coverage, semantics,
   and audited thresholds before any semantic model call. The implemented
   Shadow phase stops here; bounded semantic Change Assessment is the next phase.
5. If the state can be reaffirmed, the system creates a No Material Change
   Revision and a concise Update Summary. If not, it immediately continues
   through the existing Full Analysis pipeline and compares the resulting state
   with the baseline.
6. The execution records token use, model cost when available, elapsed time,
   data coverage limitations, and the reason for any Full escalation.

“Full Analysis” means the project's existing complete pipeline. It does not
claim objective completeness and may reuse cached or persisted source material
when the current point-in-time and provenance contracts allow it.

## Minimal research-state rules

- Prior Research directs reassessment but never supports itself as Evidence.
- Claims are atomic, decision-relevant, and distinguish observation, inference,
  and forecast. Inferences and forecasts state a falsifier.
- Questions persist until answered, superseded, or retired and may reopen.
- Decision Confidence, Claim Confidence, and Scenario Likelihood use ordinal
  values. Numeric probabilities are omitted unless a separate auditable method
  justifies them.
- New Evidence is not automatically a Material Change. A change to the thesis,
  its confidence, scenarios, risks, catalysts, invalidation conditions, or
  Evidence integrity is material.
- A failed or cancelled execution does not advance the Research Chain.

These rules define the state being tested. Exact table layouts, enum names,
prompt schemas, retry counts, and UI components are implementation choices.

## Lightweight validation

Shadow Validation is deliberately small. Historical intervals with known quiet
and material events are followed by a few controlled live pairs. During this
period the Full Analysis result is authoritative:

- an incremental Full escalation stops immediately after recording its reason;
- an incremental No Material Change proposal is compared with a paired Full
  Analysis;
- disagreements are reviewed as experiment findings rather than incidents;
- no universal accuracy threshold or permanent canary system is required.

The useful measurements are:

- whether important Claims and Questions remain coherent across Revisions;
- whether sampled comparisons expose missed thesis changes;
- tokens per successful Revision, split between bounded assessment and Full
  escalation;
- Full escalation rate, cost, and elapsed time.

The experiment is promising only if quiet updates usually cost materially less
than Full Analysis without making the live thesis visibly less useful in the
reviewed examples. No fixed percentage is chosen before measurements exist.

## Deferred from the first slice

- user-uploaded Source Documents or pasted excerpts;
- incremental execution for United States or mainland Chinese equities;
- scheduled or automatic updates;
- localized reruns of selected analysts or committee stages;
- alternative-chain, Fork, merge, or Primary-promotion UI;
- a complete Claim dependency graph, automated Claim split/merge repair, and
  calibrated numeric confidence;
- production certification, SLAs, automated quarantine, and permanent canary
  infrastructure.

The domain model leaves room for several of these capabilities, but the first
experiment should not implement them merely because they were discussed.

## Excluded scope

Crypto research support and compatibility migration from the v0.4.0 legacy CLI
product line are explicitly excluded rather than deferred. The unreleased
Web/SQLite product line may remove those surfaces instead of carrying
compatibility behavior into the experiment.
