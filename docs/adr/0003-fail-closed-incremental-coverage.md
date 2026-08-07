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
