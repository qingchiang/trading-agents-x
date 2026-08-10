# Incremental Research Experiment

This experiment explores whether TradingAgentsX can maintain a useful live thesis
for an Instrument while spending fewer model tokens on repeated analysis. It is
an experimental open-source capability, not a promise of complete research or
an advisory service.

The durable domain language is defined in [CONTEXT.md](../CONTEXT.md). The two
hard-to-reverse choices are recorded in
[ADR 0002](adr/0002-maintain-research-as-revision-chains.md) and
[ADR 0003](adr/0003-fail-closed-incremental-coverage.md). Implemented Research
Chains, Full updates, Japanese source lineage/change detection, bounded
semantic assessment, Shadow comparison, and authoritative experimental No
Material Change execution are documented in [architecture.md](architecture.md).

## Internal enablement

The experiment is fail-closed and off by default. Configure the same mode on
the Web and worker process, then restart them:

```dotenv
TRADINGAGENTS_RESEARCH_UPDATE_MODE=shadow
```

`TRADINGAGENTS_RESEARCH_UPDATE_MODE` accepts exactly `off`, `shadow`, or
`experimental`.

- `off` always runs Full Analysis.
- `shadow` runs bounded assessment for a source-qualified supported Japanese
  Instrument, retains its
  candidate or escalation reason, and always makes the paired Full result
  authoritative.
- `experimental` permits a fully covered, semantically unchanged NMC candidate
  to advance the chain without analyst reports or deliberation. Every material,
  incompatible, incomplete, invalid, novel, or uncertain result continues into
  Full Analysis within the same update.

Japanese heads without complete Required Source coverage and all other markets
continue to use Full Analysis. The mode is snapshotted when the update is
queued, so a retry cannot silently change its experiment mode.

## Questions being tested

1. Can persistent Claims, Questions, scenarios, and invalidation conditions
   preserve a coherent thesis across multiple research cutoffs better than the
   current settled-outcome memory?
2. Can a bounded assessment of new or changed Evidence avoid regenerating the
   complete research process often enough to reduce tokens, cost, and latency?

Correctness mechanisms are included only where they make these experiments
interpretable. They are not intended to become a certification or operational
governance system.

## First vertical slice

The first slice covers manual updates of supported Japanese equities:

1. An initial Full Analysis creates a Research Chain and its first Revision.
2. The Revision stores a compact Current Research State: Research Opinion,
   Primary Claims, open Questions, base/bull/bear scenarios with ordinal
   likelihoods, risks, catalysts, invalidation conditions, and Evidence links.
3. A later manual update starts from the current Revision and obtains new or
   changed source material. Source-specific overlap or snapshot retrieval may
   be used where a simple date boundary would miss corrections.
4. Deterministic gates compare source identity/version, coverage, semantics,
   and audited thresholds before one schema-constrained semantic assessment is
   allowed.
5. If the state can be reaffirmed, the system creates a No Material Change
   Revision and a concise Update Summary. If not, it immediately continues
   through the existing Full Analysis pipeline and compares the resulting state
   with the baseline.
6. The execution records token use, model cost when available, elapsed time,
   data coverage limitations, and the reason for any Full escalation.

Every Revision separately records its Role, Execution Strategy, and optional
Change Conclusion. Initial Revisions have no Change Conclusion. If independent
Full Analysis cannot justify Material Change or No Material Change, it advances
the chain with an Indeterminate Revision, preserves the state, Evidence, and
limitations, and requires the next manual update to be another Full
reassessment.

“Full Analysis” means the project's existing complete pipeline. It does not
claim objective completeness and may reuse cached or persisted source material
when the current point-in-time and provenance contracts allow it.

## Minimal research-state rules

- Prior Research directs reassessment but never supports itself as Evidence.
- Claims are atomic, decision-relevant, and distinguish observation, inference,
  and forecast. Inferences and forecasts state a falsifier.
- Questions persist until explicitly reaffirmed, answered, superseded, or
  retired and may reopen. After independent Full research, a bounded Question
  Disposition step uses only current sealed Evidence and application-assigned
  IDs. Omission never resolves a Question; ambiguous, incomplete, or twice
  invalid output preserves the baseline status, limits Question coverage, and
  makes an otherwise unchanged Revision Indeterminate. A superseded Question
  links to a separately identified successor.
- Decision Confidence, Claim Confidence, and Scenario Likelihood use ordinal
  values. Numeric probabilities are omitted unless a separate auditable method
  justifies them.
