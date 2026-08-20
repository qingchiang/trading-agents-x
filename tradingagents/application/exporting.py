"""Render self-contained, explicit run exports from durable contracts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tradingagents.application.markdown_evidence import normalize_evidence_markdown
from tradingagents.application.numeric_display import format_decision_number

from .contracts import (
    AnalystReport,
    DebateAgenda,
    DecisionBrief,
    DecisionNumericAuditAppendix,
    EvidenceTable,
    JudgeDraft,
    RebuttalReview,
    ReportLanguage,
    ResearchArtifactContent,
    ResearchCase,
    ResearchDecision,
    ResearchWarning,
    RiskReview,
    RunExport,
)
from .evidence import group_evidence_by_content


@dataclass(frozen=True)
class ExportLabels:
    """Deterministic application-owned labels for a readable export."""

    values: Mapping[str, str]
    language: str = "en"

    def __getitem__(self, key: str) -> str:
        return self.values[key]

    def report_name(self, key: str) -> str:
        return self.values.get(f"report.{key}", key.title())

    def enum_name(self, namespace: str, value: str) -> str:
        return self.values.get(f"{namespace}.{value}", value)


_EN_LABELS = {
    "title": "TradingAgentsX Research",
    "export_schema": "Export schema",
    "run": "Run",
    "status": "Status",
    "attempt": "Attempt",
    "reports": "Reports",
    "no_reports": "No final reports were recorded.",
    "research_process": "Research Process",
    "no_process": "No deliberation artifacts were recorded for this run.",
    "decision_brief": "Decision Synthesis Brief",
    "decision_brief_notice": (
        "Non-final reasoning draft. It has not passed the Final decision contract."
    ),
    "research_decision": "Research Decision",
    "no_decision": "No final decision was recorded.",
    "warnings": "Warnings",
    "no_warnings": "No structured warnings were recorded.",
    "performance": "Performance",
    "attempts": "Attempts",
    "no_attempts": "No attempt metrics were recorded.",
    "sources": "Sources",
    "no_evidence": "No sealed EvidenceBundle was recorded for this run.",
    "raw_tables": "Raw Evidence Tables",
    "evidence_items": "Evidence Items",
    "content": "Content",
    "audit_records": "Audit records",
    "structured_recoveries": "Structured Recoveries",
    "no_recoveries": "No successful structured recoveries were recorded.",
    "initial_reason": "Initial reason",
    "recovery_method": "Recovery method",
    "validation_issues": "Validation issues",
    "retry_count": "Extra calls",
    "recovered_at": "Recovered at",
    "unverified_numeric": "Unverified Numeric Drafts",
    "numeric_audit_gaps": "Unverified Derived Values",
    "decision_requirement_audit": "Decision-Critical Calculation Audit",
    "requirement_comparisons": "Requirement Comparisons",
    "requirement_id": "Requirement",
    "structured_value": "Structured display value",
    "canonical_result": "Canonical result",
    "display_scale": "Display scale",
    "comparison_precision": "Decimal places",
    "rounded_comparison": "Rounded display / result",
    "calculation_status": "Calculation status",
    "display_status": "Display status",
    "comparison_not_recorded": "This run did not record requirement comparison details.",
    "numeric_warning": (
        "The following model-proposed numeric content did not pass audit and "
        "was not used in the canonical research decision."
    ),
    "numeric_gap_warning": (
        "The following decision-critical derived values were not fully verified. "
        "The qualitative decision is retained, but these values are excluded from canonical calculations."
    ),
    "omitted_components": "Omitted Components",
    "executive_summary": "Executive Summary",
    "thesis": "Thesis",
    "scenarios": "Scenarios",
    "base": "Base",
    "bull": "Bull",
    "bear": "Bear",
    "core_assumptions": "Core Assumptions",
    "scenario_reference_range": "Scenario reference range",
    "category.technical": "Technical",
    "category.historical": "Historical",
    "category.analyst_consensus": "Analyst consensus",
    "category.fundamental": "Fundamental",
    "category.other": "Other",
    "omission.appendix": "Numeric appendix",
    "omission.calculation": "Calculation",
    "omission.scenario_range": "Scenario reference range",
    "omission.valuation": "Valuation assessment",
    "omission.market_reference": "Market reference",
    "omission.decision_claim": "Decision-critical derived value",
    "endpoint_basis": "Endpoint basis",
    "endpoint_dates": "Endpoint dates",
    "basis.observed": "direct observation",
    "basis.interpreted": "research interpretation",
    "basis.derived": "formula-derived",
    "valuation_assessment": "Valuation Assessment",
    "market_references": "Market Reference Levels",
    "calculations": "Decision-Critical Calculations",
    "catalysts": "Catalysts",
    "risks": "Risks",
    "invalidation": "Invalidation Conditions",
    "unresolved": "Unresolved Questions",
    "risk_response": "Final Committee Response to Risk Review",
    "no_calculations": "No decision-critical calculations were recorded.",
    "no_adjustments": "No risk-review adjustments were recorded.",
    "debate_agenda": "Debate Agenda",
    "material_issues": "Material Issues",
    "key_claim_audit": "Key Claim Audit",
    "artifact": "Artifact",
    "schema": "Schema",
    "prompt": "Prompt",
    "generation": "Generation",
    "generation_observation": "Generation path",
    "created": "Created",
    "round": "Round",
    "source": "source",
    "evidence": "Evidence",
    "bundle_version": "Bundle version",
    "digest": "Digest",
    "analysis_date": "Analysis date",
    "table": "Table",
    "purpose": "Purpose",
    "rows": "Rows",
    "raw_data": "Raw data",
    "refs": "Refs",
    "source_list": "Sources",
    "type": "Type",
    "quality": "Quality",
    "fallback": "Fallback",
    "analyst": "Analyst",
    "audit": "Audit",
    "confidence": "Confidence",
    "implication": "Implication",
    "rating": "Rating",
    "time_horizon": "Time horizon",
    "numeric_audit": "Numeric audit",
    "usage_note": (
        "Usage is the cumulative amount observed and persisted by this application. "
        "A hard process crash can prevent the final provider callback from being recorded."
    ),
    "llm_calls": "LLM calls",
    "tool_calls": "Tool calls",
    "input_tokens": "Input tokens",
    "output_tokens": "Output tokens",
    "cache_hit": "Cache hit",
    "cache_miss": "Cache miss",
    "reasoning": "Reasoning",
    "detailed_calls": "Detailed calls",
    "wall_time": "Wall time",
    "node": "Node",
    "resumes": "Resumes",
    "error": "Error",
    "method": "Method",
    "range": "Range",
    "as_of": "As of",
    "temporal_basis": "Temporal basis",
    "input_evidence": "Input evidence",
    "value": "Value",
    "basis": "Basis",
    "formula": "Formula",
    "inputs": "Inputs",
    "result": "Result",
    "used_by": "Used by",
    "limitations": "Limitations",
    "status_label": "Status",
    "reason": "Reason",
    "schema_valid": "Schema valid",
    "issues": "Issues",
    "opinion_notice": (
        "Non-personalized research opinion. This is not an account-level "
        "instruction, position size, or order."
    ),
    "no_market_references": "None identified.",
    "candidate_unparseable": "Candidate was not parseable as a JSON object.",
    "candidate_omitted": "Candidate omitted",
    "candidate_digest": "Digest",
    "no_validation_issues": "No validation issues recorded.",
    "raw_table_available": (
        "Raw tabular content is available in `evidence.json` and the linked `tables/*.csv` files."
    ),
    "not_audited": "not audited",
    "not_recorded": "not recorded",
    "warning.report.audit_incomplete": (
        "The readable report was preserved, but its key-claim audit is incomplete."
    ),
    "warning.report.unknown_evidence_ref": (
        "An unknown evidence reference was ignored in the readable report."
    ),
    "warning.research.unknown_evidence_ref": (
        "An unknown research evidence reference was ignored."
    ),
    "warning.deliberation.audit_incomplete": (
        "The readable deliberation was preserved, but its navigation audit is incomplete."
    ),
    "warning.decision.numeric_audit_partial": (
        "Some optional numeric components were omitted after audit; the qualitative decision remains audited."
    ),
    "warning.decision.numeric_audit_incomplete": (
        "Optional numeric components were omitted after audit; the qualitative decision remains audited."
    ),
    "none": "None identified.",
    "calculation_use.scenario": "{scenario} scenario reference range",
    "calculation_use.valuation": "Valuation assessment",
    "calculation_use.market": "Market reference: {label}",
    "calculation_use.decision": "{location}: {label}",
    "calculation_use.decision_claim": "Decision claim",
    "numeric_status.complete": "complete",
    "numeric_status.partial": "partial",
    "numeric_status.incomplete": "incomplete",
    "numeric_status.not_applicable": "not applicable",
    "numeric_phase.initial": "Initial candidate",
    "numeric_phase.repair": "Repair candidate",
    "temporal.point_in_time": "point in time",
    "temporal.live_snapshot": "live snapshot",
    "claim_importance.primary": "Primary",
    "claim_importance.supporting": "Supporting",
    "claim_kind.observation": "observation",
    "claim_kind.inference": "inference",
    "claim_kind.forecast": "forecast",
    "report.fundamentals": "Fundamentals",
    "report.market": "Market",
    "report.news": "News",
    "report.social": "Sentiment",
}

_ZH_LABELS = {
    **_EN_LABELS,
    "title": "TradingAgentsX 研究",
    "export_schema": "导出结构版本",
    "run": "运行",
    "status": "状态",
    "attempt": "尝试",
    "reports": "研究报告",
    "no_reports": "未记录最终研究报告。",
    "research_process": "研究过程",
    "no_process": "本次运行未记录研究过程产物。",
    "decision_brief": "决策综合草稿",
    "decision_brief_notice": "非正式结论，尚未通过 Final 决策契约审计。",
    "research_decision": "最终结论",
    "no_decision": "未记录最终研究结论。",
    "warnings": "警告",
    "no_warnings": "未记录结构化警告。",
    "performance": "性能指标",
    "attempts": "运行尝试",
    "no_attempts": "未记录尝试级指标。",
    "sources": "来源",
    "no_evidence": "本次运行未记录已封存的 EvidenceBundle。",
    "raw_tables": "原始证据表",
    "evidence_items": "证据条目",
    "content": "正文",
    "audit_records": "审计记录",
    "structured_recoveries": "结构化恢复",
    "no_recoveries": "未记录成功的结构化恢复。",
    "initial_reason": "初始原因",
    "recovery_method": "恢复方式",
    "validation_issues": "校验问题",
    "retry_count": "额外调用",
    "recovered_at": "恢复时间",
    "unverified_numeric": "未验证数值草案",
    "numeric_audit_gaps": "未验证派生值",
    "decision_requirement_audit": "决策关键计算审计",
    "requirement_comparisons": "Requirement 对照",
    "requirement_id": "Requirement",
    "structured_value": "结构化显示值",
    "canonical_result": "Canonical 计算结果",
    "display_scale": "显示尺度",
    "comparison_precision": "比较小数位数",
    "rounded_comparison": "舍入后显示值 / 计算结果",
    "calculation_status": "计算状态",
    "display_status": "显示状态",
    "comparison_not_recorded": "该运行未记录 Requirement 比较详情。",
    "numeric_warning": "以下模型提出的数值内容未通过审计，未用于正式研究结论。",
    "numeric_gap_warning": "以下影响最终结论的派生值尚未通过完整计算审计；定性结论会保留，但这些数值不会进入正式计算记录。",
    "omitted_components": "已省略组件",
    "executive_summary": "执行摘要",
    "thesis": "核心论点",
    "scenarios": "情景分析",
    "base": "基准情景",
    "bull": "乐观情景",
    "bear": "悲观情景",
    "core_assumptions": "核心假设",
    "scenario_reference_range": "情景参考区间",
    "category.technical": "技术",
    "category.historical": "历史",
    "category.analyst_consensus": "卖方共识",
    "category.fundamental": "基本面",
    "category.other": "其他",
    "omission.appendix": "数值附录",
    "omission.calculation": "计算",
    "omission.scenario_range": "情景参考区间",
    "omission.valuation": "估值评估",
    "omission.market_reference": "市场参考位置",
    "omission.decision_claim": "决策关键派生值",
    "endpoint_basis": "端点依据",
    "endpoint_dates": "端点日期",
    "basis.observed": "直接观察",
    "basis.interpreted": "研究解读",
    "basis.derived": "公式推导",
    "valuation_assessment": "估值评估",
    "market_references": "市场参考位置",
    "calculations": "决策关键计算",
    "catalysts": "催化因素",
    "risks": "主要风险",
    "invalidation": "失效条件",
    "unresolved": "未解决问题",
    "risk_response": "最终委员会对风险审查的回应",
    "no_calculations": "未记录决策关键计算。",
    "no_adjustments": "未记录风险审查调整。",
    "debate_agenda": "辩论议程",
    "material_issues": "重要争议",
    "key_claim_audit": "关键观点审计",
    "artifact": "产物",
    "schema": "结构版本",
    "prompt": "提示词版本",
    "generation": "生成方式",
    "generation_observation": "生成路径",
    "created": "创建时间",
    "round": "轮次",
    "source": "来源",
    "evidence": "证据",
    "bundle_version": "证据包版本",
    "digest": "摘要哈希",
    "analysis_date": "分析日期",
    "table": "表格",
    "purpose": "用途",
    "rows": "行数",
    "raw_data": "原始数据",
    "refs": "引用",
    "source_list": "来源",
    "type": "类型",
    "quality": "质量",
    "fallback": "回退",
    "analyst": "分析师",
    "audit": "审计状态",
    "confidence": "置信度",
    "implication": "含义",
    "rating": "研究评级",
    "time_horizon": "研究周期",
    "numeric_audit": "数值审计",
    "usage_note": "用量为本应用已观测并持久化的累计值；进程硬崩溃可能导致最后一次供应商回调无法记录。",
    "llm_calls": "LLM 调用",
    "tool_calls": "工具调用",
    "input_tokens": "输入 Token",
    "output_tokens": "输出 Token",
    "cache_hit": "缓存命中",
    "cache_miss": "缓存未命中",
    "reasoning": "推理输出",
    "detailed_calls": "明细覆盖调用",
    "wall_time": "耗时",
    "node": "节点",
    "resumes": "恢复次数",
    "error": "错误",
    "method": "方法",
    "range": "区间",
    "as_of": "截至日期",
    "temporal_basis": "时序依据",
    "input_evidence": "输入证据",
    "value": "数值",
    "basis": "依据",
    "formula": "公式",
    "inputs": "输入",
    "result": "结果",
    "used_by": "用途",
    "limitations": "局限",
    "status_label": "状态",
    "reason": "原因",
    "schema_valid": "结构有效",
    "issues": "问题代码",
    "opinion_notice": "非个性化研究意见；不构成账户级指令、仓位建议或订单。",
    "no_market_references": "未识别到市场参考位置。",
    "candidate_unparseable": "候选内容无法解析为 JSON 对象。",
    "candidate_omitted": "候选内容已省略",
    "candidate_digest": "摘要哈希",
    "no_validation_issues": "未记录校验问题。",
    "raw_table_available": "原始表格内容位于 `evidence.json` 及对应的 `tables/*.csv` 文件中。",
    "not_audited": "未完成审计",
    "not_recorded": "未记录",
    "warning.report.audit_incomplete": "可读报告已保留，但关键观点审计不完整。",
    "warning.report.unknown_evidence_ref": "可读报告中的未知证据引用已忽略。",
    "warning.research.unknown_evidence_ref": "未知的研究证据引用已忽略。",
    "warning.deliberation.audit_incomplete": "可读研究过程已保留，但导航审计不完整。",
    "warning.decision.numeric_audit_partial": "部分可选数值组件经审计后被省略；定性结论仍已完成审计。",
    "warning.decision.numeric_audit_incomplete": "可选数值组件经审计后被省略；定性结论仍已完成审计。",
    "none": "未识别到相关内容。",
    "calculation_use.scenario": "{scenario}情景参考区间",
    "calculation_use.valuation": "估值评估",
    "calculation_use.market": "市场参考：{label}",
    "calculation_use.decision": "用于{location}：{label}",
    "calculation_use.decision_claim": "决策结论",
    "numeric_status.complete": "完整",
    "numeric_status.partial": "部分完成",
    "numeric_status.incomplete": "未完成",
    "numeric_status.not_applicable": "不适用",
    "numeric_phase.initial": "初次候选",
    "numeric_phase.repair": "修复候选",
    "temporal.point_in_time": "时点数据",
    "temporal.live_snapshot": "实时快照",
    "claim_importance.primary": "主要",
    "claim_importance.supporting": "辅助",
    "claim_kind.observation": "观察",
    "claim_kind.inference": "推论",
    "claim_kind.forecast": "预测",
    "report.fundamentals": "基本面",
    "report.market": "市场",
    "report.news": "新闻",
    "report.social": "舆情",
}

_JA_LABELS = {
    **_EN_LABELS,
    "title": "TradingAgentsX リサーチ",
    "export_schema": "エクスポートスキーマ",
    "run": "実行",
    "status": "ステータス",
    "attempt": "試行",
    "reports": "リサーチレポート",
    "no_reports": "最終レポートは記録されていません。",
    "research_process": "リサーチプロセス",
    "no_process": "この実行には審議成果物が記録されていません。",
    "decision_brief": "意思決定統合ドラフト",
    "decision_brief_notice": (
        "非公式の結論であり、Final 意思決定契約の監査をまだ通過していません。"
    ),
    "research_decision": "最終結論",
    "no_decision": "最終結論は記録されていません。",
    "warnings": "警告",
    "no_warnings": "構造化された警告は記録されていません。",
    "performance": "パフォーマンス指標",
    "attempts": "試行履歴",
    "no_attempts": "試行別の指標は記録されていません。",
    "sources": "情報源",
    "no_evidence": "封印済み EvidenceBundle は記録されていません。",
    "raw_tables": "原始証拠テーブル",
    "evidence_items": "証拠項目",
    "content": "本文",
    "audit_records": "監査記録",
    "structured_recoveries": "構造化出力の復旧",
    "no_recoveries": "成功した構造化出力の復旧は記録されていません。",
    "initial_reason": "初期原因",
    "recovery_method": "復旧方法",
    "validation_issues": "検証上の問題",
    "retry_count": "追加呼び出し",
    "recovered_at": "復旧日時",
    "unverified_numeric": "未検証の数値ドラフト",
    "numeric_audit_gaps": "未検証の導出値",
    "decision_requirement_audit": "意思決定上の重要計算監査",
    "requirement_comparisons": "Requirement 比較",
    "requirement_id": "Requirement",
    "structured_value": "構造化表示値",
    "canonical_result": "Canonical 計算結果",
    "display_scale": "表示スケール",
    "comparison_precision": "比較する小数桁数",
    "rounded_comparison": "丸め後の表示値 / 計算結果",
    "calculation_status": "計算状態",
    "display_status": "表示状態",
    "comparison_not_recorded": "この実行では Requirement の比較詳細を記録していません。",
    "numeric_warning": "以下の数値案は監査を通過せず、正式結論には使用されていません。",
    "numeric_gap_warning": "最終判断に影響する以下の導出値は計算監査を完全には通過していません。定性的判断は保持されますが、正式な計算記録から除外されます。",
    "omitted_components": "省略された項目",
    "executive_summary": "要約",
    "thesis": "中核仮説",
    "scenarios": "シナリオ分析",
    "base": "基本シナリオ",
    "bull": "強気シナリオ",
    "bear": "弱気シナリオ",
    "core_assumptions": "主要前提",
    "scenario_reference_range": "シナリオ参考レンジ",
    "category.technical": "テクニカル",
    "category.historical": "過去データ",
    "category.analyst_consensus": "アナリスト予想",
    "category.fundamental": "ファンダメンタルズ",
    "category.other": "その他",
    "omission.appendix": "数値付録",
    "omission.calculation": "計算",
    "omission.scenario_range": "シナリオ参考レンジ",
    "omission.valuation": "バリュエーション評価",
    "omission.market_reference": "市場参考水準",
    "omission.decision_claim": "意思決定上の導出値",
    "endpoint_basis": "端点の根拠",
    "endpoint_dates": "端点の日付",
    "basis.observed": "直接観測",
    "basis.interpreted": "リサーチ解釈",
    "basis.derived": "数式による導出",
    "valuation_assessment": "バリュエーション評価",
    "market_references": "市場参考水準",
    "calculations": "意思決定上の重要計算",
    "catalysts": "カタリスト",
    "risks": "主要リスク",
    "invalidation": "無効化条件",
    "unresolved": "未解決事項",
    "risk_response": "リスクレビューへの最終委員会の回答",
    "no_calculations": "意思決定上の重要計算は記録されていません。",
    "no_adjustments": "リスクレビューによる調整は記録されていません。",
    "debate_agenda": "討論アジェンダ",
    "material_issues": "重要論点",
    "key_claim_audit": "主要主張の監査",
    "artifact": "成果物",
    "schema": "スキーマ",
    "prompt": "プロンプト版",
    "generation": "生成方式",
    "generation_observation": "生成経路",
    "created": "作成日時",
    "round": "ラウンド",
    "source": "情報源",
    "evidence": "証拠",
    "bundle_version": "証拠バンドル版",
    "digest": "ダイジェスト",
    "analysis_date": "分析日",
    "table": "テーブル",
    "purpose": "用途",
    "rows": "行数",
    "raw_data": "原始データ",
    "refs": "参照",
    "source_list": "情報源",
    "type": "種類",
    "quality": "品質",
    "fallback": "フォールバック",
    "analyst": "アナリスト",
    "audit": "監査状態",
    "confidence": "確信度",
    "implication": "示唆",
    "rating": "評価",
    "time_horizon": "期間",
    "numeric_audit": "数値監査",
    "usage_note": "使用量は本アプリが観測し保存した累計値です。プロセスの強制終了時は最後のプロバイダーコールバックを記録できない場合があります。",
    "llm_calls": "LLM 呼び出し",
    "tool_calls": "ツール呼び出し",
    "input_tokens": "入力トークン",
    "output_tokens": "出力トークン",
    "cache_hit": "キャッシュヒット",
    "cache_miss": "キャッシュミス",
    "reasoning": "推論出力",
    "detailed_calls": "詳細取得回数",
    "wall_time": "実行時間",
    "node": "ノード",
    "resumes": "再開回数",
    "error": "エラー",
    "method": "手法",
    "range": "レンジ",
    "as_of": "基準日",
    "temporal_basis": "時点区分",
    "input_evidence": "入力証拠",
    "value": "値",
    "basis": "根拠",
    "formula": "計算式",
    "inputs": "入力",
    "result": "結果",
    "used_by": "使用先",
    "limitations": "制約",
    "status_label": "ステータス",
    "reason": "理由",
    "schema_valid": "スキーマ有効",
    "issues": "問題コード",
    "opinion_notice": "非個人向けのリサーチ見解であり、口座単位の指示、ポジション量、注文ではありません。",
    "no_market_references": "市場参考水準は特定されていません。",
    "candidate_unparseable": "候補を JSON オブジェクトとして解析できませんでした。",
    "candidate_omitted": "候補を省略",
    "candidate_digest": "ダイジェスト",
    "no_validation_issues": "検証上の問題は記録されていません。",
    "raw_table_available": "生の表データは `evidence.json` と対応する `tables/*.csv` に収録されています。",
    "not_audited": "未監査",
    "not_recorded": "未記録",
    "warning.report.audit_incomplete": "可読レポートは保持されましたが、主要主張の監査は不完全です。",
    "warning.report.unknown_evidence_ref": "可読レポート内の不明な証拠参照を無視しました。",
    "warning.research.unknown_evidence_ref": "不明なリサーチ証拠参照を無視しました。",
    "warning.deliberation.audit_incomplete": "可読の審議内容は保持されましたが、ナビゲーション監査は不完全です。",
    "warning.decision.numeric_audit_partial": "一部の任意数値項目は監査後に省略されました。定性的結論の監査は完了しています。",
    "warning.decision.numeric_audit_incomplete": "任意数値項目は監査後に省略されました。定性的結論の監査は完了しています。",
    "none": "該当項目なし。",
    "calculation_use.scenario": "{scenario}シナリオ参考レンジ",
    "calculation_use.valuation": "バリュエーション評価",
    "calculation_use.market": "市場参考：{label}",
    "calculation_use.decision": "{location}に使用：{label}",
    "calculation_use.decision_claim": "意思決定上の主張",
    "numeric_status.complete": "完了",
    "numeric_status.partial": "一部完了",
    "numeric_status.incomplete": "未完了",
    "numeric_status.not_applicable": "該当なし",
    "numeric_phase.initial": "初回候補",
    "numeric_phase.repair": "修復候補",
    "temporal.point_in_time": "時点データ",
    "temporal.live_snapshot": "ライブスナップショット",
    "claim_importance.primary": "主要",
    "claim_importance.supporting": "補助",
    "claim_kind.observation": "観測",
    "claim_kind.inference": "推論",
    "claim_kind.forecast": "予測",
    "report.fundamentals": "ファンダメンタルズ",
    "report.market": "市場",
    "report.news": "ニュース",
    "report.social": "センチメント",
}


def _export_labels(run_export: RunExport) -> ExportLabels:
    language = run_export.run.request.output_language
    if language == ReportLanguage.SIMPLIFIED_CHINESE:
        return ExportLabels(_ZH_LABELS, language="zh-CN")
    if language == ReportLanguage.JAPANESE:
        return ExportLabels(_JA_LABELS, language="ja")
    return ExportLabels(_EN_LABELS, language="en")


def render_run_export_markdown(run_export: RunExport) -> str:
    """Render a human-readable audit document without hidden model messages."""
    result = run_export.result
    labels = _export_labels(run_export)
    evidence_aliases = _evidence_aliases(run_export.evidence)
    process_artifacts = tuple(
        artifact
        for artifact in run_export.artifacts
        if artifact.stage not in {"analyst", "decision"}
    )
    sections = [
        f"# {labels['title']}: {result.instrument}",
        "",
        f"- {labels['export_schema']}: `{run_export.schema_version}`",
        f"- {labels['run']}: `{result.run_id}`",
        f"- {labels['status']}: `{result.status.value}`",
        f"- {labels['attempt']}: `{run_export.run.attempt}`",
        "",
        f"## {labels['reports']}",
    ]
    if not result.reports:
        sections.extend(["", f"_{labels['no_reports']}_"])
    for name, report in result.reports.items():
        narrative = (
            _render_export_markdown(_render_analyst_report(report, labels), evidence_aliases)
            if isinstance(report, AnalystReport)
            else _render_export_markdown(str(report), evidence_aliases)
        )
        sections.extend(
            [
                "",
                f"### {labels.report_name(name)}",
                "",
                narrative,
            ]
        )

    sections.extend(["", f"## {labels['research_process']}"])
    if not process_artifacts:
        sections.extend(
            [
                "",
                f"_{labels['no_process']}_",
            ]
        )
    for artifact in process_artifacts:
        artifact_title = (
            labels["decision_brief"]
            if isinstance(artifact.content, DecisionBrief)
            else artifact.stage
        )
        sections.extend(
            [
                "",
                (f"### {artifact_title} · {artifact.role} · {labels['round']} {artifact.round}"),
                "",
                f"- {labels['artifact']}: `{artifact.id}`",
                f"- {labels['attempt']}: `{artifact.attempt}`",
                f"- {labels['schema']}: `{artifact.schema_version}`",
                f"- {labels['prompt']}: `{artifact.prompt_version}`",
                f"- {labels['generation']}: `{artifact.generation_method.value}`",
                f"- {labels['created']}: `{artifact.created_at.isoformat()}`",
            ]
        )
        sections.extend(
            "- "
            f"{labels['generation_observation']}: "
            f"`{observation.node}` · `{observation.task_kind}` · "
            f"`{observation.client_role}` · "
            f"`{observation.generation_method.value}`"
            for observation in artifact.generation_observations
        )
        human_text = _render_export_markdown(
            _artifact_human_text(artifact.content, labels),
            evidence_aliases,
        )
        if isinstance(artifact.content, DecisionBrief):
            sections.extend(["", f"> **{labels['decision_brief_notice']}**"])
        if human_text:
            sections.extend(["", human_text])

    sections.extend(["", f"## {labels['research_decision']}"])
    if result.decision is None:
        sections.extend(["", f"_{labels['no_decision']}_"])
    else:
        sections.extend(
            [
                "",
                _render_export_markdown(
                    _render_research_decision(result.decision, labels),
                    evidence_aliases,
                ),
            ]
        )

    if result.numeric_audit is not None:
        sections.extend(
            [
                "",
                _render_numeric_audit_appendix(
                    result.numeric_audit,
                    labels,
                    evidence_aliases,
                ),
            ]
        )

    warnings = _export_warnings(run_export)
    sections.extend(["", f"## {labels['structured_recoveries']}"])
    if not result.recoveries:
        sections.extend(["", f"_{labels['no_recoveries']}_"])
    else:
        for recovery in result.recoveries:
            issues = ", ".join(recovery.validation_issue_codes) or "—"
            sections.extend(
                [
                    "",
                    f"### `{recovery.node}`",
                    "",
                    f"- {labels['attempt']}: `{recovery.attempt}`",
                    f"- {labels['initial_reason']}: `{recovery.initial_reason_code}`",
                    f"- {labels['recovery_method']}: `{recovery.recovery_method.value}`",
                    f"- {labels['validation_issues']}: `{issues}`",
                    f"- {labels['retry_count']}: `{recovery.retry_count}`",
                    f"- {labels['recovered_at']}: `{recovery.recovered_at.isoformat()}`",
                ]
            )
    sections.extend(["", f"## {labels['warnings']}"])
    if not warnings:
        sections.extend(["", f"_{labels['no_warnings']}_"])
    else:
        for warning in warnings:
            details = []
            if warning.source:
                details.append(f"{labels['source']}: {warning.source}")
            if warning.evidence_ref:
                details.append(
                    f"{labels['evidence']}: "
                    + _render_alias_refs((warning.evidence_ref,), evidence_aliases, labels)
                )
            suffix = f" ({'; '.join(details)})" if details else ""
            message = labels.values.get(f"warning.{warning.code}", warning.message)
            sections.append(f"- **{warning.code}**: {message}{suffix}")

    metrics = result.metrics
    sections.extend(
        [
            "",
            f"## {labels['performance']}",
            "",
            f"_{labels['usage_note']}_",
            "",
            f"- {labels['llm_calls']}: `{metrics.llm_calls}`",
            f"- {labels['tool_calls']}: `{metrics.tool_calls}`",
            f"- {labels['input_tokens']}: `{metrics.input_tokens}`",
            f"- {labels['output_tokens']}: `{metrics.output_tokens}`",
            f"- {labels['cache_hit']}: `{metrics.cache_hit_input_tokens}`",
            f"- {labels['cache_miss']}: `{metrics.cache_miss_input_tokens}`",
            f"- {labels['reasoning']}: `{metrics.reasoning_output_tokens}`",
            (f"- {labels['detailed_calls']}: `{metrics.detailed_usage_calls}/{metrics.llm_calls}`"),
            f"- {labels['wall_time']}: `{metrics.wall_time_seconds:.3f}s`",
        ]
    )
    node_names = set(metrics.node_metrics)
    if node_names:
        sections.extend(
            [
                "",
                f"| {labels['node']} | {labels['llm_calls']} | {labels['tool_calls']} | "
                f"{labels['input_tokens']} | {labels['cache_hit']} | "
                f"{labels['cache_miss']} | {labels['output_tokens']} | "
                f"{labels['reasoning']} | {labels['detailed_calls']} | "
                f"{labels['wall_time']} |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for node in sorted(
            node_names,
            key=lambda name: (
                -metrics.node_metrics[name].wall_time_seconds,
                name,
            ),
        ):
            node_usage = metrics.node_metrics[node]
            sections.append(
                f"| `{node}` | {node_usage.llm_calls} | "
                f"{node_usage.tool_calls} | {node_usage.input_tokens} | "
                f"{node_usage.cache_hit_input_tokens} | "
                f"{node_usage.cache_miss_input_tokens} | "
                f"{node_usage.output_tokens} | "
                f"{node_usage.reasoning_output_tokens} | "
                f"{node_usage.detailed_usage_calls} | "
                f"{node_usage.wall_time_seconds:.3f}s |"
            )

    sections.extend(["", f"### {labels['attempts']}"])
    if not run_export.attempts:
        sections.extend(["", f"_{labels['no_attempts']}_"])
    else:
        sections.extend(
            [
                "",
                f"| {labels['attempt']} | {labels['status_label']} | "
                f"{labels['resumes']} | {labels['error']} | {labels['llm_calls']} | "
                f"{labels['tool_calls']} | {labels['input_tokens']} | "
                f"{labels['output_tokens']} | {labels['wall_time']} |",
                "|---:|---|---:|---|---:|---:|---:|---:|---:|",
            ]
        )
        for attempt in run_export.attempts:
            attempt_metrics = attempt.metrics
            sections.append(
                f"| {attempt.attempt} | {attempt.status.value} | "
                f"{attempt.resume_count} | {attempt.error_code or '—'} | "
                f"{attempt_metrics.llm_calls} | {attempt_metrics.tool_calls} | "
                f"{attempt_metrics.input_tokens} | "
                f"{attempt_metrics.output_tokens} | "
                f"{attempt_metrics.wall_time_seconds:.3f}s |"
            )

    sections.extend(["", f"## {labels['sources']}"])
    if run_export.evidence is None:
        sections.extend(["", f"_{labels['no_evidence']}_"])
    else:
        table_refs = {ref for table in run_export.evidence.tables for ref in table.evidence_refs}
        sections.extend(
            [
                "",
                f"- {labels['bundle_version']}: `{run_export.evidence.version}`",
                f"- {labels['digest']}: `{run_export.evidence.digest}`",
                f"- {labels['analysis_date']}: `{run_export.evidence.analysis_date}`",
            ]
        )
        if run_export.evidence.tables:
            sections.extend(["", f"### {labels['raw_tables']}"])
            for table in run_export.evidence.tables:
                sections.extend(
                    [
                        "",
                        f"#### {table.title}",
                        "",
                        f"- {labels['table']}: `{table.id}`",
                        f"- {labels['purpose']}: {table.purpose}",
                        f"- {labels['rows']}: `{len(table.rows)}`",
                        f"- {labels['raw_data']}: `tables/{table.id}.csv`",
                        f"- {labels['evidence']}: "
                        + _render_canonical_refs(table.evidence_refs, labels),
                    ]
                )
            sections.extend(["", f"### {labels['evidence_items']}"])
        for group in group_evidence_by_content(run_export.evidence.items):
            item = group.canonical
            alias = evidence_aliases[item.ref]
            sources = tuple(
                dict.fromkeys(
                    origin.source for grouped_item in group.items for origin in grouped_item.origins
                )
            ) or tuple(dict.fromkeys(grouped_item.source for grouped_item in group.items))
            sections.extend(
                [
                    "",
                    f"### {alias}",
                    "",
                    f"- {labels['refs']}: " + ", ".join(f"`{ref}`" for ref in group.refs),
                    f"- {labels['source_list']}: {', '.join(sources)}",
                    f"- {labels['type']}: {item.evidence_type}",
                    f"- {labels['quality']}: `{item.quality.value}`",
                    f"- {labels['fallback']}: `{str(item.fallback).lower()}`",
                ]
            )
            if group.content and table_refs.isdisjoint(group.refs):
                sections.extend(
                    [
                        "",
                        f"#### {labels['content']}",
                        "",
                        group.content,
                    ]
                )
            elif group.content:
                sections.extend(
                    [
                        "",
                        f"_{labels['raw_table_available']}_",
                    ]
                )
            sections.extend(
                [
                    "",
                    f"#### {labels['audit_records']}",
                    "",
                    "```json",
                    json.dumps(
                        [
                            grouped_item.model_dump(
                                mode="json",
                                exclude={"content"},
                            )
                            for grouped_item in group.items
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "```",
                ]
            )
    return "\n".join(sections)


def render_run_export_package(run_export: RunExport) -> bytes:
    """Build a self-verifying ZIP with a readable report and raw audit data."""

    payloads: dict[str, bytes] = {
        "report.md": render_run_export_markdown(run_export).encode(),
        "run.json": _json_bytes(
            {
                "schema_version": run_export.schema_version,
                "run": run_export.run.model_dump(mode="json"),
                "result": run_export.result.model_dump(mode="json"),
                "attempts": [attempt.model_dump(mode="json") for attempt in run_export.attempts],
            }
        ),
        "artifacts.json": _json_bytes(
            [artifact.model_dump(mode="json") for artifact in run_export.artifacts]
        ),
        "evidence.json": _json_bytes(
            run_export.evidence.model_dump(mode="json") if run_export.evidence is not None else None
        ),
    }
    if run_export.evidence is not None:
        for table in run_export.evidence.tables:
            payloads[f"tables/{table.id}.csv"] = _evidence_table_csv(table)

    manifest = {
        "schema_version": "1",
        "run_id": run_export.run.id,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(payloads.items())
        ],
    }
    payloads["manifest.json"] = _json_bytes(manifest)

    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path, content in sorted(payloads.items()):
            archive.writestr(path, content)
    return output.getvalue()


def _evidence_table_csv(table: EvidenceTable) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["row_id", *(column.key for column in table.columns)])
    for row in table.rows:
        writer.writerow(
            [
                row.id,
                *(_csv_raw_value(row.cells[column.key].raw_value) for column in table.columns),
            ]
        )
    return output.getvalue().encode()


def _csv_raw_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _render_analyst_report(
    report: AnalystReport,
    labels: ExportLabels,
) -> str:
    confidence = (
        f"{report.confidence:.0%}" if report.confidence is not None else labels["not_audited"]
    )
    lines = [
        f"- {labels['analyst']}: `{report.analyst}`",
        f"- {labels['audit']}: `{report.audit_status.value}`",
        f"- {labels['confidence']}: `{confidence}`",
        "",
        report.markdown,
    ]
    if report.key_claims:
        lines.extend(["", f"#### {labels['key_claim_audit']}"])
        for claim in report.key_claims:
            lines.extend(
                [
                    "",
                    "- "
                    f"{labels.enum_name('claim_importance', claim.importance.value)}"
                    " · "
                    f"{labels.enum_name('claim_kind', claim.kind.value)}: "
                    f"{claim.statement}",
                    f"  - {labels['implication']}: {claim.implication}",
                    f"  - {labels['evidence']}: {_render_refs(claim.evidence_refs, labels)}",
                ]
            )
    return "\n".join(lines)


def _evidence_aliases(evidence: Any) -> dict[str, str]:
    if evidence is None:
        return {}
    aliases: dict[str, str] = {}
    for index, group in enumerate(group_evidence_by_content(evidence.items), 1):
        alias = f"E{index:02d}"
        for ref in group.refs:
            aliases[ref] = alias
    return aliases


def _render_export_markdown(
    markdown: str,
    aliases: dict[str, str],
) -> str:
    if not markdown:
        return markdown
    normalized = normalize_evidence_markdown(
        markdown,
        allowed_refs=set(aliases),
        source="markdown export",
    )
    rendered = re.sub(
        r"\[\^(ev_[a-f0-9]{12})\]",
        lambda match: f"[{aliases[match.group(1)]}]",
        normalized.markdown,
    )
    return re.sub(r"(\[E\d+\])(?=\[E\d+\])", r"\1 ", rendered)


def _artifact_human_text(
    content: ResearchArtifactContent,
    labels: ExportLabels,
) -> str:
    if isinstance(content, AnalystReport):
        return _render_analyst_report(content, labels)
    if isinstance(content, DecisionBrief):
        return content.markdown
    if isinstance(content, ResearchCase):
        return _render_research_case(content)
    if isinstance(content, DebateAgenda):
        return _render_debate_agenda(content, labels)
    if isinstance(content, RebuttalReview):
        return _render_rebuttal_review(content)
    if isinstance(content, JudgeDraft):
        return _render_judge_draft(content)
    if isinstance(content, RiskReview):
        return _render_risk_review(content)
    if isinstance(content, ResearchDecision):
        return _render_research_decision(content, labels)
    raise TypeError(f"unsupported research artifact: {type(content)!r}")


def _render_research_case(content: ResearchCase) -> str:
    return content.markdown


def _render_debate_agenda(content: DebateAgenda, labels: ExportLabels) -> str:
    lines = [
        f"#### {labels['debate_agenda']}",
        "",
        content.summary,
        "",
        f"##### {labels['material_issues']}",
    ]
    for issue in content.issues:
        lines.extend(
            [
                "",
                f"###### `{issue.id}` · {issue.importance.value}",
                "",
                issue.question,
                "",
            ]
        )
    return "\n".join(lines)


def _render_rebuttal_review(content: RebuttalReview) -> str:
    return content.markdown


def _render_judge_draft(content: JudgeDraft) -> str:
    return content.markdown


def _render_risk_review(content: RiskReview) -> str:
    return content.markdown


def _render_research_decision(
    content: ResearchDecision,
    labels: ExportLabels,
) -> str:
    calculation_uses = _calculation_uses(content, labels)
    lines = [
        f"> {labels['opinion_notice']}",
        "",
        f"- {labels['rating']}: **{content.rating.value}**",
        f"- {labels['confidence']}: `{content.confidence:.0%}`",
        f"- {labels['time_horizon']}: {content.time_horizon}",
        (
            f"- {labels['numeric_audit']}: `"
            + (
                labels.enum_name("numeric_status", content.numeric_audit_status.value)
                if content.numeric_audit_status is not None
                else labels["not_recorded"]
            )
            + "`"
        ),
        f"- {labels['evidence']}: {_render_refs(content.evidence_refs, labels)}",
        "",
        f"### {labels['executive_summary']}",
        "",
        content.executive_summary,
        "",
        f"### {labels['thesis']}",
        "",
        content.thesis,
        "",
        f"### {labels['scenarios']}",
    ]
    for scenario in content.scenarios:
        lines.extend(
            [
                "",
                f"#### {labels[scenario.kind.value]}",
                "",
                scenario.outcome,
            ]
        )
        lines.extend(
            _render_list(
                labels["core_assumptions"],
                scenario.core_assumptions,
                level=5,
                labels=labels,
            )
        )
        for reference_range in scenario.reference_ranges:
            display_unit = f" {reference_range.unit}" if reference_range.unit else ""
            lines.extend(
                [
                    "",
                    (
                        f"**{labels['scenario_reference_range']} "
                        f"({labels.enum_name('category', reference_range.category.value)} · "
                        f"{reference_range.label}):** "
                        f"`{format_decision_number(reference_range.low.value, reference_range.unit, output_language=labels.language)}`–"
                        f"`{format_decision_number(reference_range.high.value, reference_range.unit, output_language=labels.language)}`"
                        f"{display_unit}"
                    ),
                    (
                        f"**{labels['endpoint_basis']}:** "
                        f"{labels[f'basis.{reference_range.low.basis.value}']} / "
                        f"{labels[f'basis.{reference_range.high.basis.value}']}"
                    ),
                    (
                        f"**{labels['endpoint_dates']}:** "
                        f"`{reference_range.low.as_of_date.isoformat()}` "
                        f"({labels.enum_name('temporal', reference_range.low.temporal_basis.value)}) / "
                        f"`{reference_range.high.as_of_date.isoformat()}` "
                        f"({labels.enum_name('temporal', reference_range.high.temporal_basis.value)})"
                    ),
                    reference_range.interpretation,
                ]
            )
            lines.extend(
                _render_list(
                    labels["limitations"],
                    reference_range.limitations,
                    labels=labels,
                )
            )
        lines.extend(
            [
                "",
                f"**{labels['evidence']}:** {_render_refs(scenario.evidence_refs, labels)}",
            ]
        )
    if content.valuation_assessment is not None:
        assessment = content.valuation_assessment
        lines.extend(
            [
                "",
                f"### {labels['valuation_assessment']}",
                "",
                f"- {labels['method']}: {assessment.method}",
                (
                    f"- {labels['range']}: "
                f"`{format_decision_number(assessment.low.value, assessment.unit, output_language=labels.language)}`–"
                f"`{format_decision_number(assessment.high.value, assessment.unit, output_language=labels.language)}` "
                f"{assessment.unit}"
                ),
                f"- {labels['as_of']}: `{assessment.as_of_date.isoformat()}`",
                (
                    f"- {labels['temporal_basis']}: "
                    f"{labels.enum_name('temporal', assessment.low.temporal_basis.value)} / "
                    f"{labels.enum_name('temporal', assessment.high.temporal_basis.value)}"
                ),
                f"- {labels['input_evidence']}: "
                + _render_refs(assessment.input_evidence_refs, labels),
                f"- {labels['calculations']}: " + _render_ids(assessment.calculation_ids, labels),
            ]
        )
        lines.extend(_render_list(labels["limitations"], assessment.limitations, labels=labels))
    lines.extend(["", f"### {labels['market_references']}"])
    if content.market_reference_levels:
        for level in content.market_reference_levels:
            display_unit = f" {level.unit}" if level.unit else ""
            lines.extend(
                [
                    "",
                    f"#### {level.label}",
                    "",
                    f"- {labels['value']}: "
                    f"`{format_decision_number(level.value, level.unit, output_language=labels.language)}`"
                    f"{display_unit}",
                    f"- {labels['as_of']}: `{level.as_of_date.isoformat()}`",
                    f"- {labels['evidence']}: {_render_refs(level.evidence_refs, labels)}",
                    f"- {labels['basis']}: {labels[f'basis.{level.basis.value}']}",
                    f"- {labels['temporal_basis']}: "
                    f"{labels.enum_name('temporal', level.temporal_basis.value)}",
                    (f"- {labels['calculations']}: " + _render_ids(level.calculation_ids, labels)),
                    "",
                    level.interpretation,
                ]
            )
    else:
        lines.extend(["", f"_{labels['no_market_references']}_"])
    lines.extend(["", f"### {labels['calculations']}"])
    if content.calculation_records:
        for calculation in content.calculation_records:
            inputs = ", ".join(
                f"{name}={format_decision_number(value, output_language=labels.language)}"
                for name, value in calculation.inputs.items()
            )
            lines.extend(
                [
                    "",
                    f"#### `{calculation.id}`",
                    "",
                    f"- {labels['used_by']}: "
                    + ", ".join(calculation_uses.get(calculation.id, ())),
                    f"- {labels['formula']}: `{calculation.formula}`",
                    f"- {labels['inputs']}: `{inputs}`",
                    f"- {labels['result']}: "
                    f"`{format_decision_number(calculation.result, calculation.unit, output_language=labels.language)}` "
                    f"{calculation.unit}",
                    f"- {labels['as_of']}: `{calculation.as_of_date.isoformat()}`",
                    f"- {labels['temporal_basis']}: "
                    f"{labels.enum_name('temporal', calculation.temporal_basis.value)}",
                    f"- {labels['evidence']}: "
                    + _render_refs(calculation.input_evidence_refs, labels),
                ]
            )
            lines.extend(
                _render_list(labels["limitations"], calculation.limitations, labels=labels)
            )
    else:
        lines.extend(["", f"_{labels['no_calculations']}_"])
    lines.extend(_render_list(labels["catalysts"], content.catalysts, labels=labels))
    lines.extend(_render_list(labels["risks"], content.risks, labels=labels))
    lines.extend(
        _render_list(
            labels["invalidation"],
            content.invalidation_conditions,
            labels=labels,
        )
    )
    lines.extend(_render_list(labels["unresolved"], content.unresolved_questions, labels=labels))
    lines.extend(["", f"### {labels['risk_response']}"])
    if content.risk_review_adjustments:
        for adjustment in content.risk_review_adjustments:
            lines.extend(
                [
                    "",
                    (f"#### {adjustment.source_role.title()} · {adjustment.disposition.value}"),
                    "",
                    f"**{adjustment.subject}**",
                    "",
                    adjustment.explanation,
                    "",
                    f"**{labels['evidence']}:** {_render_refs(adjustment.evidence_refs, labels)}",
                ]
            )
    else:
        lines.extend(["", f"_{labels['no_adjustments']}_"])
    return "\n".join(lines)


def _calculation_uses(
    content: ResearchDecision,
    labels: ExportLabels,
) -> dict[str, tuple[str, ...]]:
    uses: dict[str, list[str]] = {}

    def add(calculation_ids: tuple[str, ...], label: str) -> None:
        for calculation_id in calculation_ids:
            uses.setdefault(calculation_id, []).append(label)

    for calculation in content.calculation_records:
        for use in calculation.decision_uses:
            add(
                (calculation.id,),
                labels["calculation_use.decision"].format(
                    location=_decision_component_label(use.component_path, labels),
                    label=use.label,
                ),
            )

    for scenario in content.scenarios:
        for reference_range in scenario.reference_ranges:
            add(
                tuple(
                    item
                    for item in (
                        reference_range.low.calculation_id,
                        reference_range.high.calculation_id,
                    )
                    if item is not None
                ),
                labels["calculation_use.scenario"].format(
                    scenario=labels[scenario.kind.value]
                ),
            )
    if content.valuation_assessment is not None:
        add(
            content.valuation_assessment.calculation_ids,
            labels["calculation_use.valuation"],
        )
    for level in content.market_reference_levels:
        add(
            level.calculation_ids,
            labels["calculation_use.market"].format(label=level.label),
        )
    return {calculation_id: tuple(dict.fromkeys(labels)) for calculation_id, labels in uses.items()}


def _decision_component_label(component_path: str, labels: ExportLabels) -> str:
    if component_path == "executive_summary":
        return labels["executive_summary"]
    if component_path == "thesis":
        return labels["thesis"]
    if component_path.startswith("risks."):
        return labels["risks"]
    if component_path.startswith("invalidation_conditions."):
        return labels["invalidation"]
    if component_path.startswith("risk_review_adjustments."):
        return labels["risk_response"]
    match = re.fullmatch(r"scenarios\.(base|bull|bear)\..+", component_path)
    if match:
        return labels["calculation_use.scenario"].format(
            scenario=labels[match.group(1)]
        )
    return labels["calculation_use.decision_claim"]


def _render_numeric_audit_appendix(
    appendix: DecisionNumericAuditAppendix,
    labels: ExportLabels,
    evidence_aliases: Mapping[str, str],
) -> str:
    has_snapshots = bool(appendix.snapshots)
    has_checks = bool(appendix.requirement_checks)
    lines = [
        f"## {labels['decision_requirement_audit'] if has_checks else labels['unverified_numeric'] if has_snapshots else labels['numeric_audit_gaps']}",
        "",
        f"- {labels['status_label']}: `{appendix.status.value}`",
    ]
    if has_snapshots or appendix.omitted_components:
        lines[1:1] = [
            "",
            f"> **{labels['warnings']}:** "
            f"{labels['numeric_warning'] if has_snapshots else labels['numeric_gap_warning']}",
        ]
    if appendix.requirement_checks:
        lines.extend(
            [
                "",
                f"### {labels['requirement_comparisons']}",
                "",
                (
                    f"| {labels['requirement_id']} | {labels['structured_value']} | "
                    f"{labels['canonical_result']} | {labels['comparison_precision']} | "
                    f"{labels['calculation_status']} | {labels['display_status']} |"
                ),
                "|---|---:|---:|---:|---|---|",
            ]
        )
        for check in appendix.requirement_checks:
            stated = format_decision_number(
                float(check.stated_value),
                check.unit,
                output_language=labels.language,
            )
            comparison = (
                format_decision_number(
                    float(check.comparison_result),
                    check.unit,
                    output_language=labels.language,
                )
                if check.comparison_result is not None
                else "—"
            )
            lines.append(
                f"| {check.label} (`{check.component_path}`) | {stated} {check.unit} | "
                f"{comparison}{f' {check.unit}' if check.comparison_result is not None else ''} | "
                f"{check.fraction_digits} | `{check.calculation_status.value}` | "
                f"`{check.display_status.value}` |"
            )
        for check in appendix.requirement_checks:
            refs = _render_alias_refs(
                check.input_evidence_refs,
                evidence_aliases,
                labels,
            )
            rounded = (
                f"{check.rounded_stated_value} / {check.rounded_canonical_result}"
                if check.rounded_stated_value is not None
                and check.rounded_canonical_result is not None
                else "—"
            )
            lines.extend(
                [
                    "",
                    f"- **{check.label}** (`{check.requirement_id}`)",
                    f"  - {labels['formula']}: `{check.formula}`",
                    f"  - {labels['inputs']}: `{json.dumps(check.inputs, ensure_ascii=False, sort_keys=True)}`",
                    f"  - {labels['rounded_comparison']}: `{rounded}`",
                    f"  - {labels['canonical_result']}: `{check.canonical_result}`",
                    f"  - {labels['display_scale']}: `{check.display_scale.value}`",
                    f"  - {labels['evidence']}: {refs or '—'}",
                ]
            )
            if check.issue_codes:
                lines.append(
                    f"  - {labels['issues']}: "
                    + ", ".join(f"`{code}`" for code in check.issue_codes)
                )
    elif not has_snapshots and not appendix.omitted_components:
        lines.extend(["", f"_{labels['comparison_not_recorded']}_"])
    if appendix.omitted_components:
        lines.extend(["", f"### {labels['omitted_components']}"])
        for item in appendix.omitted_components:
            lines.append(
                f"- **{_numeric_omission_label(item, labels)}** "
                f"(`{item.component_path}`): "
                + ", ".join(f"`{code}`" for code in item.issue_codes)
            )
    for snapshot in appendix.snapshots:
        phase_label = labels.enum_name("numeric_phase", snapshot.phase.value)
        lines.extend(
            [
                "",
                f"### {phase_label}",
                "",
                f"- {labels['method']}: `{snapshot.method.value}`",
                f"- {labels['reason']}: `{snapshot.reason_code}`",
                f"- {labels['schema_valid']}: `{str(snapshot.schema_valid).lower()}`",
                (
                    f"- {labels['issues']}: "
                    + (
                        ", ".join(f"`{code}`" for code in snapshot.validation_issues)
                        or f"_{labels['no_validation_issues']}_"
                    )
                ),
            ]
        )
        if snapshot.candidate is not None:
            lines.extend(
                [
                    "",
                    "```json",
                    json.dumps(snapshot.candidate, ensure_ascii=False, indent=2),
                    "```",
                ]
            )
        elif snapshot.candidate_omitted:
            lines.append(
                f"- {labels['candidate_omitted']}: "
                f"`{snapshot.candidate_omitted}` "
                f"({labels['candidate_digest']} `{snapshot.candidate_digest}`)"
            )
        else:
            lines.append(f"- {labels['candidate_unparseable']}")
    return "\n".join(lines)


def _numeric_omission_label(item: Any, labels: ExportLabels) -> str:
    parts: list[str] = []
    if item.scenario_kind is not None:
        parts.append(labels[item.scenario_kind.value])
    parts.append(labels.enum_name("omission", item.component_type.value))
    if item.reference_label:
        parts.append(item.reference_label)
    return " · ".join(parts)


def _render_list(
    title: str,
    items: tuple[str, ...],
    *,
    level: int = 5,
    labels: ExportLabels,
) -> list[str]:
    prefix = "#" * level
    return [
        "",
        f"{prefix} {title}",
        "",
        *([f"- {item}" for item in items] if items else [f"- {labels['none']}"]),
    ]


def _render_refs(refs: tuple[str, ...], labels: ExportLabels) -> str:
    return " ".join(f"[^{ref}]" for ref in refs) or labels["none"]


def _render_ids(refs: tuple[str, ...], labels: ExportLabels) -> str:
    return ", ".join(f"`{ref}`" for ref in refs) or labels["none"]


def _render_canonical_refs(
    refs: tuple[str, ...],
    labels: ExportLabels,
) -> str:
    return ", ".join(f"`{ref}`" for ref in refs) or labels["none"]


def _render_alias_refs(
    refs: tuple[str, ...],
    aliases: Mapping[str, str],
    labels: ExportLabels,
) -> str:
    rendered = tuple(f"[{aliases[ref]}]" for ref in refs if ref in aliases)
    return " ".join(rendered) or labels["none"]


def _export_warnings(run_export: RunExport) -> tuple[ResearchWarning, ...]:
    """Collect each structured warning once across durable result/artifacts."""

    warnings = [
        *run_export.result.warnings,
        *(
            warning
            for report in run_export.result.reports.values()
            if isinstance(report, AnalystReport)
            for warning in report.warnings
        ),
        *(
            warning
            for artifact in run_export.artifacts
            if isinstance(artifact.content, AnalystReport)
            for warning in artifact.content.warnings
        ),
    ]
    return tuple(dict.fromkeys(warnings))
