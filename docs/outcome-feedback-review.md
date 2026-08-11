# Research Review and Outcome Feedback lifecycle

Status: aligned

This document defines the first two improvement phases for the product-facing
Research Review and the lifecycle behind Outcome Reflection and Outcome
Feedback. It implements the separation accepted in
[ADR 0006](adr/0006-separate-outcome-feedback-from-research-state.md) without
introducing Outcome Feedback Context selection or injection.

## Scope

Phase 1 corrects the product language and information hierarchy of the current
`/memory` surface. Phase 2 makes Reflection generation, retry, validation,
usage, and Feedback retirement auditable. A later Outcome Feedback Context
experiment is explicitly deferred.

These phases do not make Outcome Observation, Outcome Reflection, or Outcome
Feedback into Evidence, Prior Research, Current Research State, or a Change
Conclusion input.

## Settled decisions

### Product language

The product-facing concept is **Research Review**. The UI must not describe the
surface or its Feedback as "Research Memory". The internal domain concepts
remain Outcome Observation, Outcome Reflection, Outcome Feedback, and Outcome
Feedback Context.

The primary UI sections are:

1. Source Research Decision
2. Outcome Observation
3. Method Reflection
4. Method Feedback

The canonical route is `/reviews`. The legacy `/memory` route and user-facing
Memory terminology are removed without a compatibility redirect. Persisted
`memory:<run-id>` references are identifiers rather than URLs; current renderers
must generate Research Review links for any retained historical reference.

### Information hierarchy

The primary card content is, in order:

1. the source Research Decision;
2. the deterministic Outcome Observation; and
3. qualified Method Feedback, when one exists.

The complete model-generated Outcome Reflection is a candidate rather than an
accepted lesson. It belongs in collapsed generation and audit details. When
qualification fails, the primary surface shows the ineligible state and a
concise reason; it must not present the unqualified Reflection as Method
Feedback.

### Legacy context containment

Ordinary non-chain executions must stop receiving the legacy `MemoryContext`
while Outcome Feedback Context remains deferred. Research Chain executions
continue to receive an explicit empty context. This fail-closed containment
prevents a generated Reflection from influencing later research despite its
Feedback being ineligible or retired.

No minimal Feedback selector is introduced as part of Phase 1 or Phase 2.

### Feedback retirement

Feedback retirement is an irreversible, auditable lifecycle transition for one
Outcome Feedback record. It is not a temporary visibility preference, does not
delete its source Observation or Reflection, and does not stop future outcome
settlement for the instrument.

If temporary exclusion is needed later, it requires a separate domain concept;
it must not reinterpret `retired` as a reversible toggle.

### Documentation boundary

This document owns the Phase 1 and Phase 2 design. ADR 0006 remains the durable
architectural decision and should only link to this design rather than absorb
UI, API, or attempt-record details. `CONTEXT.md` remains implementation-free.

## Phase 1: Research Review experience

### Progressive disclosure

The primary surface uses a localized, concise limitation: five common trading
intervals are short-term methodological feedback only. Observation method and
qualification metadata do not appear as body copy.

The following fields remain available under collapsed method and audit details:

- Observation method category and version
- market timezone
- price and adjustment semantics
- complete horizon limitation
- Feedback qualification-policy version
- raw lifecycle states and timestamps

Raw enum values and version identifiers remain visible in that disclosure for
auditability even when the primary copy uses localized labels.

### Derived review status

The API centrally derives a non-persisted `review_status` read model from the
authoritative Observation, Reflection, and Feedback lifecycles. Clients must not
independently reproduce the derivation. Its states are:

- `awaiting_observation`
- `observation_delayed`
- `awaiting_reflection`
- `reflection_retry_scheduled`
- `reflection_failed`
- `reflection_invalid`
- `feedback_available`
- `feedback_ineligible`
- `feedback_retired`
- `lifecycle_inconsistent`

The derived status is not a new lifecycle source of truth. The underlying
states remain available in method and audit details.

The API derives exactly one state in this order:

1. an unresolved Observation with a recent provider failure is
   `observation_delayed`;
2. any other unresolved Observation is `awaiting_observation`;
3. a settled Observation whose initial or manual generation is queued is
   `awaiting_reflection`;
4. a provider failure with another automatic retry scheduled is
   `reflection_retry_scheduled`;
5. a provider failure whose automatic budget is exhausted is
   `reflection_failed`;