- New Evidence is not automatically a Material Change. A change to the thesis,
  its confidence, scenarios, risks, catalysts, invalidation conditions, or
  Evidence integrity is material.
- A failed or cancelled execution does not advance the Research Chain.
- A successful Revision advances only after all state, coverage, delta,
  summary, audit, Evidence, and Source Record lineage references close within
  its own Effective Evidence Snapshot.
- Eligible Baseline status is server-derived. Incomplete or Indeterminate heads
  report `full_required`; there is no force-incremental repair path.
- EDINET and TDnet are always Required for Japanese announcements. Required
  fundamentals and market domains use their typed J-Quants contracts, and
  active Claim/open Question dependencies add further Required Sources. Each
  source needs a complete point-in-time Watermark over the cutoff. Zero results
  are valid; positive results need a same-source observed version whose Evidence
  resolves in the Effective Evidence Snapshot.

These rules define the state being tested. Exact table layouts, enum names,
prompt schemas, retry counts, and UI components are implementation choices.

## Lightweight validation

Shadow Validation is deliberately small. The reviewed offline contract set
covers quiet Evidence, official correction/withdrawal, missing Required
coverage, incompatible semantics, threshold crossing, material novelty, and
invalid or indeterminate structured output. These cases are exercised through
the service seam with a real temporary SQLite repository and deterministic
collectors/models. A few controlled live pairs may then be run with
`RUN_LIVE_DATA_TESTS=1` and `PYTHON_DOTENV_DISABLED=1`; live observations must
contain only ticker, cutoff, source status, reason codes, and aggregate metrics,
never credentials, prompt text, private reasoning, or response headers.

The pytest live runner remains a non-authoritative rehearsal: it backs up the
configured database and runs each case against a temporary copy. It must not be
used as evidence that a main-database Research Chain advanced.

### Authoritative maintainer validation

The authoritative workflow is deliberately user-triggered and writes to the
configured main SQLite database. Implementing or installing it does not
authorize a live run. Before a separately approved run:

1. Stop or drain the worker so no other update can advance a selected head.
2. In the Research Chain reader, review exactly five distinct Japanese Chains
   whose server-derived next-update policy is `incremental_allowed`. Select one
   Chain for each of `quiet_interval`, `material_event`, `source_integrity`,
   `missing_coverage`, and `threshold_crossing`; do not reuse a Chain.
3. Review each cutoff, expected bounded result, and expected Full Change
   Conclusion. Include Material Change, No Material Change, and Indeterminate
   across the set. Save only this control metadata in a JSON file such as
   `tmp/incremental-research/reviewed-live-cases.json`:

```json
[
  {"scenario":"quiet_interval","chain_id":"<quiet-chain-id>","analysis_date":"2026-08-10","expected_bounded_result":"no_material_change","expected_full_change_conclusion":"indeterminate"},
  {"scenario":"material_event","chain_id":"<material-chain-id>","analysis_date":"2026-08-10","expected_bounded_result":"source_version_change","expected_full_change_conclusion":"material_change"},
  {"scenario":"source_integrity","chain_id":"<integrity-chain-id>","analysis_date":"2026-08-10","expected_bounded_result":"source_correction","expected_full_change_conclusion":"no_material_change"},
  {"scenario":"missing_coverage","chain_id":"<coverage-chain-id>","analysis_date":"2026-08-10","expected_bounded_result":"coverage_incomplete","expected_full_change_conclusion":"indeterminate"},
  {"scenario":"threshold_crossing","chain_id":"<threshold-chain-id>","analysis_date":"2026-08-10","expected_bounded_result":"threshold_crossing","expected_full_change_conclusion":"material_change"}
]
```

Keep the application in `shadow` mode. Supply the two existing live opt-ins and the separate
`--in-place-database` confirmation. The `--backup` destination must not exist:
Run the command from a clean Git checkout. Before it loads the cases, opens the
application, creates the backup, or starts a Research Execution, it refuses
staged changes, tracked modifications, and non-ignored untracked files. Ignored
credentials, SQLite databases, backups, reviewed cases, and prior manifests do
not make an otherwise clean checkout invalid. The command records the full
commit of that exact source checkout and binds the sanitized manifest to its
ignored `tmp/` area; it never copies a diff or repository content into the
manifest.

