# ADR 0006: Materialize Incremental Decisions by Outcome

- Status: Accepted
- Date: 2026-08-31

## Context

Incremental Research originally regenerated a complete Research Decision for
every successful Node. When new information did not require a different
judgment, the generated Decision repeated the Full Baseline with small,
difficult-to-audit wording differences. A field-patch contract could avoid that
rewriting, but would duplicate the Decision schema inside Research Reassessment
and create a deterministic merge surface that must evolve with every Decision
field.

## Decision

Incremental synthesis declares one whole-Decision outcome: `unchanged` or
`updated`. Unchanged Nodes skip full Decision generation and materialize the
Full Baseline Decision exactly. Updated Nodes generate and validate a complete
Decision and must contain a real field change. Research Reassessment remains a
component-impact explanation rather than a patch schema, while Full Research
Required remains an independent warning. Both branches atomically persist a
complete current Decision under the same public contract.

Final Research Decision confidence is expressed as the rubric-based levels
`low`, `medium`, and `high`, not as a pseudo-precise numeric probability.
Process-specific analyst, judge, and sentiment confidence remains numeric.

## Consequences

- Common unchanged updates avoid a full Decision LLM call and wording drift.
- Readers and comparison code always receive a complete Research Decision.
- Changes outside current Reassessment component identifiers can still require
  `updated` without turning Reassessment into a generic merge language.
- Historical numeric Decision confidence requires a one-time lossy migration.
- Historical Incremental products that predate the outcome retain an explicit
  Not Recorded Under This Schema state.
