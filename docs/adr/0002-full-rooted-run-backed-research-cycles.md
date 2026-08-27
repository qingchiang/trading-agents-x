# ADR 0002: Full-Rooted Run-Backed Research Cycles

- Status: Accepted
- Date: 2026-08-16
- Partially superseded by: [ADR 0005](0005-bounded-best-effort-incremental-data.md)
  for complete-empty and Information Advancement semantics only

## Context

Treating every research run as an unrelated full analysis makes an
instrument's history difficult to navigate. The earlier incremental-research
experiment instead made revisions depend on one another and introduced a
large state machine for advancing a single authoritative chain. That coupling
made historical comparison, deletion, and independent reassessment harder to
reason about.

## Decision

Each supported Listed Instrument has one Research Timeline identified by its
canonical normalized ticker. A successfully and atomically committed Research
Run is the Research Node itself: the Run and Node share one identity and one
set of product data, and failed or cancelled Runs never become Nodes.

A Full Research Node establishes a Research Cycle. Every Incremental Research
Node in that Cycle directly uses the Full Node as its baseline and recomputes
from the Full Research Decision plus all eligible incremental information
through its own cutoff. Incremental Nodes are siblings: they do not consume one
another and cannot become baselines. Their cutoffs must be later than the Full
baseline but need not be later than the current Cycle head, so historical
Incremental Nodes may be backfilled without changing the head.

Research Cycle is a derived relationship, not a separately persisted product
object. The Full Node ID identifies the Cycle, and Incremental Nodes reference
that ID as their baseline. All Cycles with an active Full Node remain available
for later incremental research, regardless of Primary selection. A Cycle in
Trash remains retained and readable but cannot accept new Incremental Nodes
until restored.

Committed Node research data is immutable. An Incremental Node may be removed
independently; removing a Full Node removes its entire Cycle. Trash and Primary
selection are mutable product metadata rather than mutations of research
content.

Each Run-backed Node owns one immutable sealed Evidence bundle rather than
linking to a globally deduplicated Evidence store. An Incremental bundle stores
only Evidence from its Full-to-cutoff collection window and may reference the
Full baseline's bundle without copying it. It cannot reference sibling or
cross-Cycle Evidence.

An Incremental Node does not require a nonempty Evidence bundle. A proven
complete scan that finds no matching records or a newly reviewable
Full-baseline component is sufficient information advancement. Performance is
derived from collected market Evidence rather than treated as a separate kind
of advancement. A window containing only failed, unavailable, or unqueried
sources is not.

## Consequences

- Users can compare conclusions produced from different Full baselines or from
  different cutoffs within one Cycle without implying a revision chain.
- Timeline admission must be part of the successful Run transaction; there is
  no separate Node snapshot or eventual promotion step.
- Cycle heads and warnings are derived from active Nodes, while Primary
  selection remains an explicit user choice.
- Incremental analysis may repeat work from the Full baseline, but avoids
  inherited sibling state and the transition machinery required by a chained
  revision model.
- Equivalent Evidence content may be stored by more than one Node. Content
  hashes support comparison, while Node-local ownership keeps deletion,
  restoration, and audit boundaries explicit.