6. a candidate that remains structurally invalid after repair is
   `reflection_invalid`;
7. generated Feedback maps to `feedback_retired`, `feedback_ineligible`, or
   `feedback_available` in underlying lifecycle order.

An impossible lifecycle combination derives `lifecycle_inconsistent`; the API
does not guess a normal display status. Collection responses retain the affected
Review so one corrupt row cannot hide all other Reviews. Its card exposes a
data-integrity warning and audit details, disables all actions, and never treats
the item as available Feedback. A detail response may additionally expose a
structured consistency error.

### Actions

The Reflection section owns its regeneration action. When regeneration is
allowed, `Regenerate Method Reflection` occupies its own stable action row
below the failure explanation. On narrow screens it expands to the available
width with a touch target of at least 44 CSS pixels. Once accepted, the action
remains in place, reads `Queued`, and is disabled. It never shares an inline
text flow with lifecycle copy.

The Feedback section owns its retirement action. `Retire this Method Feedback`
is a low-emphasis destructive action in a consistently aligned section action
row. It appears only for eligible Feedback. Ineligible and retired Feedback do
not offer retirement.

### Accessibility and responsive behavior

- Lifecycle changes are announced through an appropriate `aria-live` region.
- An error explanation is programmatically associated with the action that
  addresses it, and both share one semantic section.
- Disclosures are keyboard operable, and no state relies on color alone.
- Focus remains within the affected Review section after an action result.
- Retirement confirmation traps focus while open and restores it to its trigger
  when dismissed. Reduced-motion preferences disable smooth deep-link scrolling.
- Narrow layouts retain stable action placement and adequate touch targets.

### Filtering and ordering

The primary lifecycle filter uses `review_status`, not only the underlying
Observation status. The UI offers these grouped choices:

- Needs attention: structurally invalid Reflection or exhausted automatic
  provider-failure budget
- In progress: awaiting Observation, awaiting Reflection, or scheduled retry
- Feedback available
- Ineligible or retired
- All

The default ordering remains descending source Research Decision creation time.
The UI does not silently reorder history by action priority; selecting Needs
attention is the explicit way to focus actionable Reviews.

The specification owns exact localized copy and executable acceptance cases
within these settled presentation and accessibility boundaries.

## Phase 2: Auditable Reflection lifecycle

### Structured output

Reflection generation uses a versioned structured `OutcomeReflectionDraft`
instead of parsing an exact marker from free text. Its required bounded fields
are:

- `directional_assessment`
- `decision_evidence_lesson`
- `method_lesson`

The application, not the model, owns the fixed short-horizon limitation. The
model is not asked to self-certify compliance with a boolean field. One bounded
repair is allowed after an invalid initial candidate; a still-invalid repair
ends the generation cycle as invalid.

The structured contract is versioned as `outcome_reflection.v1`. Its immutable
Attempt records use `outcome_reflection_attempt.v1`. Observation remains
`short_term_relative_return.v1`. Because qualification now consumes a typed
lesson rather than extracting an exact free-text marker, newly generated
Feedback uses `outcome_feedback_qualification.v2`. Existing v1 and legacy
Feedback retains its recorded status and is not requalified.

### Attempt history

Every initial generation, bounded repair, and user-requested regeneration
creates an immutable Outcome Reflection Attempt. The aggregate Reflection
lifecycle points to its current state and final successful attempt without
overwriting earlier failures. A later successful Reflection does not erase the
attempts that preceded it.

### Invalid-candidate diagnostics

An invalid Attempt retains a strictly bounded, sensitive-data-sanitized raw
candidate for diagnosis. It also records the candidate digest and length plus
typed, sanitized schema-validation issues. Invalid candidate content is never
Outcome Feedback, never appears on the primary surface, and is never available
to later research. An audit view may show it only as safely escaped plain text
behind a closed disclosure.

### Usage ownership

Reflection work does not alter metrics for the already completed Research Run.
Each Attempt independently records LLM calls, input and output tokens,
cache-hit and cache-miss input tokens, reasoning output tokens, active wall
time, and provider-reported cost. `cost_usd` remains null when the provider did
not explicitly report it. Aggregate Reflection usage is the sum of its Attempts
without losing the per-Attempt breakdown.

Usage has an explicit `reported`, `not_reported`, or `legacy_unknown` status.
Unknown token values are null, not zero. Zero is valid only when explicitly
reported. The Attempt kind may establish that an LLM call occurred even when
the provider supplied no token metadata.

