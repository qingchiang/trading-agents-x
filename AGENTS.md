# Repository Agent Instructions

This is the shared project instruction source for coding agents. Local Codex
preferences live in the git-ignored `AGENTS.override.md`, which explicitly loads
this file.

## Task scope and completion

- Investigation, review, and planning requests authorize inspection and a report;
  implement changes only when the request includes them.
- For implementation requests, complete the in-scope changes, relevant local
  validation, and fixes for failures caused by those changes. Reuse decisions and
  authorization already established in the conversation; ask only when a missing
  decision materially affects scope, correctness, or permission.
- Skill workflows must stay within the current task, project rules, and granted
  permissions.
- Complete the work needed to deliver the requested outcome without expanding
  the task's goals. Report the result, relevant validation, and unresolved blockers.

## Independent maintenance and licensing

TradingAgentsX is maintained independently. Do not monitor or synchronize with
the original project's branches, releases, or roadmap as part of development.
See ADR 0001 for the independent product-line decision and maintenance policy.

Preserve `LICENSE`, `NOTICE`, and applicable copyright, license, and attribution
notices for inherited or incorporated work.

## Before changing code

1. Inspect the current implementation and tests; documentation describes
   invariants, but the code remains authoritative for exact interfaces.
2. Read the relevant parts of [`docs/architecture.md`](docs/architecture.md)
   when a change touches a durable subsystem boundary: public contracts,
   lifecycle or persistence, the graph or Evidence model, data routing,
   point-in-time handling, market adapters, or security boundaries. Localized
   changes outside those boundaries do not require reloading the architecture
   document.
3. Internal interfaces may evolve provided the same change updates their
   callers, tests, migrations, and documentation where applicable. Preserve
   the public contracts listed below unless the task explicitly authorizes a
   contract change; update affected callers, tests, and documentation together.

## Commands

Choose commands relevant to the task; this is a reference, not a mandatory
checklist. Use uv 0.12.1 or newer locally, `uv.lock` for reproducible dependencies,
and `--no-dev` for source-checkout runtime sync and run commands.

```bash
uv sync --locked

uv run --locked pytest -q
uv run --locked pytest tests/data/test_market_routing.py
uv run --locked ruff check .

npm ci --prefix frontend
npm test --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend

# Build the wheel through an isolated sdist; see package-data rules below.
uv build --out-dir wheelhouse
uv run --locked --no-dev python scripts/verify_wheel.py \
  wheelhouse/trading_agents_x-*.whl

# Select relevant probes under the access policy below; default pytest and CI skip these.
RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
  uv run --locked pytest -q -m live_data -k '<relevant_probe>'
```

See `.github/workflows/ci.yml` for the supported Python test matrix, tool pins,
and required CI checks. Pytest markers are `unit`, `integration`, `live_data`,
and `smoke`. For this project's installation workflow, use pip only in a clean
environment that validates the final wheel as a downstream consumer. This
restriction does not apply to independent tools in isolated environments.

## Validation

- Run checks proportionate to the change, including relevant tests and Ruff for
  Python changes. Use the full suite when the impact or agreed acceptance scope
  calls for it.
- Once relevant checks pass, expand or repeat validation only for new changes,
  failures, or unresolved concerns. Fix failures introduced by the task and
  report unrelated failures without expanding the task.
- Use live data checks when a change affects an external interface contract or
  available evidence is insufficient. Select a few representative probes rather
  than running the entire cross-market suite by default. Report missing live
  verification only where it matters to the change.

## Data, model, and database access

- Task-relevant public documentation and small, low-frequency live data queries
  are allowed without separate confirmation. Existing data-service credentials
  may be used within known quotas without incremental charges. Prefer temporary
  caches for probes; reuse existing caches when appropriate while respecting the
  database-write authorization below. Respect rate limits and stop expanding
  requests on repeated failures, rate limiting, or uncertain cost.
- Bulk downloads, historical backfills, continuous polling, material quota use,
  and queries with additional or unclear charges require authorization covering
  their scope and budget.
- Paid LLM calls from project scripts, tests, or the application, including full
  research runs, require explicit authorization. An explicit request to run an
  analysis authorizes that run without a separate numeric budget. Respect any
  stated budget or attempt limits and continue within the authorization without
  asking for each request. Additional unrequested runs or material cost expansion
  need further authorization. This rule concerns project-issued model calls,
  not ordinary coding-agent interaction.
- Read-only diagnosis of an existing application database is allowed when
  relevant to the task. Use an explicit read-only connection, select only needed
  fields and bounded results, and avoid application startup paths that may
  initialize, migrate, or change database settings. Do not dump credentials or
  expose secrets in logs or responses.
- Writes, migrations, deletion, restoration, and other state-changing maintenance
  of an existing application database require explicit authorization. Disposable
  test databases may be created, migrated, and modified as needed for validation.

## Sandboxed environments

Some agent harnesses allow writes only inside the checkout and temporary
directories. If a command fails because a default cache such as `~/.cache/uv`
is read-only, redirect only that tool's cache to a writable temporary path:

```bash
export UV_CACHE_DIR="${TMPDIR:-/tmp}/trading-agents-x-uv-cache"
```

