# ADR 0007: Independent Runtime and Offline History Cutover

- Status: Accepted
- Date: 2026-09-22
- Partially supersedes: [ADR 0003](0003-retire-legacy-memory-without-migration.md),
  specifically its pre-Timeline Run readability policy

The independently maintained, single-user application no longer preserves
upstream internal imports or development-era execution compatibility. Domain
contracts and pure rules have no application, database or SDK dependencies;
research receives runtime context and persistence callbacks, while data and LLM
adapters do not import application orchestration. The top-level Python entry
point, local SQLite deployment, research semantics and source policies remain.

The database upgrade boundary is `0013_submission_identity`. New installations
start at an independent migration baseline. Older installations must first use
the old program to reach 0013, then run an explicit offline converter into a
nonexistent destination. The predecessor migration chain remains in Git history
and is excluded from the runtime distribution. This trades broad automatic
upgrade compatibility for a smaller, auditable runtime and a safe rollback path.

The converter reads the source without modifying it. It removes pre-Timeline
Runs identified by their historical schema/kind markers, never by absence of a
Research Node. Current-schema failed and cancelled Runs are retained. Node IDs,
Full Baseline links, Primary and Trash state, events, attempts, research content,
Evidence, Decisions and original audit snapshots remain authoritative. Missing
historical facts stay missing; current settings cannot supply them. Retained
Full Nodes remain eligible Incremental baselines without regenerating research.

Web and worker must be stopped and queued/running work resolved before cutover.
Cross-version execution checkpoints are excluded. Switch the configured database
path only after backup and copy validation; keep the original database and old
program together for rollback. No automatic in-place upgrade or deletion of the
source database is permitted.
