# Application configuration

Start the application and open **Settings**. SQLite stores daily research
configuration, provider connections and credentials. No `.env` file is required
for a normal local installation. Connections have stable IDs and independent addresses and credentials. Add as
many connections of an existing interface type as needed; vendor presets are
creation templates, not a fixed list of configured services. Anthropic and Google use an API
origin (without `/v1` or `/v1beta`); OpenAI-compatible services use their API base
URL, commonly ending in `/v1`. Model discovery adds each API's list path.

The settings page supports Chinese, English and Japanese, searches labels,
configuration keys and legacy environment names, and explains defaults, ranges,
source and effect. Categories display one working area at a time: model connections, research
defaults, data/news, and storage/maintenance. Each
connection or settings group saves independently. Connection-specific advanced
compatibility parameters are available inside its editor. Restore
Defaults resets that group's non-secret settings; credentials are deleted only
through the explicit credential action. Interface language and sidebar collapse
are controlled in the sidebar and remain local to the browser.

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
Original files are preserved. After a successful import, you can delete `.env`
if it contains only daily configuration and credentials. Keep any startup
variables you still need (such as custom database paths, host/port or LAN
authentication), or move them to your process environment before deleting the
file. Startup variables continue to apply after restart. An explicitly selected
Bedrock system credential chain also continues to use its AWS environment or
profile configuration.

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

Existing installations must use the explicit [offline cutover](database-cutover.md)
from revision `0013_submission_identity`. The source is read-only and the target
must not exist. Backups contain credentials; keep them under local file access
controls. Older databases must first be upgraded using the predecessor program.

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
new credentials are never silently sent to an old address. Historical Runs without complete recorded executable bindings remain readable
for audit; create a new Run to execute them. No connection identity is inferred
from current defaults.

Web capabilities, model discovery, CLI, Python and workers resolve through the
same module. Model discovery caches are isolated by connection ID and its execution/discovery
revision. Editing unrelated research defaults does not invalidate model lists. It can
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


## Model connections and role selection

Quick and deep roles each retain a connection ID, model ID, and reasoning choice.
The UI starts with a shared-connection editor when both IDs match. Switch to
separate connections to mix services, including two accounts or gateways using
the same API. Switching a role's connection clears its unconfirmed model and
reasoning selection; merging roles asks which connection to keep. Model IDs can
always be entered manually. Refreshing model lists is explicit and does not
start a research/model-generation request.

The connection owns its interface (Chat Completions, Responses, Anthropic,
Google, Azure or Bedrock), discovery strategy and model compatibility policy.
Ollama can use Chat Completions with native model discovery. DeepSeek/MiniMax
compatibility rules remain available when changing the endpoint. Generic
compatible endpoints do not automatically acquire vendor-specific behavior.
Responses is explicit in the connection transport.

Python/HTTP requests and daily configuration use the same role structure:

```json
{
  "models": {
    "quick": {"connection_id": "local", "model": "quick-model", "reasoning_effort": null},
    "deep": {"connection_id": "remote", "model": "deep-model", "reasoning_effort": "provider_default"}
  }
}
```

Omitted or null request fields inherit the corresponding daily default.
`provider_default` explicitly omits the native reasoning parameter. Full research
resolves both roles; Incremental resolves only deep and rejects an explicit quick
selection. CLI `--quick-connection`, `--deep-connection`, model and reasoning
flags populate these fields. `--connection` sets both Full role connection IDs.
The old `llm_provider`, flat role request fields, `asset_type`, and `--provider`
are no longer accepted. Environment aliases are understood only during explicit
import and are never consulted during execution.

A new Run records one executable configuration containing its resolved bindings;
Incremental contains no quick binding. Connection transport, compatibility,
capability checks and per-connection reasoning defaults remain independent of
role selection. New exports use schema 12.

