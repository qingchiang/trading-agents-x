---
status: accepted
---

# Admit near-live Evidence without treating it as point-in-time Coverage

TradingAgentsX is an experimental, live-first research product whose existing
market-local policy permits retrieval-time snapshots for today and the five
preceding dates. Preserve that bounded tolerance: auditable Near-live Evidence
may inform Full Analysis and Change Assessment, while its source publication or
effective date must not exceed the Research Cutoff and its producer-owned
retrieval time must remain visible.

Admission uses the producer-owned, timezone-aware `retrieved_at`, converted to
the instrument market's local date, against the Research Cutoff. The inclusive
window is `0 <= retrieved_local_date - Research Cutoff <= 5 days`: retrieval on
the cutoff date or one of the five following local dates is eligible. A cutoff
after the retrieval date and age 6+ retrievals are not eligible. The timestamp
must not be later than the sealed Evidence snapshot's timezone-aware
`sealed_at`; missing or naive timestamps fail closed.

Near-live Evidence is Advisory unless the Current Research State explicitly
requires its source. It cannot satisfy Anchor Coverage, prove Transition
Coverage or No Material Change, or turn an empty live-only response into proof
that no Source Record exists. An Advisory near-live input neither qualifies nor
disqualifies an otherwise complete Forward Research Anchor; when promoted to
Required, its non-point-in-time limitation remains blocking.

Keep Information Frontier as the point-in-time Evidence and Coverage boundary.
Do not introduce a second Revision-level acquisition frontier: each live-only
origin retains its own `retrieved_at`, and the sealed Evidence snapshot retains
its `sealed_at` as the deterministic replay bound. Composite sources isolate
point-in-time and near-live channels so an unsafe channel cannot erase a safe
sibling. This decision extends, and
does not supersede, ADR-0007's separation of Anchor Coverage from Transition
Coverage.
