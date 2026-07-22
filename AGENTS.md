# Repository Agent Instructions

This is the shared entry point for coding agents and agent harnesses working in
this repository. Keep it concise, tool-neutral, and limited to stable rules that
are useful across tasks.

## Before changing code

1. Inspect the current implementation and tests; documentation describes
   invariants, but the code remains authoritative for exact interfaces.
2. Read the relevant parts of [`docs/architecture.md`](docs/architecture.md)
   before changing the agent graph, data routing, point-in-time handling, or
   market adapters.
3. Preserve existing agent-facing tool signatures unless the task explicitly
   requires an interface change.

## Commands

```bash
pip install -e ".[dev]"   # CI-compatible development install

pytest -q
pytest tests/test_market_routing.py
pytest tests/test_x.py::Cls::test_y
ruff check .

# Opt-in network contracts; default pytest and CI skip these.
RUN_LIVE_DATA_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
  uv run --extra dev pytest -q -m live_data

python main.py
tradingagents
```

CI (`.github/workflows/ci.yml`) runs pytest on Python 3.10-3.13, a bare-install
import smoke test, and repo-wide ruff. Pytest markers are `unit`, `integration`,
`live_data`, and `smoke`.

## Sandboxed environments

Some agent harnesses allow writes only inside the checkout and temporary
directories. If a command fails because a default cache such as `~/.cache/uv`
is read-only, redirect only that tool's cache to a writable temporary path:

```bash
export UV_CACHE_DIR="${TMPDIR:-/tmp}/trading-agents-x-uv-cache"
export PIP_CACHE_DIR="${TMPDIR:-/tmp}/trading-agents-x-pip-cache"
```

Keep these caches outside the repository so they do not dirty the worktree. Do
not use `sudo`, weaken the sandbox, or change project dependency configuration
to work around a local permission error. A DNS, TLS, proxy, or package-index
failure is a separate network restriction: use the harness's approval mechanism
or report the blocked validation instead of disabling certificate checks or
silently changing package sources.

## Engineering invariants

- `TradingAgentsGraph` in `tradingagents/graph/trading_graph.py` is the public
  API. The graph and direct Python callers share dataflow implementations.
- Data vendors are selected by configured, ordered chains. Do not silently use
  an unconfigured vendor or add ad hoc fallback outside the routing/assembler
  design.
- Vendor failures use the typed taxonomy in
  `tradingagents/dataflows/errors.py`. Preserve actual-source and fallback
  provenance when adding or changing a source.
- Historical analysis must fail closed for live-only or non-point-in-time data.
  Graph-facing dates come from workflow state and all results must be truncated
  to the analysis cutoff.
- Ticker-less global news, macro, and prediction-market methods remain
  market-agnostic. Market-specific multi-source aggregation belongs in an
  assembler because the generic router is first-success fallback.
- Do not change global HTTP-library behavior to accommodate one source. Keep
  retries, timeouts, caching, and schema validation local to the adapter or its
  shared subsystem utility.

## Dependencies and package data

`pyproject.toml` uses PEP 621 extras. Runtime imports belong in
`[project.dependencies]`; test and development tools belong in
`[project.optional-dependencies].dev`.

Register non-code runtime files in `[tool.setuptools.package-data]` and load
them with `importlib.resources`. An editable install seeing a local file does
not prove that the wheel contains it.

## Documentation scope

- Keep architecture and durable design decisions in tracked, tool-neutral
  documentation under `docs/`.
- Keep current phase status, test counts, commit hashes, and short-lived plans
  in issues, pull requests, or task-specific notes; do not put them in files
  automatically loaded by every agent session.
- Harness-specific files should adapt this shared guide, not duplicate it or
  become a second source of project truth.

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
