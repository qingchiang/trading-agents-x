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
own Research Node. Research Reassessment and Outcome Review may refer to Full
Baseline component identifiers, but those identifiers do not create global
Claims or cross-node lifecycle objects.
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
A Research Node produced from a Full Baseline, all eligible information after
that baseline through a later Analysis Cutoff, a Research Reassessment, a
complete current Research Decision, and any then-observable Outcome Review. It
is a direct child of the baseline, does not consume sibling Incremental Research
Nodes, and cannot establish a Full Baseline. Its fixed product data also
includes a Collection Manifest, Research Coverage, Performance Observation,
Full Research Required result, Method Snapshot, and explicit status for any
nonblocking Outcome Review failure or omission.
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
The required evaluation of how information available after a Full Baseline
affects each of its Decision Components. Every baseline component is classified
as reaffirmed, strengthened, weakened, overturned, or unresolved, with reasons
and Evidence or Collection Manifest references. An overturned core thesis is
one deterministic Full Research Required trigger.
_Avoid_: Outcome review, reflection

**Incremental Evidence**:
Evidence first available after a Full Baseline and no later than an Incremental
Research Node's Analysis Cutoff, including later corrections or restatements of
earlier effective periods. Its provenance is explicit and is not restricted at
the domain level to a data-vendor origin.
_Avoid_: Recent data, delta snapshot

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

**Collection Manifest**:
The deterministic audit of one Incremental Research Run's requested sources and
domains, planned and observed scan intervals, collection outcomes, source
watermarks, sanitized failure classes, and produced Evidence references. It is
the collection record from which Research Coverage is assessed, not the
coverage judgment itself. A source outcome is complete with records, complete
and empty, partial, unavailable, failed, not queried, or not applicable; an
empty result is complete only when the scanned interval is proven.
_Avoid_: Research coverage, source state machine

**Information Advancement**:
New admissible input sufficient to justify an Incremental Research Node. It may
be new Evidence, a proven complete source scan with no matching records, or a
Full Baseline component that has newly become reviewable; Performance is a
derived result rather than a separate advancement, and unavailable, failed, or
unqueried results alone do not advance information.
_Avoid_: Nonempty evidence, elapsed time

**Research Coverage**:
The explicit account of which required research domains and sources were or
were not represented by admissible Evidence or proven collection. Each
domain is Required or Advisory and is classified as complete, limited, missing,
or not applicable. Limited or missing Required coverage produces a
deterministic Full Research Required reason; Advisory gaps do not do so alone.
_Avoid_: Data availability, confidence

**Outcome Review**:
A retrospective LLM evaluation of existing Full Baseline Decision Components
using current Incremental Evidence and Performance Observation, without
consuming sibling conclusions or inventing criteria absent from the baseline.
Each reviewed component is supported, contradicted, mixed, not yet observable,
or not evaluable, with the reason for any inconclusive state.
_Avoid_: Research reassessment, memory, settlement

**Performance Observation**:
A deterministic measurement of a Listed Instrument's Vendor-adjusted Return,
its Benchmark Price-index Returns, and their Reported Benchmark Differences
over an explicitly bounded interval. It records requested cutoffs and the
latest completed market sessions whose valid closes were available by the Run's
Information Cutoff At; each benchmark is independently available, and the
result may inform Outcome Review and Incremental Synthesis without becoming
external Evidence.
_Avoid_: Reflection, verdict

**Performance Calculation Record**:
The immutable account of a Performance Observation's exact endpoint values,
price bases, relevant adjustments or corporate actions, formula, unrounded
results, provider identities, and retrieval times. It closes the calculation's
audit trail without requiring a copy of the complete daily price series.
_Avoid_: Rounded display value, full price history

**Performance Reference**:
A typed reference from a Decision Component, Research Reassessment, or Outcome
Review to a Performance Calculation Record. It is distinct from an Evidence
reference and cannot satisfy Research Coverage.
_Avoid_: Evidence reference, embedded calculation

