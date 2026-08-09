---
status: proposed
---

# Fall back to Full Analysis when a bounded update is inconclusive

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

Evidence closure covers every Evidence reference reachable from the Current
Research State, its Claims and Questions, scenarios, risks, catalysts,
invalidation conditions, Coverage Attestation, delta, Update Summary, and
update audit, together with every Source Record lineage reference. A draft with
an unresolved reference cannot advance the chain. In Shadow Validation, a
bounded No Material Change candidate compared with an Indeterminate Full result
is inconclusive rather than agreement or disagreement; Full is authoritative
for the Revision but is not treated as infallible ground truth.
