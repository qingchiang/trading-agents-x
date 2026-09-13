# Interface language

Interface labels describe research and user actions rather than storage or generation mechanics. Public identifiers, rating enums and persisted report text are unchanged.

| Concept | English | Chinese | Japanese |
| --- | --- | --- | --- |
| Research Reassessment | Reassessment | 逐项重评 | 項目別再評価 |
| Complete Research Decision | Full assessment | 完整研究判断 | 総合判断 |
| Performance Observation | Period performance | 区间表现 | 対象期間の騰落 |
| Cycle Head | Latest research in cycle | 本周期最新研究 | サイクル内の最新リサーチ |
| Run activity | Run progress | 执行进度 | 実行状況 |

Incremental research opens the analysis brief, followed by a separate period-performance section. Reassessment and the complete assessment have distinct views. An unchanged overall assessment can coexist with strengthened or weakened components. Neither an unchanged assessment nor missing optional data implies an absence of new information.

Period changes use the recorded vendor adjustment basis and actual trading sessions. Instrument-minus-benchmark differences are percentage points, never Alpha or total return. Missing and not-yet-observable values are not zero.

Cycle management describes the Full baseline and its owned Incremental research. Related uncommitted tasks are visibly distinct and do not extend lifecycle ownership.

Comparison is a reading mode inside the instrument workspace. Each side retains its research date, direct Full baseline, and Evidence scope. Unknown schema fields belong in diagnostics; their presence must not be presented as unchanged research content. Historical missing, null, and empty values remain distinct.

Cycle menus apply to the Full baseline and its owned research. Task menus apply to the selected run. Lifecycle confirmation uses the server preview and invalidates that preview immediately when a write is rejected.

Shareable reading state uses `node`, `view=compare`, ordered repeated `compare` values, `compare_mode`, and `changed_only`. Run history expansion uses repeated `expanded_group` values. The router keeps library and research return context per history entry; session storage keeps temporary reading positions. Direct links without return context use the library or the selected research's default reading view.

| Concept | English | Chinese | Japanese |
| --- | --- | --- | --- |
| Decision time horizon | Time horizon | 判断期限 | 判断の対象期間 |
| Overall decision outcome | Overall assessment change | 整体判断变化 | 総合判断の変化 |
| Update collection scope | Materials and limitations | 资料与限制 | 資料と制約 |

Reading surfaces share a 960px maximum outer boundary; Markdown fills the available inner width. Structured views declare major outline entries. Process navigation locates stage and report headers, while report navigation uses recorded sections. Historical Markdown headings are scoped to their own body. Ambiguous old numbered sections produce an explicit unavailable-chapter message.

`/runs/:id` defaults to execution progress. Explicit historical reading links normalize to the exact instrument/node with their report, citation, section and Trash context. Legacy and partial artifacts remain accessible through the shared reader. Task lists retain cycle ownership and status, while assessments belong in the instrument workspace.

Incremental Evidence describes only the current update's recorded materials, with a separate baseline Evidence link and return context. Domain filters use recorded references, and source names come from actual Evidence rather than attempted providers. Unknown limitations remain visible. Performance exposes recorded endpoint values, readable provider adjustment basis and minute-resolution UTC retrieval times; full precision remains in diagnostics and exports.
