---
status: accepted
---

# Separate outcome feedback from longitudinal research state

The legacy memory flow combines a prior Research Decision, a short-horizon
market outcome, and a free-text Reflection, then injects that record into later
analysis. This can anchor the next thesis to an old conclusion, confuse a
five-completed-interval observation with thesis validity, and leak information
into historical analysis because applicability and point-in-time availability
are not explicit.

Separate the flow into four concepts. Outcome Observation is a deterministic,
versioned ex-post measurement over an explicit market-local window. Outcome
Reflection is a candidate methodological lesson whose failure cannot erase a
completed Observation. Outcome Feedback is created only after qualification
records its source Decision or Revision, method category, horizon limitation,
applicability, point-in-time availability, and eligible, ineligible, or retired
status. Outcome Feedback Context is a bounded, versioned selection of eligible
Feedback for a target cutoff and records inclusion and exclusion reasons.

Feedback becomes available at the latest time when its Observation data,
Reflection, and qualification exist, and it may be selected only when that time
is no later than the target analysis cutoff. Selection must also match declared
Instrument or market, research stage, domain, category, and horizon. An empty
Context is valid. Feedback is neither Evidence nor Prior Research: it cannot
establish Current Research State, satisfy Coverage, or determine a Change
Conclusion or escalation.

For the five-completed-interval return method, the source Decision or linked
Research Revision cutoff may serve as the return's baseline price date. The
linked Revision cutoff is authoritative when present. The Observation start
cannot precede the effective source cutoff, its end must follow the cutoff, and
qualification still becomes available only after the Observation data,
Reflection, and qualification all exist. Outcome Feedback records a
qualification-policy version independently
from its schema version and Observation method version. The first explicit
policy is `outcome_feedback_qualification.v1`. Correcting a policy does not
retroactively requalify an existing Feedback record; legacy and otherwise
unversioned rows remain explicit.

The first Research Chain experiment continues to generate, persist, and expose
Outcome Observation and Reflection but injects neither legacy memory nor
Outcome Feedback Context into authoritative research. A later experiment may
evaluate a non-authoritative Context only after the point-in-time,
applicability, qualification, and selection contracts exist. This delays any
benefit from reflective learning, but keeps incremental-versus-Full validation
from being confounded by an unqualified and asymmetric input.

Implemented by the versioned `short_term_relative_return.v1` Observation,
independent Reflection states, deterministic Feedback qualification and the
explicit empty-memory boundary for Research Chain executions. Selection and
injection of Outcome Feedback Context remain deferred.
