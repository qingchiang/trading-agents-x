# Equity Research Timeline

This context describes how research for one listed instrument evolves through
full and incremental analysis without conflating the latest conclusion with an
eligible baseline.

## Language

**Listed Instrument**:
An exchange-listed US, Japanese, or mainland-Chinese equity within the
supported-equity product boundary. It owns one Research Timeline identified by
an Instrument Key.
_Avoid_: Company, ticker

**Instrument Key**:
The canonical normalized yfinance-style ticker that identifies a Listed
Instrument's Research Timeline within the supported-equity boundary. Exchange
suffixes such as `.T`, `.SS`, and `.SZ` carry the relevant market distinction;
market, timezone, vendor route, and benchmark selection are derived metadata,
not additional identity fields. A ticker change starts a distinct Research
Timeline, and ticker reuse is not automatically reconciled.
_Avoid_: Market plus ticker, company name, timezone bucket

**Research Timeline**:
The chronological collection of Research Cycles for a Listed Instrument. It
orders research for presentation without implying a dependency chain between
cycles or between sibling Incremental Research Nodes. It comes into existence
only when the Listed Instrument's first Full Research Node is committed.
_Avoid_: Research chain, memory

**Research Cycle**:
A Full Research Node and the set of Incremental Research Nodes that directly
use it as their Full Baseline. Cycles are independent; one remains open to
later incremental research only while its Full Node is active, and its
Incremental Nodes may be backfilled after the baseline but before its current
Cycle Head.
_Avoid_: Revision chain, branch

**Research Run**:
The lifecycle of one requested research execution, including its attempts and
terminal outcome. It becomes succeeded and gains the role of a Research Node
only through an Atomic Research Commit.
_Avoid_: Research node, revision

**Research Attempt**:
One execution attempt within a Research Run. Retries retain the Run's Analysis
Cutoff, Information Cutoff At, and Method Snapshot rather than admitting newer
information or configuration.
_Avoid_: New run, refreshed research

**Research Node**:
A successfully committed Research Run positioned on a Research Timeline at an
Analysis Cutoff. It shares the Research Run's identity and product data rather
than owning a duplicate snapshot. Its research inputs and outputs are immutable
after commit; Trash state and Primary Research Cycle selection are external
product metadata.
_Avoid_: Revision, copied snapshot

**Research Decision**:
The complete current research judgment produced by a Research Node. Full and
Incremental Research Nodes use the same decision schema so that Primary
Research and Node Comparison do not require separate conclusion models.
_Avoid_: Incremental summary, rating only

**Decision Component**:
A reviewable part of a Research Decision with a stable identifier scoped to its
own Research Node. Research Reassessment may refer to Full Baseline component
identifiers, but those identifiers do not create global Claims or cross-node
lifecycle objects.
_Avoid_: Global claim, question object

**Atomic Research Commit**:
The single transaction that validates and persists a successful Research Run's
required product data, Research Node role, and Timeline relationships. A commit
failure leaves a failed Research Run and no Research Node.
_Avoid_: Promotion, eventual timeline linking

**Active Research Node**:
A Research Node that has not been placed in Trash. Only active nodes can serve
as a Full Baseline, Cycle Head, or Primary Research.
_Avoid_: Retained node, completed run

**Trash**:
The reversible state of a retained Research Node that removes it from active
Timeline roles without purging its immutable data. An Incremental Node may
enter Trash independently; a Full Node takes its Research Cycle with it, and
restoration cannot violate active same-Cycle, same-cutoff uniqueness.
_Avoid_: Delete, purge, archive

**Permanent Purge**:
The irreversible removal of a Research Node and its owned data. Purging a Full
Node necessarily purges its entire Research Cycle.
_Avoid_: Trash, hide

**Full Research Node**:
A Research Node produced by an independent, comprehensive analysis that does
not consume prior research conclusions or reviews. It establishes a Full
Baseline.
_Avoid_: Initial node, anchor

**Incremental Research Node**:
A Research Node produced from a Full Baseline, genuinely new admitted
information, a Research Reassessment, and a complete current Research Decision.
The new information may be PIT Evidence, Near-live Advisory Evidence, or a newly
completed stock session used by the current product. The Node is a direct child
of the baseline, does not consume siblings, and cannot establish a Full
Baseline; its fixed products also include Collection Summary, Research
Availability, Performance Observation, Full Research Required, and Method
Snapshot.
_Avoid_: Delta, patch, memory update

**Cycle Head**:
The active Research Node with the latest Analysis Cutoff in one Research Cycle.
It is the Full Research Node until that cycle has an active Incremental Research
Node.
_Avoid_: Timeline head, full baseline

**Primary Research Cycle**:
The user-selected Research Cycle used for default product views and actions. It
does not restrict or change research in other cycles.
_Avoid_: Current cycle, only active cycle

**Primary Research**:
The Research Decision represented by the Primary Research Cycle's Cycle Head.
_Avoid_: Current research, latest research

