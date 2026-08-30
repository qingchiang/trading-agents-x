# Repository Agent Instructions

This is the shared entry point for coding agents and agent harnesses working in
this repository. Keep it concise, tool-neutral, and limited to stable rules that
are useful across tasks.

## Before changing code

1. Inspect the current implementation and tests; documentation describes
   invariants, but the code remains authoritative for exact interfaces.
2. Read the relevant parts of [`docs/architecture.md`](docs/architecture.md)
   when a change touches a durable subsystem boundary: public contracts,
   lifecycle or persistence, the graph or Evidence model, data routing,
   point-in-time handling, market adapters, or security boundaries. Localized
   changes outside those boundaries do not require reloading the architecture
   document.
3. Keep the typed objects exported from `tradingagents` stable as the public
   application contracts. Internal graph, service, repository, and adapter
   interfaces may evolve with the independent product architecture, provided
   the same change updates their callers, tests, migrations, and documentation
   where applicable.

## Commands

```bash
uv sync --locked

uv run --locked pytest -q
uv run --locked pytest tests/test_market_routing.py
uv run --locked pytest tests/test_x.py::Cls::test_y
uv run --locked ruff check .

npm ci --prefix frontend
npm test --prefix frontend
npm run typecheck --prefix frontend
npm run build --prefix frontend

# Omit --wheel: uv then builds the wheel from an isolated sdist instead of
# reusing a potentially stale in-tree build/ directory.
uv build --out-dir wheelhouse
uv run --locked --no-dev python scripts/verify_wheel.py \
  wheelhouse/trading_agents_x-*.whl

# Opt-in network contracts; default pytest and CI skip these.
RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
  uv run --locked pytest -q -m live_data

uv run --locked --no-dev tradingagents run NVDA --date 2026-07-24
uv run --locked --no-dev tradingagents serve
uv run --locked --no-dev tradingagents worker
```

CI (`.github/workflows/ci.yml`) uses uv and `uv.lock` for pytest on Python
3.12-3.14, repo-wide Ruff, frontend unit/browser/type/build checks,
OpenAPI/type drift checks, wheel validation, and Docker Web/worker smoke. A
fresh standard venv installs the final wheel with pip as the only project
installation path that does not use uv, preserving the downstream wheel
consumer contract. Pytest markers are `unit`, `integration`, `live_data`, and
`smoke`.

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
  `tradingagents/dataflows/errors.py`. Preserve actual-source and fallback
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
- Normal development does not merge `upstream/main`. Monitor upstream
  read-only and adapt a relevant security/correctness fix only after auditing
  it against TradingAgentsX contracts; see ADR 0001.

## Dependencies and package data

Runtime imports belong in `[project.dependencies]`, user-facing optional
features belong in PEP 621 `[project.optional-dependencies]`, and test or
development tools belong in the PEP 735 `[dependency-groups].dev` group. Add
them with `uv add <package>`, `uv add --optional <extra> <package>`, or
`uv add --dev <package>` respectively, and commit the resulting `uv.lock`
change.

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
See `docs/agents/domain.md`.

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
