# Graph Evaluation and Release Gates

TradingAgentsX separates deterministic contract evaluation from measured LLM
performance. Passing offline fixtures does not prove quality, token, latency,
or risk-recall improvements.

## Fixed contract suite

`evals/fixtures/` contains versioned US, Japanese, China A-share, and crypto
evidence cases. Each market covers:

- bullish;
- bearish;
- mixed;
- missing-data;
- historical-date scenarios.

The tests exercise Fast, Standard, and Deep three times per scenario without
calling an LLM or network source. They verify graph-facing contracts such as:

- every evidence reference resolves in the sealed bundle;
- exact figures have supporting evidence references;
- usable evidence identifies its actual source;
- fallback evidence retains provenance;
- effective and market-local availability dates do not cross the cutoff;
- research decisions contain no account sizing, entry, stop, target, order, or
  portfolio-weight instructions;
- expected rating and seeded risk terms remain consistent.

Run them with:

```bash
PYTHON_DOTENV_DISABLED=1 pytest -q tests/evals
```

The exact test path may be included in the normal `pytest -q` suite; use
`rg --files tests | rg eval` if selecting individual files.

## Recorded model measurements

Performance gates require an external, opt-in run that records
`EvalMeasurement` rows. A valid comparison must use:

- suite version `1`;
- the same model for baseline Standard, current Standard, and current Deep;
- the same case IDs in all three groups;
- exactly repetitions 1, 2, and 3 for every case/profile;
- observed token counts and wall time from the runtime metrics callback;
- deterministic contract evaluation of each output.

Do not synthesize missing timing/token values or substitute fixture execution
time for model execution time.

Each measurement contains:

```text
suite_version
model
case_id
profile
repetition
quality_score
input_tokens
wall_time_seconds
risk_recall
severe_issues
```

Keep raw outputs, evidence bundles, model/provider identity, resolved run
settings, and measurement rows together as the review artifact. Secrets and raw
authorization/provider headers must not be captured.

## Hard release gates

`tradingagents.evals.evaluate_release_gates()` applies:

| Gate | Requirement |
| --- | --- |
| Severe regressions | zero across current Standard and Deep |
| Standard quality | median no lower than baseline Standard |
| Standard input tokens | median at least 30% below baseline |
| Standard wall time | median at least 25% below baseline |
| Deep risk recall | median at least 10 percentage points above current Standard |

The evaluator rejects incomplete repetition matrices, mismatched case sets, and
mixed model identities before calculating gates.

Deep is not releaseable as a supported profile until its risk-recall gate
passes. A failure should lead to prompt/topology adjustment and another recorded
comparison, not a relaxed threshold.

## Interpreting the score

The contract evaluator deducts for severe and warning issues. It is a release
guard, not a calibrated forecast-accuracy score. Outcome settlement is also not
a direct graph benchmark: five aligned intervals are useful short-term feedback,
but they cannot establish long-horizon thesis quality by themselves.

Reviewers should examine both aggregate gates and individual failures,
especially:

- future-visible or non-PIT evidence;
- missing actual-source attribution;
- an exact number supported only by unrelated evidence;
- bullish/bearish language inconsistent with rating;
- missing seeded counterevidence or risks;
- unavailable data treated as a negative or neutral fact.

## Adding a scenario

1. Add the evidence case to the appropriate market fixture.
2. Include a stable case ID, analysis date, expected rating, and seeded risks.
3. Preserve requested/effective/available timing and source/fallback fields.
4. Run the full offline matrix.
5. Add the case to the next baseline/current recorded measurement set.
6. Do not compare a new case set with an older incomplete baseline.