```bash
RUN_LIVE_DATA_TESTS=1 RUN_LIVE_LLM_TESTS=1 PYTHON_DOTENV_DISABLED=1 \
TRADINGAGENTS_LLM_PROVIDER=deepseek \
TRADINGAGENTS_QUICK_THINK_LLM=deepseek-chat \
TRADINGAGENTS_DEEP_THINK_LLM=deepseek-chat \
TRADINGAGENTS_RESEARCH_UPDATE_MODE=shadow \
uv run --locked tradingagents research validate-live-thesis \
  tmp/incremental-research/reviewed-live-cases.json \
  --backup tmp/incremental-research/live-validation-backup.db \
  --in-place-database
```

Export `DEEPSEEK_API_KEY` and, when the reviewed cases depend on authenticated
official sources, `JQUANTS_API_KEY` and `EDINET_API_KEY` in the launching shell.
Do not put credentials in the cases file or command line.

The command completes and verifies an ordinary online SQLite backup before it
queues the first execution. It records a recovery-point file containing only
the backup filename, size, SHA-256 digest, creation time, and Alembic revision.
It then writes one exclusive sanitized JSON entry per scenario under the
ignored `tmp/incremental-research/live-validation/` area. Entries contain only
the exact clean source commit, reviewed expectations, application status, validation
verdict, and run/Chain/Revision IDs. A successful application execution whose
results differ from expectations is `expectation_mismatch`; it is never passed.
If enqueueing fails before SQLite creates a run, the scenario still receives an
`application_failed` entry with a null run and Revision ID.

`expected_bounded_result` may be `no_material_change` or a stable Full-escalation
reason such as `source_correction`, `source_withdrawal`,
`coverage_incomplete`, `source_version_change`, or `threshold_crossing`.
The paired Full result remains authoritative. A No Material Change candidate
paired with an Indeterminate Full result must be recorded as `inconclusive`.

Inspect the manifest first, then use its IDs to inspect the current CLI database
layout without copying payloads into the manifest:

```bash
tradingagents runs show <run-id>
sqlite3 ~/.tradingagents/tradingagents.db \
  "SELECT id,status,research_chain_id,baseline_revision_id FROM runs WHERE id='<run-id>';"
sqlite3 ~/.tradingagents/tradingagents.db \
  "SELECT id,current_revision_id FROM research_chains WHERE id='<chain-id>';"
sqlite3 ~/.tradingagents/tradingagents.db \
  "SELECT id,predecessor_revision_id,producing_run_id,execution_strategy,change_conclusion FROM research_revisions WHERE id='<revision-id>';"
```

For recovery, first stop Web and worker and preserve the current database and
its WAL/SHM sidecars. Prefer pointing `TRADINGAGENTS_DATABASE_PATH` at a copy of
the recorded backup and inspecting it before replacing anything. The backup's
filename, digest, size, and Alembic revision in `recovery-point.json` identify
the intended recovery point. Never combine the backup with stale WAL/SHM files.

During Shadow validation the Full Analysis result is authoritative:

- an incremental Full escalation stops immediately after recording its reason;
- an incremental No Material Change proposal is compared with a paired Full
  Analysis;
- disagreements are reviewed as experiment findings rather than incidents;
- an Indeterminate Full result makes a paired No Material Change candidate
  inconclusive, not agreement or disagreement;
- no universal accuracy threshold or permanent canary system is required.

The useful measurements are:

- whether important Claims and Questions remain coherent across Revisions;
- whether sampled comparisons expose missed thesis changes;
- tokens per successful Revision, split between bounded assessment and Full
  escalation;
- Full escalation rate, cost, and elapsed time.

The experiment is promising only if quiet updates usually cost materially less
than Full Analysis without making the live thesis visibly less useful in the
reviewed examples. No fixed percentage is chosen before measurements exist.

## Deferred from the first slice

- user-uploaded Source Documents or pasted excerpts;
- incremental execution for United States or mainland Chinese equities;
- scheduled or automatic updates;
- localized reruns of selected analysts or committee stages;
- alternative-chain, Fork, merge, or Primary-promotion UI;
- a complete Claim dependency graph, automated Claim split/merge repair, and
  calibrated numeric confidence;
- production certification, SLAs, automated quarantine, and permanent canary
  infrastructure.

The domain model leaves room for several of these capabilities, but the first
experiment should not implement them merely because they were discussed.

## Excluded scope

Crypto research support and compatibility migration from the v0.4.0 legacy CLI
product line are explicitly excluded rather than deferred. The unreleased
Web/SQLite product line may remove those surfaces instead of carrying
compatibility behavior into the experiment.