**Performance Reference Closure**:
The requirement that every committed Performance Reference resolves to the
same Research Node's sealed and successful Performance Calculation Record. A
closure violation prevents Atomic Research Commit.
_Avoid_: Cross-node calculation reference, dangling performance reference

**Not Yet Observable Performance**:
The Performance Observation state in which its start and end cutoffs resolve
to the same eligible completed market session. It produces no return value and
does not by itself constitute Information Advancement.
_Avoid_: Zero return, unavailable performance

**Performance Component Status**:
The state of one Vendor-adjusted Return, Benchmark Price-index Return, or
Reported Benchmark Difference: calculated, not yet observable, unavailable, or
failed. The Performance Observation is complete, partial, or unavailable as
derived from its component states rather than from an ambiguous empty value.
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
prices of one Vendor-adjusted Return. An explicitly disclosed fallback may
select the vintage, but endpoint stitching or mid-interval basis changes are
invalid.
_Avoid_: Mixed-provider series, baseline price snapshot

**Performance Benchmark**:
A named price index used to contextualize a Listed Instrument's Vendor-adjusted
Return. Its stable product identity is independent of provider symbols and
routes; every supported market has one Core Benchmark and one Focus Benchmark,
with independent availability and no ETF substitution or cross-provider series
stitching.
_Avoid_: Provider ticker, ETF proxy, total-return index

**Core Benchmark**:
The broad-market Performance Benchmark: TOPIX for Japan, the S&P 500 for the
United States, and the CSI 800 for mainland China.
_Avoid_: Focus benchmark, universal benchmark

**Focus Benchmark**:
The secondary Performance Benchmark representing a narrower segment or style:
the JPX Prime 150 for Japan, the Nasdaq 100 for the United States, and the CSI
STAR & CHINEXT 50 for mainland China. It complements rather than replaces the
Core Benchmark.
_Avoid_: Core benchmark, peer group, sector benchmark

**Benchmark Price-index Return**:
The change in a Performance Benchmark's official price-index level over the
same actual sessions selected for the Listed Instrument.
_Avoid_: Total-return index return, ETF return

**Reported Benchmark Difference**:
The arithmetic difference between a Vendor-adjusted Return and a Benchmark
Price-index Return over the same actual sessions. It is descriptive, may mix
adjustment bases, and is neither Alpha nor a like-for-like excess return.
_Avoid_: Alpha, benchmark-relative return, excess return

**Full Research Required**:
An explicit warning on an Incremental Research Node that an independent Full
Research Node is needed because incremental interpretation is no longer a
sufficient foundation. The warning does not prevent the node from being a Cycle
Head or its decision from being Primary Research, and it does not grant Full
Baseline eligibility. Its structured reasons are the union of non-removable
deterministic reasons and semantic reasons added by Incremental Synthesis, with
each reason carrying its origin and supporting Evidence or Collection Manifest
references.
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
The market-local date beyond which information is inadmissible to a Research
Node. A Research Cycle contains at most one active Incremental Research Node
for that date, while different cycles may contain nodes at the same cutoff and
multiple Full Research Nodes may independently share a cutoff. Creating an
Incremental Research Node does not require a cutoff later than the Cycle Head.
_Avoid_: Run time, creation time

**Information Cutoff At**:
The single precise instant bounding every admissible input to a Research Run,
including Evidence and market closes. It is market-local day-end for a
historical Analysis Cutoff or is fixed immediately before current-date research
begins; Performance sessions derive from it rather than introducing another
cutoff, and a future Analysis Cutoff is invalid.
_Avoid_: Performance cutoff, run completion time

**Manual Update Request**:
An explicit request to extend a selected Research Cycle to a later Analysis
Cutoff. It is the only current trigger for incremental research.
_Avoid_: Schedule, settlement job

**Method Snapshot**:
The immutable, non-secret account of the research schema, application and
prompt versions, model and provider settings, enabled roles, data-routing and
coverage policy, language, thresholds, and configuration fingerprint used by a
Research Node. It supports audit and comparison without promising exact
replay.
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
