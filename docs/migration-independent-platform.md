# Migration to the Independent Web and Database Platform

This release is intentionally breaking. SQLite becomes the source of truth,
and the supported entry points are the typed Python API, non-interactive CLI,
and versioned HTTP API.

## Before upgrading

1. Preserve your existing repository or installation until the migration is
   verified.
2. Back up any legacy Markdown memory and report directories.
3. Record provider/model settings you still need without copying credentials
   into notes, issues, or logs.
4. Install the new release into a separate virtual environment.
5. Configure a local database path and provider keys through `.env`.

The migration does not modify legacy report trees or old checkpoint databases.

## Entry-point changes

### Python

Before:

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
graph = TradingAgentsGraph(config=config)
final_state, decision = graph.propagate("7203.T", "2026-07-24")
graph.save_reports(final_state, "7203.T")
```

After:

```python
from tradingagents import AnalysisRequest, RunProfile, TradingAgents

app = TradingAgents.from_env()
result = app.run(
    AnalysisRequest(
        ticker="7203.T",
        analysis_date="2026-07-24",
        profile=RunProfile.STANDARD,
        output_language="ja",
    )
)

print(result.run_id)
print(result.reports)
print(result.decision)
```

`AnalysisResult` replaces the loose `(final_state, decision)` tuple. Reports and
the typed decision are persisted automatically. Use
`tradingagents export RUN_ID` or the export API when a Markdown/JSON file is
needed.

The following compatibility surfaces are removed:

- `TradingAgentsGraph` public export;
- `tradingagents.graph.trading_graph`;
- legacy graph setup, propagation, reflection, and signal modules;
- direct `save_reports()` lifecycle;
- Markdown memory writer;
- per-ticker checkpoint databases;
- interactive Rich/questionary questionnaire.

### CLI

Before:

```bash
tradingagents
```

After, choose an explicit command:

```bash
tradingagents run NVDA --date 2026-07-24 --profile standard
tradingagents serve
tradingagents worker
tradingagents runs list
```

Run `tradingagents --help` and `tradingagents COMMAND --help` for exact
options.

## Configuration migration

Configuration is loaded once at each process entry point. Per-run settings are
resolved into an immutable, redacted snapshot. Do not call a global
`set_config()` function or mutate `DEFAULT_CONFIG` for an active process.

Replace legacy graph/persistence settings with:

```dotenv
TRADINGAGENTS_HOME=~/.tradingagents
TRADINGAGENTS_DATABASE_PATH=~/.tradingagents/tradingagents.db
TRADINGAGENTS_CACHE_DIR=~/.tradingagents/cache

TRADINGAGENTS_LLM_PROVIDER=openai
TRADINGAGENTS_QUICK_THINK_LLM=gpt-5.4-mini
TRADINGAGENTS_DEEP_THINK_LLM=gpt-5.5
TRADINGAGENTS_OUTPUT_LANGUAGE=en
```

Removed settings include legacy debate/risk round counts, Markdown memory
limits, report paths, and the old checkpoint toggle. Profile choice now owns
graph depth; the database/checkpoint lifecycle is always managed by the
application service.

API keys continue to come only from environment variables. They are not
migrated into SQLite.

## Legacy Markdown memory

Only decision/outcome/reflection blocks are importable. Old report trees and
failed checkpoints are not imported.

Start with a dry run:

```bash
tradingagents memory import /path/to/memory.log
```

The JSON report includes total, importable, malformed, skipped, and per-block
issues. A dry run performs no database writes and does not create a backup.

Apply after reviewing the report:

```bash
tradingagents memory import /path/to/memory.log --apply
```

Applied import behavior:

- the original Markdown file is never edited;
- a timestamped adjacent backup is created before importable blocks are
  written, unless `--no-backup` is explicit;
- each normalized block is keyed by a SHA-256 content hash;
- rerunning the command skips hashes already recorded;
- malformed blocks are reported independently and do not hide valid blocks;
- pending blocks remain pending;
- resolved blocks retain raw return, alpha, observation range, holding
  intervals, and reflection when present;
- legacy decisions receive conservative typed defaults for fields the old
  format did not contain.

An applied command exits with code 2 when malformed blocks exist, even if valid
blocks were imported. This makes partial migration visible to automation.

## Reports and checkpoints

Keep old report directories as read-only archives. New runs do not append to
them. Export a completed database run explicitly:

```bash
tradingagents export RUN_ID --format markdown -o report.md
tradingagents export RUN_ID --format json -o result.json
```

Old per-ticker checkpoint databases are incompatible and intentionally ignored.
New checkpoints share the application SQLite file, are namespaced by
run/attempt, and follow the retry/cancel/success cleanup policy.

## Database deployment and backup

The Web and worker must use the exact same local database file:

```dotenv
TRADINGAGENTS_DATABASE_PATH=/absolute/local/path/tradingagents.db
```

Do not place it on NFS, SMB, or another network filesystem. For Docker Compose,
both services use the `tradingagents_data` named volume.

Create a consistent backup while services are running:

```bash
tradingagents db backup /path/to/tradingagents-backup.db
```

The command refuses to overwrite a destination unless `--force` is supplied.

## Behavioral changes to review

- The final output is a research rating, not an account-specific trade.
- Fast, Standard, and Deep have fixed topology contracts.
- Analysts share one sealed evidence snapshot after parallel collection.
- Cancellation takes effect at node boundaries; an active provider request may
  finish first.
- `retry` continues the same run/attempt lineage; “New from this run” opens an
  editable form and creates a linked run with fresh evidence only after
  confirmation.
- Outcome settlement runs in the background and no longer waits for a later
  same-ticker analysis.
- UI language and report language are separate settings.

## Verification checklist

1. `GET /api/v1/health` reports `status: ok`.
2. Web and worker point to one local database.
3. A test run appears in Dashboard and Run Detail.
4. Refreshing Run Detail replays its event timeline.
5. Exported Markdown/JSON matches the persisted result.
6. Legacy memory dry-run has no unexpected malformed blocks.
7. Applied import leaves the original file unchanged and creates a backup.
8. `tradingagents db backup` produces a readable copy.
