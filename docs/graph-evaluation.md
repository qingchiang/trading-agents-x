# Research Graph evaluation and release gates

TradingAgentsX separates deterministic contract auditing from recorded model
quality. Offline fixtures can prove that a result obeys the Evidence, table,
point-in-time, arithmetic, and research-boundary contracts. They cannot prove
that one prompt or graph produces better research than another.

Resource metrics are recorded for every real evaluation. During the V2
quality-first phase, token usage, LLM-call count, and wall time are descriptive
baselines only and never pass/fail thresholds.

## Two fixture layers

### Deterministic contract fixtures

`evals/fixtures/` contains versioned US, Japanese, China A-share, and crypto
cases. Each market covers bullish, bearish, mixed, missing-data, and historical
scenarios. Default tests load and audit these fixed inputs without invoking a
model, executing a fake profile matrix, or inventing timing/token measurements.

The audits cover:

- sealed Evidence refs, actual sources, fallback provenance, and market-local
  visibility dates;
- raw `EvidenceTable` integrity and exclusion from model/report contexts;
- valid report footnotes and report-level/claim-level source references;
- reproducible decision-critical `CalculationRecord` formulas and inputs;
- nested JSON, fallback sentinels, and malformed artifacts;
- current-evidence versus `memory:*` reference separation;
- rating consistency, seeded-risk recall, and the research-only boundary.

Run the offline layer with:

```bash
PYTHON_DOTENV_DISABLED=1 pytest -q tests/evals
```

### Curated quality fixtures

`evals/quality_fixtures/research_v2.json` defines 20 cross-market quality
scenarios: four markets times bullish, bearish, mixed, missing-data, and
historical cases. The preparation step expands it into:

- 75 role-local frozen Analyst inputs: Market, Social, News, and Fundamentals
  for stocks, and Market, Social, and News for crypto;
- one role-local sealed EvidenceBundle and deterministic EvidenceTable per
  Analyst;
- 20 combined EvidenceBundles for graph comparison;
- expected ratings and deliberately seeded risks.

Generate a sealed suite and print its call plan without making any model call:

```bash
python scripts/run_graph_evaluation.py prepare-quality \
  --spec evals/quality_fixtures/research_v2.json \
  --output /tmp/tradingagents-eval/quality-suite.json

python scripts/run_graph_evaluation.py plan \
  --suite /tmp/tradingagents-eval/quality-suite.json
```

With three repetitions, the complete suite contains 630 recorded outputs and
the following primary logical calls:

| Variant | Calls |
| --- | ---: |
| `main_analyst` | 225 |
| `v2_analyst` | 225 |
| `main_medium` | 1,080 |
| `v2_standard` | 480 |
| `v2_deep` | 600–840 |
| **Total** | **2,610–2,850** |

The range comes from zero to two optional additional Deep rebuttal rounds.
Bounded schema/JSON recovery calls are not included. Because this is a large
paid matrix, printing the plan is not authorization to execute it.

## Comparison identity

The recorded matrix has five variants:

1. `main_analyst`: the old Analyst prompt for each matching frozen role input;
2. `v2_analyst`: the Markdown-first Analyst report and audit extraction over
   the same input;
3. `main_medium`: the exact old Medium debate/risk topology;
4. `v2_standard`: the production V2 Standard deliberation topology;
5. `v2_deep`: the production V2 Deep deliberation topology.

The fixed baseline commit for this V2 comparison is:

```text
b2821a506f9f7c80f8c4b5285fbef0304032eb1a
```

The baseline adapter lives in `evals/adapters/`, outside the production Python
package. It refuses a worktree that is not exactly the requested commit.
Production code therefore does not ship the old graph or old prompts.

Old tool-calling Analysts receive the frozen role transcript on their final
report turn and are prohibited from making another tool call. The News
prefetch is disabled for that isolated turn. The old Sentiment prompt receives
the frozen source through its existing market-specific prefetch contract.
These controls preserve the old prompt/report behavior while preventing a
baseline evaluation from contacting live data sources.

Graph comparison uses one predeclared, zero-severe V2 Analyst repetition as the
approved report set. The same complete AnalystReports and combined
EvidenceBundle are then supplied to `main_medium`, `v2_standard`, and
`v2_deep`. `ResearchGraph.execute_frozen()` uses the production deliberation
nodes and prompts while skipping data tools and Analyst generation.

Every record retains:

```text
variant and case/repetition identity
exact baseline or current commit
provider, quick/deep models, reasoning, language, and temperature
stable prompt-contract SHA-256, dynamic runtime-prompt-trace SHA-256,
Evidence digest, and output SHA-256
complete Evidence, reports, decision, and visible artifacts
deterministic issues and seeded-risk recall
real LLM calls, input/output tokens, and wall time
```

The prompt-contract hash covers the frozen input plus the exact source/prompt
versions at the recorded commit and must remain stable across repetitions. The
runtime trace hash covers prompts actually sent during the dynamic debate and
may vary as earlier role outputs and Deep stopping decisions vary. Prompt
bodies and raw provider messages are not persisted. Provider secrets, headers,
hidden reasoning, and authorization material must never be captured.

## Opt-in execution

Real-model commands require both `--execute` and
`RUN_LIVE_LLM_EVALS=1`. Before setting them, record the provider, models,
reasoning settings, temperature, language, call plan, and expected billing, and
obtain explicit authorization.