Keep these caches outside the repository so they do not dirty the worktree. Do
not use `sudo`, weaken the sandbox, or change project dependency configuration
to work around a local permission error. A DNS, TLS, proxy, or package-index
failure is a separate network restriction: use the harness's approval mechanism
or report the blocked validation instead of disabling certificate checks or
silently changing package sources.

## Engineering invariants

- `TradingAgents`, `AnalysisRequest`, `AnalysisResult`, `ResearchDecision`, and
  `RunProfile` are the public Python API. Do not reintroduce
  `TradingAgentsGraph` as a compatibility surface.
- `AnalysisService` owns the full run lifecycle. Graph nodes return typed state;
  they must not write reports, application tables, or standalone memory files.
- SQLite is the application source of truth. Markdown and JSON are explicit
  export formats, and legacy report trees remain read-only archives.
- Load immutable application settings at an entry point and resolve immutable
  settings/context for each run. Do not add mutable package-global config or
  import-time dotenv loading.
- All post-analyst roles consume the same sealed `EvidenceBundle`. The final
  decision is research-only and must not contain account sizing, entries,
  stops, targets, orders, or execution instructions.
- Web and worker may share SQLite only on the same host-local filesystem. Do
  not put the database/WAL on NFS or SMB, or add cross-host worker claims
  without changing the repository/checkpointer architecture.
- Data vendors are selected by configured, ordered chains. Do not silently use
  an unconfigured vendor or add ad hoc fallback outside the routing/assembler
  design.
- Vendor failures use the typed taxonomy in
  `tradingagents/domain/vendor_errors.py`. Preserve actual-source and fallback
  provenance when adding or changing a source.
- Strict historical/PIT inputs must fail closed for live-only or non-point-in-
  time data and be truncated to the analysis cutoff. The sole bounded exception
  is explicitly labeled Near-live Advisory Evidence under the documented market-
  local zero-to-five-day policy; it retains retrieval-time provenance and never
  proves historical completeness or absence. Graph-facing dates come from
  workflow state.
- Ticker-less global news, macro, and prediction-market methods remain
  market-agnostic. Market-specific multi-source aggregation belongs in an
  assembler because the generic router is first-success fallback.
- Do not change global HTTP-library behavior to accommodate one source. Keep
  retries, timeouts, caching, and schema validation local to the adapter or its
  shared subsystem utility.

## Dependencies and package data

Runtime imports belong in `[project.dependencies]`, user-facing optional
features belong in PEP 621 `[project.optional-dependencies]`, and test or
development tools belong in the PEP 735 `[dependency-groups].dev` group. Add
them with `uv add <package>`, `uv add --optional <extra> <package>`, or
`uv add --dev <package>` respectively, and include the resulting `uv.lock`
change in the same delivery.

Register non-code runtime files in `[tool.setuptools.package-data]` and load
them with `importlib.resources`. An editable install seeing a local file does
not prove that the wheel contains it.

Build release wheels through an sdist with `uv build --out-dir <empty-dir>`.
Do not use `uv build --wheel` directly from the source tree: setuptools can
reuse an ignored incremental `build/` directory and retain package data that
has since been deleted or renamed.

## Documentation scope

- Keep architecture and durable design decisions in tracked, tool-neutral
  documentation under `docs/`.
- Keep current phase status, test counts, commit hashes, and short-lived plans
  in issues, pull requests, or task-specific notes; do not put them in files
  automatically loaded by every agent session.
- Harness-specific files should adapt this shared guide, not duplicate it or
  become a second source of project truth.

## Agent skills

### Issue tracker

Issues and specs are stored as local Markdown under `.scratch/<feature>/`; do
not publish them remotely unless explicitly requested. See
`docs/agents/issue-tracker.md`.

### Domain docs

This is a single-context repo with `CONTEXT.md` and `docs/adr/` at the root.
Consult the glossary and relevant ADRs when working on domain terminology or
design decisions. Read `docs/agents/domain.md` for that workflow; domain docs
are not prerequisites for unrelated exploration or mechanical edits.

### TDD applicability gate

Use TDD when the change has meaningful observable behavior that can be
tested through a stable seam, especially for business logic, APIs,
validation, transformations, and regression fixes.

Do not force a red-green loop for purely mechanical, presentational,
configuration, or glue-code changes when a focused existing test or
direct verification is more appropriate.

## Git commit instructions

When creating or proposing a Git commit message, inspect the staged diff
(`git diff --cached`) and ensure the message accurately describes only the
staged changes. Do not derive the message from the chat title or use a generic
summary.

Use Conventional Commits:

1. Write a concise title in the form `<type>[optional scope]: <description>`.
2. A body may be omitted only for a single, trivial change that is fully
   explained by the title.
3. Otherwise, add a blank line followed by a concise bulleted body explaining
   what changed and why it changed, when supported by the staged diff or task
   context.
4. Do not include claims unsupported by the staged changes or task context.
5. Mark breaking changes with `!` and explain them in the body or a
   `BREAKING CHANGE:` footer.

Allowed types: `feat`, `fix`, `refactor`, `perf`, `style`, `test`, `docs`,
`build`, `chore`, `ci`, and `revert`.