### Generation cycles and retry budget

A generation cycle contains one initial structured generation and at most one
bounded schema repair. A candidate that remains structurally invalid after the
repair receives no further automatic model call.

After an initial generation cycle fails because of a provider or transport
error, it receives at most three automatic retry cycles, delayed by one hour,
six hours, and twenty-four hours. After that retry budget is exhausted, the
Reflection remains failed but eligible for an explicit user-requested
regeneration. A user-requested regeneration starts a new manual cycle with the
same one-repair limit. No cycle recomputes or refetches the persisted Outcome
Observation.

In Phase 1 and Phase 2, a successfully generated Reflection is terminal even
when its Feedback is ineligible or later retired. Those states do not permit
another generation from the same Observation. Supporting replacement Feedback
would require a separately designed multi-version Reflection and Feedback
lifecycle; repeated generation must not become a way to select a favorable
lesson.

### Regeneration API

User regeneration creates an explicit generation-cycle resource:

```text
POST /api/v1/outcome-observations/{outcome_id}/reflection-regenerations
Idempotency-Key: <key>
```

The accepted response is `202` and identifies the cycle, its queue state, and
any active cycle. Reusing an idempotency key returns the same cycle. A different
key while another cycle is queued or running receives `409` with the active
cycle identifier. A missing Outcome receives `404`; an unresolved Observation
receives `409`. The legacy retry endpoint remains as a deprecated compatibility
surface for one release cycle.

### Feedback retirement API

Only eligible Feedback may transition irreversibly to retired. Retirement
requires one typed reason from `not_useful`, `too_specific`, `misleading`, or
`other`, plus an optional bounded note. Repeating retirement is idempotent and
returns the existing retired state without appending duplicate reasons.

The confirmation explains that retirement does not delete the Observation,
Reflection, or Attempts and does not disable future settlement for the
instrument.

### Retention

Attempts, typed diagnostics, usage, and bounded sanitized invalid candidates
share the source Research Run's retention lifecycle. They have no independent
TTL and are deleted only when permanent deletion of the source Run cascades to
the Outcome lifecycle.

### Export boundary

Phase 1 and Phase 2 do not add a Research Review export and do not add
post-Run Outcome lifecycle data to `RunExport`. SQLite remains the source of
truth; the UI, API, and database backup provide the required current audit
surfaces. A portable Review snapshot requires a concrete downstream or external
audit need and belongs to the deferred Outcome Feedback Context design.

### Legacy migration

Migration does not fabricate a modern successful Attempt or re-run a model.
Existing Reflection rows are represented honestly:

- generated rows receive `legacy_unstructured_generated` Attempts;
- invalid rows receive `legacy_invalid_reason_unknown` Attempts;
- retryable failures preserve their existing sanitized error code;
- pending rows with no known model call receive no Attempt.

Missing candidates, validation issues, and usage remain null with
`legacy_unknown` usage. Existing Reflection and Feedback status is preserved;
historical Feedback is not requalified.

### Validation boundary

Release acceptance is deterministic and does not require live LLM access.
Backend tests cover initial success, repair success, double invalid output,
provider failure, retry backoff and exhaustion, manual regeneration,
idempotency, and concurrency. Migration tests begin from the current
`0010_outcome_feedback_policy` shape. API tests cover derived status and
`202`, `404`, and `409` transitions. Frontend tests cover every Review status,
keyboard behavior, English, Simplified Chinese, and Japanese copy, and narrow
browser layouts.

An explicitly authorized opt-in live-LLM smoke may record model identity,
structured-output success, and Attempt usage. Its nondeterministic output is not
a release gate.

The specification owns field-level transition payloads, exact size limits, and
executable acceptance cases within these settled lifecycle boundaries.

## Handoff after design alignment

This design does not pre-assign implementation-ticket order. Once the design
tree is closed and the maintainer confirms shared understanding, generate the
implementation specification with `to-spec`. Only after that specification is
complete, use `to-tickets` to derive dependency-aware implementation tickets.

## Explicitly deferred

- Outcome Feedback Context selection and injection
- Shadow evaluation of Feedback Context
- Re-enabling any longitudinal feedback input for ordinary or Research Chain
  executions

The deferred design seed lives at
`.scratch/outcome-feedback-context/draft.md` and is not an implementation spec.