**Node Comparison**:
A deterministic, read-only comparison of any two Research Nodes in the same
Research Timeline that have not been permanently purged. An explicitly selected
node in Trash may participate; comparison does not create research or change
either node, and Performance Observations retain their own intervals rather
than becoming directly ranked or differenced.
_Avoid_: Comparative run, comparison node

**Comparison Alignment**:
The correspondence used by Node Comparison: fixed Research Schema sections
align across Nodes, while an Incremental Node's explicit baseline-component
references align within its Cycle. Free-form components from different Cycles
remain side by side rather than being semantically matched.
_Avoid_: Global component identity, inferred claim match

**Method Changed**:
The condition in which compared Nodes have different Method Snapshots. It does
not prevent comparison, but their Research Decision differences are not
attributed automatically to either Evidence or method changes.
_Avoid_: Incomparable, evidence-caused change

**Not Recorded Under This Schema**:
The comparison state for a field that did not exist in a Node's Research Schema
Version, distinct from unavailable, not applicable, empty, unchanged, or an
unsupported comparison.
_Avoid_: Missing data, null, unchanged

**Full Baseline**:
The Full Research Node at the root of a Research Cycle from which that cycle's
Incremental Research Nodes are produced.
_Avoid_: Cycle head, previous node

**Research Reassessment**:
The required evaluation of how newly admitted information affects each Full
Baseline Decision Component. Every baseline component is classified as
reaffirmed, strengthened, weakened, overturned, or unresolved, with a concise
reason grounded in admitted information and disclosed limitations. An
overturned core thesis is one Full Research Required trigger.
_Avoid_: Outcome review, reflection

**Incremental Evidence**:
New Evidence admitted for one Incremental Research Node. It is either strict
PIT Evidence inside the Full-Baseline-to-target information window or explicitly
bounded Near-live Advisory Evidence; its provenance and temporal basis remain
visible.
_Avoid_: Recent data, delta snapshot, historical replay

**PIT Evidence**:
Evidence whose reliable availability semantics establish that it was publicly
knowable no later than the Research Run's Information Cutoff At. A later
correction may qualify even when its Effective Date precedes the Full Baseline.
_Avoid_: Retrieval-time snapshot, period-end-only data

**Near-live Advisory Evidence**:
An explicitly non-PIT retrieval-time observation admitted only when the target
Analysis Cutoff is between zero and five market-local calendar days old. It may
inform research but cannot prove historical completeness, historical absence,
or strict historical availability for its domain.
_Avoid_: PIT Evidence, historical snapshot, replayable evidence

**Evidence Available At**:
The earliest reliably established instant at which Evidence was publicly
available. A date-only release is conservatively available at the relevant
market-local day-end unless a finer reliable timestamp exists.
_Avoid_: Effective date, retrieval time, guessed publication time

**Sealed Evidence Bundle**:
The immutable Evidence snapshot owned by one Research Run and therefore by its
same-identity Research Node. An Incremental Research Node's bundle contains
only its Incremental Evidence and does not copy the Full Baseline's bundle.
_Avoid_: Shared evidence store, inherited snapshot

**Evidence Reference Closure**:
The requirement that every Evidence reference in committed research resolves
within its permitted sealed bundles. Full research may reference only its own
bundle; Incremental research may reference only its own bundle and its Full
Baseline's bundle, never a sibling Node or unsealed Evidence.
_Avoid_: Best-effort citation, cross-cycle evidence link

**Collection Summary**:
The immutable account of the data sources actually used by one Incremental
Research Run, the observations admitted from them, and their material
limitations or failures. It describes actual results rather than certifying
every configured provider, an exhaustive scan, or historical absence.
_Avoid_: Collection Manifest, provider attempt ledger, completeness proof

**Information Advancement**:
New admissible information sufficient to justify an Incremental Research Node:
at least one new PIT or qualified Near-live Advisory observation, or a newly
completed stock-market session used by the current research product. Elapsed
time, a repeated observation changed only by retrieval time, provider failure,
and an unproven empty feed do not advance information.
_Avoid_: Complete-empty proof, elapsed time, successful request alone

**Research Availability**:
The disclosed breadth of usable information obtained for one Research Run,
classified by research domain as available, limited, or missing. It communicates
input limitations without claiming exhaustive provider or historical coverage.
_Avoid_: Research Coverage, source certification, confidence score

**Performance Observation**:
A deterministic measurement of a Listed Instrument's Vendor-adjusted Return
over an explicitly bounded interval, with independently optional Benchmark
Context. It records the actual completed market sessions and price-series basis
and may inform Incremental Synthesis without becoming external Evidence.
_Avoid_: Reflection, verdict

**Performance Calculation Record**:
The immutable account of a Performance Observation's endpoint values, sessions,
price basis, formula, provider identity, retrieval time, and result. It preserves
the calculation's provenance without requiring a copy of the complete series.
_Avoid_: Rounded display value, full price history

