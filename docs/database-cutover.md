# Offline Database Cutover

The independent runtime accepts new databases and its own migration baseline.
It does not open older databases for automatic upgrade. Its predecessor input
is exactly `0013_submission_identity`; use the old program first if the database
has an earlier revision.

1. With the old program, finish or cancel queued/running research and stop Web
   and worker. Keep the old program available for rollback.
2. Create a backup using the old `tradingagents db backup <backup.db>` command.
3. With the new program, convert into a **new** destination:

   ```sh
   tradingagents db migrate-current --source /path/old.db --destination /path/new.db
   ```

4. Verify the count and missing-field report, retained Timeline nodes and baseline usability
   against a disposable copy before switching the installation's
   `TRADINGAGENTS_DATABASE_PATH` to the new database.
5. Restart Web and worker together with the new program and database path.

The source is read-only; an existing destination is never overwritten. A failed
conversion leaves no published target. The converter checks database integrity,
foreign keys and copied values before publishing the completed file. It omits
cross-version checkpoints, retains current-schema execution history (including
failed and cancelled Runs without Nodes), and removes only pre-Timeline Runs
identified by their historical markers. The report never prints credentials.

Retain the original database and backup. Before new research is written, rollback
consists of stopping the new processes and restoring the old program and original
database path. Research written after switching belongs to the new database and
must not be silently discarded during a later rollback.

The report counts retained/removed Runs, Research Nodes, verified tables and
missing historical fields. Original request, configuration, method and submission
snapshots are retained separately from normalized projections. These audit
snapshots never enter execution validation. The converter preserves recorded
submission identity; an old idempotency key whose identity cannot be recovered
returns an explicit conflict and requires a new key.
