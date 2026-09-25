# Test Responsibilities

Tests follow the subsystem that owns the behavior. Shared offline inputs and
model doubles live in `tests/support`; test modules do not import one another.
Frontend tests live beside their feature, with browser journeys in `frontend/e2e`.
The root Python tests cover distribution identity, the formal Python API and
repository hygiene.

| Responsibility | Primary verification |
|---|---|
| Instrument admission, dates, Evidence, Decision and numeric presentation | `tests/domain` |
| Full profiles across US/JP/CN, model-role budgets, recovery and sealed Evidence | `tests/research/test_research_graph.py` |
| Readable deliberation and decision generation | `tests/research/test_deliberation.py`, `test_decision_generation.py` |
| Decision references and numeric-audit removal migration | `tests/research/test_decision_references.py`, `tests/persistence/test_remove_numeric_audit.py` |
| Routing, actual-source fallback, cache identity, PIT and bounded collection | `tests/data`, including `cn` and `jp` |
| Independent connections, credential rotation, defaults and explicit import | `tests/configuration` |
| SDK protocol differences and model discovery | `tests/llm` |
| Queue, cancel, retry, recovery, event replay and atomic research completion | `tests/application` |
| Real SQLite constraints, migration baseline and 0013 conversion | `tests/persistence` |
| Cycle Primary/Trash/restore/purge and comparison | `tests/application/test_cycle_trash_lifecycle.py`, `test_research_node_comparison.py` |
| HTTP adaptation, security and public schemas | `tests/web` |
| Command adaptation and process supervision | `tests/cli` |
| Research reading, Evidence navigation, diagnostics, settings and languages | Adjacent frontend tests and `frontend/e2e` |
| Distribution and startup | `scripts/verify_wheel.py` and the wheel/Docker CI jobs |

Keep complete rule combinations at their owning boundary. HTTP, CLI and browser
checks protect their adaptation and essential journeys, rather than repeating
all domain combinations. Parameterized cases remain useful when inputs exercise
different rules. SQLite transaction and constraint tests use real databases.

Retired compatibility tests include old internal import paths, old Run readers,
flat model-selection aliases, provider-addressed discovery, provenance marker
encoding and ambient observation capture. Current tests protect structured
source results, producer identity and original retrieval timestamps. Presentation
changes must not alter market rows or disclosure dates consumed by research.

Deterministic model doubles and source fixtures validate behavior and request
budgets without paid model calls. Historical migration fixtures preserve the
predecessor schema independently of the current ORM. The converted Full Baseline
is exercised through a simulated Incremental run. Source-specific availability,
rate-limit, publication-date and adjustment rules remain in adapter/collector
checks; shared normalization is checked once at its boundary.

Run the complete Python suite on every supported Python version for a cross-cutting
change. The frontend gate includes unit tests, type checking, OpenAPI/TypeScript
consistency, build output and Playwright. Validate installed behavior from a wheel
built through an sdist, then check Web and worker startup in Docker. Live source
probes remain selective and opt-in; paid research is not a default cleanup gate.