**Not Yet Observable Performance**:
The Performance Observation state in which its start and end cutoffs resolve
to the same eligible completed market session. It produces no return value and
does not by itself constitute Information Advancement.
_Avoid_: Zero return, unavailable performance

**Performance Component Status**:
The state of the stock return or one optional benchmark context: calculated,
not yet observable, or unavailable. A transport or provider failure is retained
as a limitation and makes only its affected component unavailable.
_Avoid_: Null result, overall run status

**Research Market Series**:
The explicitly disclosed provider-adjusted market-price series used as Evidence
for research interpretation and technical indicators. It may supply a
Vendor-adjusted Return, but only a sealed Performance Calculation Record makes
that change a Performance Observation.
_Avoid_: Unified return series, unrecorded performance

**Vendor-adjusted Return**:
The change in a Listed Instrument's Research Market Series under its explicitly
named provider, adjustment basis, and Adjustment Vintage. It is not assumed
comparable across providers or markets and is neither Price Return nor Total
Return.
_Avoid_: Price return, total return, raw return

**Adjustment Vintage**:
The provider, adjustment basis, and retrieval instance shared by both endpoint
prices of one Vendor-adjusted Return. A broader series may be truncated locally,
but endpoint stitching or mid-interval basis changes are invalid.
_Avoid_: Mixed-provider series, baseline price snapshot

**Benchmark Context**:
An optional named index return displayed beside a Listed Instrument's
Vendor-adjusted Return for the same interval where compatible endpoints are
available. It is independently unavailable and is not required for an
Incremental Research Node.
_Avoid_: Required benchmark, peer ranking, ETF substitution

**Reported Benchmark Difference**:
The optional arithmetic difference between a Vendor-adjusted Return and a
Benchmark Context over compatible actual sessions. It is descriptive and is
neither Alpha nor a like-for-like excess return.
_Avoid_: Alpha, benchmark-relative return, excess return

**Full Research Required**:
An explicit warning on an Incremental Research Node that an independent Full
Research Node is needed because incremental interpretation is no longer a
sufficient foundation. The warning does not prevent the node from being a Cycle
Head or its decision from being Primary Research, and it does not grant Full
Baseline eligibility. Its structured reasons explain a material thesis change,
identity uncertainty, unreliable attribution, or another limitation that makes
the bounded update insufficient; missing optional data alone is not such a
reason.
_Avoid_: Automatic escalation, failed update

**Cycle Warning**:
A derived warning that exists while any active Incremental Research Node in a
Research Cycle carries Full Research Required.
_Avoid_: Node warning, timeline warning

**Timeline Warning**:
A derived warning that mirrors the Primary Research Cycle's Cycle Warning.
Changing the Primary Research Cycle changes the Timeline Warning without
altering cycle or node warnings.
_Avoid_: Cycle warning, inherited node warning, baseline failure

**Analysis Cutoff**:
The market-local target date of a Research Node. Strict PIT Evidence must be
knowable by its corresponding Information Cutoff At; explicitly bounded
Near-live Advisory Evidence is the only non-PIT exception. A Research Cycle
contains at most one active Incremental Research Node for that date, while
different cycles may contain nodes at the same cutoff and multiple Full Nodes
may independently share a cutoff.
_Avoid_: Run time, creation time

**Information Cutoff At**:
The precise instant bounding strict PIT inputs and completed market sessions
for a Research Run. It is market-local day-end for a historical Analysis Cutoff
or is fixed immediately before current-date research begins; a future Analysis
Cutoff is invalid. Near-live Advisory Evidence retains its later retrieval time
and never masquerades as PIT Evidence at this cutoff.
_Avoid_: Performance cutoff, run completion time

**Manual Update Request**:
An explicit request to extend a selected Research Cycle to a later Analysis
Cutoff. It is the only current trigger for incremental research.
_Avoid_: Schedule, settlement job

**Method Snapshot**:
The immutable, non-secret account of the research schema, application and
prompt versions, model and provider settings, enabled roles, data-routing and
data-availability policy, language, thresholds, and configuration fingerprint
used by a Research Node. It supports audit and comparison without promising
exact replay.
_Avoid_: Secret-bearing config, reproducibility guarantee

**Execution History**:
The operational view of all Research Runs, including failed, cancelled, Legacy,
and successfully committed runs. A successful post-redesign run links to its
same-identity Research Node; Execution History is not a second research
timeline.
_Avoid_: Research timeline, product history

**Legacy Research Run**:
A run created before the Research Timeline redesign. It remains readable in
Execution History but is not a Research Node, cannot be a Full Baseline, and is
not automatically converted.
_Avoid_: Imported node, legacy baseline

**Research Schema Version**:
The version of the post-redesign Research Node product-data contract. Every
retained post-redesign Full Research Node remains baseline-compatible while
active; Legacy Research Runs are outside this contract.
_Avoid_: Application version, prompt version
