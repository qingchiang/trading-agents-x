# Research quality rubric v1

This rubric scores recorded model outputs. It does not replace deterministic
schema, point-in-time, evidence-reference, or arithmetic audits. Any severe
deterministic issue remains a release failure regardless of the review score.

## Review protocol

1. The coordinator freezes the model settings, evidence suite, prompt hashes,
   baseline commit, and current commit before generation.
2. Outputs are assigned opaque candidate IDs and presented in randomized order.
   The reviewer must not see the variant name, commit, token usage, call count,
   latency, or prior score.
3. The evidence snapshot and scenario are visible. Analyst candidates are
   compared only with the matching Analyst role. Graph candidates receive the
   same approved Analyst reports and EvidenceBundle.
4. Score every candidate independently before comparing paired candidates.
   Length, number of tables, and assertive language are not quality proxies.
5. Use one reviewer identity for the complete release matrix. A panel may be
   represented by one stable panel ID after resolving disagreements and
   recording its adjudicated scores.
6. Lock the reviews before revealing variant identities or resource metrics.
   Store the resulting `EvalReview` rows separately from generated records.

Use increments of `0.05`. The anchors below describe `0.00`, `0.25`, `0.50`,
`0.75`, and `1.00`; intermediate scores should reflect where the candidate
falls between adjacent anchors.

## Factual completeness

- **1.00:** Captures every material supplied fact, correctly distinguishes
  observed, inferred, unavailable, and contradictory evidence, preserves dates
  and source boundaries, and omits no fact necessary to challenge the result.
- **0.75:** Covers nearly all material evidence with only minor omissions that
  do not change interpretation.
- **0.50:** Covers the main direction but misses one or more material facts,
  comparisons, limitations, or counterexamples.
- **0.25:** Selectively summarizes a small subset of the evidence or treats
  missing coverage as a substantive signal.
- **0.00:** Fundamentally misstates or ignores the supplied evidence.

## Analytical depth

- **1.00:** Explains causal mechanisms, competing interpretations, temporal
  relevance, uncertainty, counterevidence, and conditions that would change the
  view. Analyst reports fully cover their role-specific research dimensions;
  graph outputs resolve or explicitly preserve material disputes.
- **0.75:** Provides sound mechanisms and meaningful counteranalysis, with
  limited gaps in second-order effects or uncertainty calibration.
- **0.50:** Offers plausible interpretation but mostly restates evidence,
  leaves important tensions unresolved, or relies on generic risks.
- **0.25:** Thin narrative, weak causal reasoning, repeated assertions, or
  superficial bull/bear role play.
- **0.00:** No usable analysis beyond unsupported conclusions.

## Table readability

- **1.00:** Tables serve a clear analytical purpose, expose the observations
  needed to independently check the narrative, use understandable labels and
  units, retain evidence links, and distinguish observed from derived values.
- **0.75:** Tables are useful and auditable with minor organization or labeling
  issues.
- **0.50:** A table is present but omits a material comparison, partly repeats
  prose, or requires substantial cross-reading to interpret.
- **0.25:** Tables are decorative, confusing, materially incomplete, or hide
  the relevant comparison.
- **0.00:** Suitable tabular data is not presented, or the table is materially
  misleading.

For a missing-data scenario, a clear coverage/availability table can earn full
credit. Do not reward invented rows or numbers merely to make a table look
complete.

## Decision utility

For Analyst candidates, score how well the report enables a later committee to
form and challenge a research opinion. For graph candidates, score the final
research opinion and its visible deliberation.

- **1.00:** Gives a coherent non-personalized opinion with well-calibrated
  confidence, conditional scenarios, catalysts, risks, invalidation,
  unresolved questions, time horizon, and useful valuation/reference context
  when evidence permits. The reader can see what would improve or weaken the
  view.
- **0.75:** Clearly useful for a decision, with only minor omissions or
  calibration issues.
- **0.50:** Directionally usable but lacks one or more material decision
  conditions, scenario distinctions, or dispute resolutions.
- **0.25:** Generic recommendation, internally inconsistent position, or little
  guidance on what evidence matters next.
- **0.00:** Unusable or misleading research conclusion.

Account-specific sizing, order instructions, mandatory entry/stop/target
language, and guaranteed-return claims are deterministic boundary violations;
they are not compensated by a high utility score.