Disable a connection to hide it from new research while retaining queued tasks,
resumes and failed-task retries. Replace default references before disabling.
Deletion removes credentials and is blocked while a default or unfinished Run
references the connection. Finished reports remain readable after deletion,
but a failed Run must be recreated with an available connection instead of
retrying the removed one. A non-secret tombstone prevents identity reuse.

The offline converter preserves connection IDs, reset templates, credentials and
initialization. Original request/config/method/submission snapshots remain in
`audit_snapshot`, separately from normalized reading projections. Missing
historical connection identity, analysis brief or decision outcome stays missing.
No model calls or current defaults fill historical facts.

## Settings API changes

`GET /api/v1/settings` includes safe connection views and credential-presence
flags. `/settings/schema` includes creation presets. `PATCH /settings` accepts
`connection_changes` with create/update/reset/delete actions, scoped credential
changes and the current global revision. It can atomically replace role defaults
and disable/delete their previous connection. Reset restores the connection's
original template without changing credentials. Update `enabled` to disable or
reenable; no additional authentication or account flow is required.

`GET /api/v1/settings/connections/{id}/models` discovers that connection's
models. The credential reveal request accepts `connection_id` plus a credential
field such as `api_key`; legacy data-source names remain supported. Reveal and
settings responses use `Cache-Control: no-store`. Mutation/reveal keeps the
existing same-origin and LAN-token rules.

Import previews include target connection IDs and credential field names,
without secret values. Deleted legacy targets are reported as issues: exclude
those imported fields and configure a new connection explicitly. A stale source
fingerprint or revision still rejects application.

Drafts survive category navigation in page memory. Leaving Settings prompts
before discarding changes. Revision conflicts retain drafts and require an
explicit comparison/reapply choice. Viewed keys are cleared when leaving the
connection editor and are never written to browser persistent storage.

### Concurrent editing and restoration

Settings retain a baseline and an in-memory draft for each editing unit. Saving
sends only changed fields. A revision conflict loads the latest values and
shows field-level differences: unrelated edits are merged, while overlapping
edits require an explicit choice. Pending credential replacements and deletions
must also be confirmed; comparison never displays plaintext keys. Save again
after reviewing the merged draft. A second concurrent update requires another
review. Deleted connection identities cannot be restored by reapplying a draft.

A connection's restore action previews changes to non-sensitive parameters and
places them in the draft for review and saving. It preserves credentials, name,
and enabled state. New connections use **Restore creation settings**. Connections
converted from `0013` retain their recorded creation or upgrade template and the
corresponding restore label; conversion does not replace that template with
current defaults. Legacy native effort keys are converted to the connection’s
`reasoning_effort`; its reset template is converted separately using the values
recorded in that template. A selected role value takes precedence, and
`provider_default` explicitly suppresses the native effort parameter.

### Submission replay and Incremental roles

An idempotency key identifies the normalized original submission, including
explicit research overrides and `source_run_id`, separately from its resolved
request and execution snapshot. Repeating the same submission returns the
original Run before resolving current defaults, checking connections, or querying
data sources. Changing an explicit override returns 409. Omitting a setting and
explicitly selecting its default remain different submissions; `null` on a field
whose meaning is inheritance is equivalent to omission. Conversion retains an
original submission identity only when it can be recovered from recorded facts.
Reusing a historical key with an unknown original identity returns an explicit
conflict. The runtime never guesses equivalence or creates a duplicate Run.
The internal identity is excluded from research exports.

New Incremental submissions resolve and validate only `models.deep`; an explicit
`models.quick` override is rejected. There is no quick placeholder in the
execution snapshot. Full Research resolves both roles. Historical original
snapshots remain separate audit records; converted reading projections preserve
missing identity fields as unrecorded. Execution and retry read only the
credentials needed by their retained executable bindings.

Google connections always use the Gemini Developer API with the configured
endpoint and DB key. Ambient Google keys or Vertex backend selection variables
do not change that authentication mode. Bedrock's explicitly selected system
credential chain retains its existing behavior.
