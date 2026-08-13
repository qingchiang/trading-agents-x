---
status: accepted
---

# Fall back to Full Analysis when a bounded update is inconclusive

The baseline-eligibility and Indeterminate-anchor rules in this ADR are
superseded by [ADR 0007](0007-separate-anchor-and-transition-coverage.md). The
remaining fail-closed Change Assessment, escalation, Evidence closure, market
capability, and Shadow Comparison decisions remain accepted.

Incremental Execution is not defined as fetching a shorter date range. It uses
the current Research Revision as an Eligible Baseline, collects source-aware
changes since that cutoff, and checks them against active Claims, open
Questions, and their Required Domains. Prior Research is a reassessment
checklist; only source material with timing and provenance is Evidence.

The first experiment uses a binary gate. A bounded Change Assessment may create
a No Material Change Revision only when its Coverage Attestation records no
decision-relevant change and no unresolved gap. Otherwise it automatically
runs the existing Full Analysis pipeline; there is no force-incremental path.
Every successful Revision retains an Effective Evidence Snapshot containing
inherited and new Evidence with explicit lineage. Source corrections,
withdrawals, incompatible semantics, or uncertain novelty also cause Full
Analysis rather than a guessed incremental conclusion.

Full Analysis is an independent reassessment strategy, not a guarantee of
complete coverage. When it cannot support either Material Change or No Material
Change, it creates an Indeterminate Revision that advances the chain and
preserves the resulting state, Evidence, and limitations. That Revision is
readable and exportable but never an Eligible Baseline; the next manual update
must use Full Analysis again. Baseline eligibility is derived separately from
Revision Role and Change Conclusion and requires complete state, Evidence
closure, Coverage Attestation, source versions, and compatible market
semantics. A later candidate cannot repair an ineligible baseline by rewriting
its coverage.

Required Source completeness is source-aware rather than a non-empty-version
check. Each Required Source must have a complete point-in-time Source Watermark
covering the Revision cutoff. A completed scan that returns no records is valid;
a scan that reports records must resolve to observed Source Record Versions in
the same Effective Evidence Snapshot. The first bounded capability applies to
supported Japanese equities as a market capability. United States and
mainland-China equities remain Full-only until
their own typed source-coverage contracts exist.

One typed next-update policy evaluation combines Revision validity, Required
Source completeness, market capability, and the configured experiment mode.
API presentation, enqueue enforcement, and deterministic assessment use that
same result and stable reasons; no caller can force an unsupported or
ineligible Instrument into Incremental Execution.

Evidence closure covers every Evidence reference reachable from the Current
Research State, its Claims and Questions, scenarios, risks, catalysts,
invalidation conditions, Coverage Attestation, delta, Update Summary, and
update audit, together with every Source Record lineage reference. A draft with
an unresolved reference cannot advance the chain. In Shadow Validation, a
bounded No Material Change candidate compared with an Indeterminate Full result
is inconclusive rather than agreement or disagreement; Full is authoritative
for the Revision but is not treated as infallible ground truth.