Create a detached worktree at the exact baseline commit:

```bash
git worktree add --detach /tmp/tradingagents-main-b282 \
  b2821a506f9f7c80f8c4b5285fbef0304032eb1a
```

The following commands show the execution sequence. Replace the angle-bracket
values only after authorization, and use identical settings in every command.

```bash
RUN_LIVE_LLM_EVALS=1 python evals/adapters/main_medium.py \
  --worktree /tmp/tradingagents-main-b282 \
  --expected-commit b2821a506f9f7c80f8c4b5285fbef0304032eb1a \
  --suite /tmp/tradingagents-eval/quality-suite.json \
  --output-dir /tmp/tradingagents-eval/main-analyst \
  --mode analyst \
  --provider <provider> --quick-model <model> --deep-model <model> \
  --quick-reasoning <effort> --deep-reasoning <effort> \
  --output-language <language> --temperature <temperature> --execute

RUN_LIVE_LLM_EVALS=1 python scripts/run_graph_evaluation.py run-v2-analyst \
  --suite /tmp/tradingagents-eval/quality-suite.json \
  --output-dir /tmp/tradingagents-eval/v2-analyst \
  --provider <provider> --quick-model <model> --deep-model <model> \
  --quick-reasoning <effort> --deep-reasoning <effort> \
  --output-language <language> --temperature <temperature> --execute

python scripts/run_graph_evaluation.py freeze-graph-inputs \
  --suite /tmp/tradingagents-eval/quality-suite.json \
  --records /tmp/tradingagents-eval/v2-analyst/records.jsonl \
  --repetition 1 \
  --output /tmp/tradingagents-eval/graph-suite.json

RUN_LIVE_LLM_EVALS=1 python evals/adapters/main_medium.py \
  --worktree /tmp/tradingagents-main-b282 \
  --expected-commit b2821a506f9f7c80f8c4b5285fbef0304032eb1a \
  --suite /tmp/tradingagents-eval/graph-suite.json \
  --output-dir /tmp/tradingagents-eval/main-medium \
  --mode medium \
  --provider <provider> --quick-model <model> --deep-model <model> \
  --quick-reasoning <effort> --deep-reasoning <effort> \
  --output-language <language> --temperature <temperature> --execute

RUN_LIVE_LLM_EVALS=1 python scripts/run_graph_evaluation.py run-v2-standard \
  --suite /tmp/tradingagents-eval/graph-suite.json \
  --output-dir /tmp/tradingagents-eval/v2-standard \
  --provider <provider> --quick-model <model> --deep-model <model> \
  --quick-reasoning <effort> --deep-reasoning <effort> \
  --output-language <language> --temperature <temperature> --execute

RUN_LIVE_LLM_EVALS=1 python scripts/run_graph_evaluation.py run-v2-deep \
  --suite /tmp/tradingagents-eval/graph-suite.json \
  --output-dir /tmp/tradingagents-eval/v2-deep \
  --provider <provider> --quick-model <model> --deep-model <model> \
  --quick-reasoning <effort> --deep-reasoning <effort> \
  --output-language <language> --temperature <temperature> --execute
```

## Blinded review and gates

Use [quality rubric v1](../evals/rubrics/quality-v1.md). Candidate identities
and resource metrics remain hidden until scores are locked. The same reviewer
or adjudicated panel ID and rubric version must cover the entire matrix.
`EvalReview` rows stay separate from generated records; see
`evals/rubrics/review.example.jsonl`.

Join all five record sets only after review:

```bash
python scripts/run_graph_evaluation.py materialize \
  --records /tmp/tradingagents-eval/main-analyst/records.jsonl \
  --records /tmp/tradingagents-eval/v2-analyst/records.jsonl \
  --records /tmp/tradingagents-eval/main-medium/records.jsonl \
  --records /tmp/tradingagents-eval/v2-standard/records.jsonl \
  --records /tmp/tradingagents-eval/v2-deep/records.jsonl \
  --reviews /tmp/tradingagents-eval/reviews.jsonl \
  --output /tmp/tradingagents-eval/measurements.jsonl

python scripts/run_graph_evaluation.py gate \
  --measurements /tmp/tradingagents-eval/measurements.jsonl
```

The evaluator rejects incomplete repetitions, prompt drift between repetitions,
mismatched frozen evidence, mixed model/settings/reviewer identities, duplicate
records, and mixed baseline or current commits.

Hard release gates are:

| Gate | Requirement |
| --- | --- |
| Deterministic regressions | zero severe issues in V2 Analyst, Standard, and Deep |
| V2 Analyst quality | every rubric dimension median is no lower than main Analyst |
| Standard quality | every rubric dimension median is no lower than main Medium |
| Deep quality | every rubric dimension median is no lower than Standard |
| Deep risk recall | median at least 10 percentage points above Standard |

The four scored dimensions are factual completeness, analytical depth, table
readability, and decision utility. LLM calls, tokens, and wall time appear in
the result summary but do not affect `passed`.

No quality claim is valid until this real, same-setting matrix has been
recorded and reviewed. Outcome settlement over five aligned intervals is useful
short-term feedback, but it is not a substitute for the research-quality
comparison.
