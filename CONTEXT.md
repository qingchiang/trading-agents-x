# Investment Research Context

This context describes how TradingAgentsX maintains an evidence-grounded
research thesis across repeated assessments of the same Instrument. It
distinguishes durable research state from the executions and reports that
produce it.

## Research lineage

**Instrument**:
A specific listed equity security. An issuer, ticker string, related security,
or crypto asset is not interchangeable with the Instrument.
_Avoid_: Company, ticker

**Research Chain**:
A linear lineage of research about exactly one Instrument, composed of
immutable Research Revisions.
_Avoid_: Rerun history, report thread

**Research Fork**:
An alternative Research Chain whose provenance begins at a Revision in another
chain rather than adding a second head to that chain.
_Avoid_: Chain branch, revision branch

**Primary Research Chain**:
The sole Research Chain for an Instrument whose Current Research State is
presented as the default research opinion.
_Avoid_: Latest chain, active branch

**Research Revision**:
An immutable reassessment in a Research Chain containing the complete resulting
research state and, for an update, a conclusion about change from its predecessor.
_Avoid_: Incremental report, rerun

**Revision Role**:
Whether a Research Revision establishes the first Current Research State or
updates a preceding Revision.
_Avoid_: Execution strategy, change outcome

**Change Conclusion**:
The conclusion of an update Revision relative to its predecessor: Material
Change, No Material Change, or Indeterminate. An initial Revision has no Change
Conclusion.
_Avoid_: Execution strategy, coverage status

**Current Research State**:
The structured thesis established or reaffirmed by the current Research
Revision, including its opinion, Claims, Questions, scenarios, risks,
catalysts, invalidation conditions, and Evidence relationships.
_Avoid_: Latest report, report bundle

**Prior Research**:
Claims, Questions, and opinions from earlier Research Revisions that guide what
must be reassessed but cannot prove themselves correct.
_Avoid_: Historical Evidence

**Research Artifact**:
A human-readable report or deliberation record produced during research but
not itself part of the Current Research State.
_Avoid_: Research state, canonical report

## Thesis state

**Research Opinion**:
An Evidence-grounded assessment of an Instrument's attractiveness,
uncertainty, scenarios, risks, catalysts, and invalidation conditions that
supports the user's own judgment.
_Avoid_: Trade instruction, trading plan

**Research Mandate**:
The shared purpose of assessing an Instrument for the user's own judgment
without account-specific advice or execution decisions.
_Avoid_: Run profile, model configuration

**Research Claim**:
A persistent, atomic, decision-relevant assertion whose standing and confidence
may change across Research Revisions.
_Avoid_: Debate issue, conclusion text

**Primary Claim**:
An active Research Claim explicitly identified as a direct basis of the
Research Opinion.
_Avoid_: Overall opinion, report headline

**Epistemic Kind**:
The nature of a Research Claim as an observation, inference, or forecast.
_Avoid_: Decision role

**Decision Role**:
How a Research Claim contributes to the Research Opinion, such as thesis,
risk, catalyst, invalidation, or scenario assumption.
_Avoid_: Epistemic kind

**Falsifier**:
An observable condition or test that would refute an inference or forecast.
_Avoid_: Generic risk, disclaimer

**Claim Standing**:
Whether a Research Claim is active, invalidated by Evidence or events, or
retired because it is no longer relevant without necessarily being false.
_Avoid_: Strengthened, weakened

**Claim Change**:
How a Research Revision treats a Claim, such as introducing, reaffirming,
strengthening, weakening, invalidating, retiring, or superseding it.
_Avoid_: Claim Standing

**Claim Relationship**:
A typed connection between Research Claims, such as dependency,
contradiction, derivation, or supersession.
_Avoid_: Evidence relationship, free-form link

**Research Question**:
A persistent uncertainty that remains open, becomes answered, is superseded or
retired, and may reopen when later Evidence changes the answer.
_Avoid_: Research Claim, debate issue

**Question Disposition**:
How an update treats a persistent Research Question, such as reaffirming,
answering, reopening, superseding, or retiring it, with applicable current
Evidence and a durable reason. Supersession links the retained Question to a
separately identified successor.
_Avoid_: Question status, omission from a report

**Scenario Likelihood**:
An ordinal assessment of which scenario is more plausible over a shared cutoff
and forward horizon; likelihoods may tie or be indeterminate.
_Avoid_: Scenario confidence, numeric probability

**Scenario Probability**:
An optional numeric likelihood supported by a declared method and auditable
inputs for a complete scenario set over a shared horizon.
_Avoid_: Model guess, Decision Confidence

**Decision Confidence**:
The ordinal confidence in the overall Research Opinion, distinct from the
probability of any scenario.
_Avoid_: Base-case probability

**Claim Confidence**:
The ordinal confidence in a Research Claim, distinct from a numeric
probability.
_Avoid_: Claim probability, model score

## Evidence and coverage

**Evidence**:
Observable source material with explicit timing and provenance that may support
or challenge Research Claims; Prior Research is not Evidence.
_Avoid_: Context, previous conclusion

**Effective Evidence Snapshot**:
The complete sealed Evidence view supporting one Research Revision, including
inherited and newly obtained Evidence with explicit lineage.
_Avoid_: Evidence delta, previous bundle reference

