# Application configuration

Start the application and open **Settings**. SQLite stores daily research
configuration, provider connections and credentials. No `.env` file is required
for a normal local installation. Each provider has one connection; provider
addresses and credentials are independent. Anthropic and Google use an API
origin (without `/v1` or `/v1beta`); OpenAI-compatible services use their API base
URL, commonly ending in `/v1`. Model discovery adds each API's list path.

The settings page supports Chinese, English and Japanese, searches labels,
configuration keys and legacy environment names, and explains defaults, ranges,
source and effect. Research defaults, model services, news, data routing,
cache/retention and advanced compatibility settings save independently. Restore
Defaults resets that group's non-secret settings; credentials are deleted only
through the explicit credential action. Interface preferences remain local to
the browser.

## First start and migration

The Web UI and historical research remain available before initialization.
Workers wait without claiming queued Runs, and new research requires completing
configuration. Choose **Use program defaults** to start configuring manually,
or preview an import from existing environment configuration. Importing is
explicit, including on subsequent launches.

An import can use the startup environment and existing or uploaded `.env` and
`.env.enterprise` files. Priority is process environment, primary `.env`, then
`.env.enterprise`. The preview shows non-secret values, credential presence,
conflicts and invalid or unsupported fields. Exclude named fields or correct the
source and preview again before applying. Credentials never appear in the
preview. Application of the reviewed preview is atomic and checks both the
source fingerprint and the database revision.

The old `TRADINGAGENTS_LLM_BACKEND_URL` belongs only to the imported default
provider. Provider-specific addresses such as `OLLAMA_BASE_URL` and
`AZURE_OPENAI_ENDPOINT` import into their respective connections. Azure now
uses Settings for its key, endpoint, deployment and API version; no separate
enterprise file is necessary. Bedrock offers bearer token, static AWS access
keys, or an explicitly selected system AWS credential chain with region and
optional profile. System authentication can use SDK-supported roles/profiles;
it is not a fallback for a missing DB-managed bearer token or access key.

After initialization, daily environment variables do not override or supply
missing DB values. Editing an old `.env` does not update a running application.
Original files are preserved; remove obsolete daily values yourself when they
are no longer needed. Startup variables continue to apply after restart.

Headless setup uses the same configuration module:

```bash
# Read-only previews; do not create or migrate a database.
tradingagents config initialize
tradingagents config import-env --file .env --enterprise-file .env.enterprise

# Explicitly apply the selected operation.
tradingagents config initialize --apply
tradingagents config import-env --file .env --apply
tradingagents config import-env --file .env --exclude OLD_VARIABLE --apply
```

Back up the existing database before upgrading. Stop old Web/worker processes,
upgrade, complete the settings import, and then allow workers to continue.
The new migration adds configuration tables without rewriting retained Runs or
research products. An entire database backup now includes credentials.

## Resolution and execution

Daily parameter precedence is explicit Run request, saved DB default, program
default. CLI flags omitted by the user inherit DB defaults, including research
depth and analysts; an explicit `standard` still overrides a saved `deep`.
`TradingAgents.from_env()` retains its public entry point but now uses the
environment for startup and import candidates, not daily runtime overrides.

Creation freezes research parameters and non-secret connection information on
the Run. Queued Runs, retries and checkpoint resumes retain those parameters.
At each execution start, the application reads the current credentials once,
and binds them to an isolated in-memory execution context. Key rotation affects
the next execution, not clients already running. Endpoint/connection changes
reject execution of incompatible older Runs with a prompt to create a new Run;
new credentials are never silently sent to an old address. Legacy Runs without
complete connection information use only retained fields and built-in provider
defaults when that is unambiguous.

Web capabilities, model discovery, CLI, Python and workers resolve through the
same module. Model discovery caches include the configuration revision. It can
fall back to configured models/manual model IDs when live discovery is
unavailable; Bedrock bearer authentication currently uses that fallback.
Saving configuration does not call a paid model or start research.

News windows are calendar offsets inclusive of both endpoints: a 14-day offset
covers 15 dates. Candidate budgets, output budgets and provider hard limits are
different: Yahoo candidates cap at 200, China candidates at 100, and global news
queries at 5. Increasing a window or budget does not prove historical coverage.
The source cache retains observed material only. Routing retains the existing
tool, market, default-category precedence and ordered fallback semantics.

Trash retention is read at the next cleanup. Reducing it can remove older items
already in the trash; zero disables automatic deletion. News cache changes apply
to newly created research.

## Startup settings and internal values

Keep paths (`TRADINGAGENTS_HOME`, `TRADINGAGENTS_DATABASE_PATH`,
`TRADINGAGENTS_CACHE_DIR`), host/port, worker poll/lease and SQLite timeout in
startup configuration. Docker publication variables and LAN token/session secret
also stay there. The settings page displays deployment information read-only.
Compose accepts an absent optional `.env` file. For bundled Ollama, set the
Ollama connection address to `http://ollama:11434/v1` in Settings.

Project paths and the news-selection algorithm version are internal values, not
editable research preferences. Configuration catalog tests require every default
field to be classified so a new configuration does not become undiscoverable.

## Credentials and administrative API

Credentials are ordinary local SQLite data protected by local file access, just
as a local `.env` was. No accounts or encryption service are introduced. Keys
are masked by default, with explicit reveal/copy, replacement and deletion.
They are excluded from Run snapshots, graph state/checkpoints, events, research
exports and browser persistent storage. Whole-database backups include them.

`GET /api/v1/settings` returns effective settings, origins, credential presence,
initialization and revision. `GET /api/v1/settings/schema` describes the editable
catalog. `PATCH /api/v1/settings` atomically updates values and credentials and
supports explicit `reset_fields`; omitted fields remain unchanged and legal
null values retain their field meaning. Credentials set to null are deleted.
A stale revision returns HTTP 409, preserving the browser's pending edits.

`POST /api/v1/settings/credentials/reveal` returns only the requested credential.
`POST /api/v1/settings/import/preview` and `/import/apply` implement the reviewed
import and initialization workflow. All settings responses use `Cache-Control:
no-store`. Browser writes/reveal enforce same origin. Existing LAN authentication
also applies; local loopback use remains login-free.
