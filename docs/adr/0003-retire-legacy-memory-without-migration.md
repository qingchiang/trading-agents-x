# ADR 0003: Retire Legacy Memory Without Migration

- Status: Accepted
- Date: 2026-08-16

## Context

The legacy Memory model settles completed Runs on a fixed schedule, stores
Outcome and Reflection state, and can inject historical feedback into later
independent research. The new model separates deterministic performance
observation, retrospective Outcome Review, and current Research Reassessment,
and performs the latter two only as part of an explicitly requested
Incremental Research Run. Preserving both models would retain conflicting
lifecycle and product semantics.

## Decision

The Research Timeline redesign is an explicit compatibility boundary. Remove
the legacy Memory UI and API, prompt injection, fixed-period settlement,
compatibility aliases, contracts, tables, and persisted Outcome and Reflection
data. Do not automatically migrate or convert existing Runs into Research
Nodes.

Legacy Runs and their core Run, Evidence, report, and Research Decision data
remain readable through Execution History, but they do not belong to a
Research Timeline and cannot serve as Full baselines. Outcome Review and
Performance Observation are new Incremental Research products, not wrappers
around legacy Memory records.

The compatibility break applies only to pre-redesign data. Every retained Full
Research Node created after the redesign must remain eligible for Incremental
Research across later product-schema upgrades through explicit data
migrations. Any future break of that guarantee requires another deliberate
architecture decision.

## Consequences

- The migration deliberately destroys legacy Outcome and Reflection data, so
  rollout and backup procedures must treat it as irreversible.
- The application carries no dual Memory/Review behavior and no conversion
  path for incomplete or incorrect development-era Runs.
- Historical core research remains inspectable without being mistaken for a
  valid incremental baseline.
- Post-redesign schema evolution assumes a stronger compatibility obligation
  than the legacy model receives.