**Source Document**:
An immutable original document supplied for research; citable passages or
structured facts derived from it may become Evidence.
_Avoid_: Attachment Evidence, prompt file

**Source Record**:
The stable identity of an upstream filing, announcement, observation, or other
source-native fact across retrievals and Research Revisions.
_Avoid_: Evidence ref, content hash

**Source Record Version**:
An immutable observed version of a Source Record that preserves corrections,
withdrawals, and replacements rather than overwriting them.
_Avoid_: Overwritten source record

**Research Domain**:
A coherent area of investigation, such as market data, fundamentals, company
announcements, news, social sentiment, or macro conditions.
_Avoid_: Analyst role, provider

**Required Domain**:
A Research Domain that must be checked because an active Claim or open Question
depends on it.
_Avoid_: Selected analyst

**Advisory Domain**:
A Research Domain that may add useful context but is not necessary to reassess
the current Claims and Questions.
_Avoid_: Optional truth, ignored source

**Coverage Attestation**:
An explicit record of which Claims, Questions, and Research Domains were
checked for a Research Revision, together with gaps and limitations.
_Avoid_: Completeness guarantee, completion flag

**Source Watermark**:
A source-specific boundary describing how far collection examined Source
Records; it is not simply the prior analysis date or retrieval time.
_Avoid_: Last analysis date

**Change Relationship**:
The assessed effect of Evidence on a Claim or Question, such as support,
weakening, contradiction, answering, reopening, irrelevance, or uncertainty.
_Avoid_: Sentiment label, Claim Change

## Research updates

**Eligible Baseline**:
The current Revision of a Research Chain when it contains enough state,
Evidence, coverage, and compatible semantics to support a bounded update.
Eligibility is independent of its Change Conclusion; an Indeterminate Revision
is never eligible.
_Avoid_: Latest run, same-ticker result

**Update Intent**:
A request to produce the next Research Revision from a Research Chain's current
head.
_Avoid_: Execution attempt, rerun template

**Research Execution**:
A processing attempt for an Update Intent; an unsuccessful attempt does not
create a Research Revision or change the Current Research State.
_Avoid_: Research Revision, partial revision

**Full Analysis**:
A reassessment strategy used to establish a Research Chain or when a bounded
update cannot justify preserving its Current Research State.
_Avoid_: Objective completeness, default rerun

**Incremental Execution**:
An update strategy that examines new or changed Evidence relative to an
Eligible Baseline and may escalate to Full Analysis.
_Avoid_: Shorter date window, incremental report

**Change Assessment**:
A bounded comparison of new or changed Evidence with the Current Research
State to decide whether it may remain unchanged or requires Full Analysis.
_Avoid_: Headline filter, unrestricted rerun

**Automatic Escalation**:
The transition from Incremental Execution to Full Analysis when the bounded
assessment cannot justify No Material Change.
_Avoid_: Incremental failure, user retry

**Shadow Comparison**:
The experimental comparison of a bounded No Material Change candidate with an
independent Full Analysis conclusion. An Indeterminate Full result is
inconclusive rather than agreement or disagreement.
_Avoid_: Accuracy verdict, Full ground truth

**Material Change**:
A Change Conclusion that a decision-relevant part of the Current Research State
changed; new Evidence alone is not necessarily material.
_Avoid_: Any new data, model impression

**No Material Change**:
A Change Conclusion that advances the research cutoff and Evidence record while
reaffirming the semantic Current Research State.
_Avoid_: No-op, copied revision

**Indeterminate**:
A Change Conclusion used when an update cannot justify either Material Change
or No Material Change. The Revision advances the chain but is not an Eligible
Baseline.
_Avoid_: Coverage incomplete, failed execution

**Update Summary**:
A human-readable account of what an update checked, what changed, its coverage
limitations, and whether it used Incremental Execution or Full Analysis.
_Avoid_: Regenerated full report, status badge

## Outcome feedback

**Outcome Observation**:
A bounded ex-post observation associated with a Research Decision over an
explicit market-local window and declared observation method. It does not by
itself prove or disprove a thesis.
_Avoid_: Thesis result, current Evidence

**Outcome Reflection**:
A limited methodological lesson derived from a Research Decision and its
Outcome Observation. It is neither Prior Research nor Evidence.
_Avoid_: Updated thesis, verified cause

**Outcome Feedback**:
A qualified, auditable methodological lesson associated with a Research
Decision or Research Revision, its Outcome Observation, and an Outcome
Reflection. It becomes available only after the Observation data, Reflection,
and qualification decision all exist; its `available_at` is the latest of
those times.
_Avoid_: Research memory, longitudinal thesis

**Outcome Feedback Context**:
A bounded, point-in-time view of applicable Outcome Feedback selected for a
specific research cutoff. Selection requires eligible Feedback whose declared
instrument or market, research stage, domain, category, and horizon apply to
the target research; the selector is versioned, bounded, and records inclusion
and exclusion reasons. An empty context is valid. It cannot establish Current
Research State, serve as Evidence, satisfy Coverage, or determine a Change
Conclusion.
_Avoid_: Memory context, Prior Research
