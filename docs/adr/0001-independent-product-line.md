# ADR 0001: Independent TradingAgentsX Product Line

- Status: Accepted
- Date: 2026-07-27
- Amended: 2026-09-16 (independent maintenance without upstream monitoring)

## Context

TradingAgentsX began as a compatibility-conscious fork of TradingAgents.
Avoiding structural changes made upstream merges easier while the project built
US, Japanese, and China A-share data paths and point-in-time/provenance
contracts.

The product now needs boundaries that differ materially from the inherited
runtime:

- a local Web run center and durable database rather than interactive CLI
  questionnaires and report directories;
- run-scoped immutable configuration rather than package-global mutation;
- recoverable queue, event, and checkpoint lifecycles;
- typed, evidence-first analyst handoffs;
- research decisions rather than account-level Trader/portfolio instructions;
- graph profiles designed for this product.

Continuing to optimize for mergeability would make these requirements harder
to implement and would keep two incompatible product models coupled.

## Decision

TradingAgentsX is an independent product line.

1. Development does not monitor or synchronize with the original project's
   branches, releases, or roadmap. No upstream remote is needed for maintenance.
2. Security and correctness work is driven by this project's requirements and
   evidence, without an obligation to track the original project's changes.
3. External work may be incorporated when relevant to a task, after reviewing its
   assumptions, dependencies, data semantics, and effect on TradingAgentsX tests.
4. Release versions and schedules are owned by TradingAgentsX.
5. Git history, Apache-2.0 licensing, NOTICE attribution, and the original paper
   citation are retained.

### Amendment: 2026-09-16

The original decision retained read-only upstream monitoring and selective fix
triage. The maintainer ended that standing workflow because this product is now
maintained independently of the original project's development. This amendment
removes the monitoring obligation while preserving the architectural decision,
project history, licensing, and attribution.

## Consequences

### Positive

- Public contracts can follow the Web/database research product directly.
- Legacy graph topology and persistence paths can be removed instead of carried
  as permanent compatibility layers.
- Configuration, evidence, and point-in-time semantics remain owned by this
  repository.
- Architecture changes can be reviewed and released independently.

### Costs

- Upstream fixes are no longer received automatically.
- Security and correctness fixes must be identified and validated through this
  project's own maintenance, rather than a standing upstream-monitoring process.
- Users of `TradingAgentsGraph`, interactive questionnaire flows, Markdown
  memory, or report-directory APIs need a breaking migration.
- Attribution and provenance of selectively incorporated changes require
  deliberate maintenance.

## Guardrails

- Do not describe an upstream merge as routine maintenance.
- Do not copy a change solely because it exists upstream; trace its runtime and
  data assumptions first.
- Preserve TradingAgentsX market-local date, Evidence, fallback, and provenance
  contracts when adapting external work.
- Document incorporated work in commits, changelog entries, and NOTICE when
  licensing or attribution requires it.

## Reconsideration

Revisit this decision only if the products converge again at the public API,
persistence, graph, and data-contract levels. Sharing selected libraries or
algorithms is not by itself a reason to resume branch merging.
