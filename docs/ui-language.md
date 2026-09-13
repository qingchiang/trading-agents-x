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
