"""Deterministic decision prompt guidance and examples."""

from __future__ import annotations

from tradingagents.domain.common import (
    ReportLanguage,
)
from tradingagents.research.synthesis.drafts import ResearchDecisionCoreDraft
from tradingagents.research.synthesis.numeric_evidence import (
    NumericValueCatalogEntry,
)


def decision_scenario_assumption_guidance(output_language: str) -> str:
    """Return reader-facing scenario guidance without adding a hard validator."""

    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return (
            "每条情景假设必须在脱离上下文后仍可独立理解。涉及共识、指引、"
            "目标值或预测时，必须写明指标主体（例如 EPS、收入、营业利润或目标价）、"
            "数值与单位，以及理解该假设所需的时间范围或条件。例如："
            "‘分析师 EPS 共识上修至每股 185–195 日元’；不要只写"
            "‘共识上修至 185–195 日元’。"
        )
    if output_language == ReportLanguage.JAPANESE.prompt_label:
        return (
            "各シナリオの前提は、文脈から切り離しても単独で理解できるように書くこと。"
            "コンセンサス、ガイダンス、目標値または予想に触れる場合は、指標の主体"
            "（EPS、売上高、営業利益、目標株価など）、数値と単位、および必要な期間や"
            "条件を明記すること。例：『アナリストのEPSコンセンサスが1株185～195円へ"
            "上方修正される』。『コンセンサスが185～195円へ上方修正される』だけでは"
            "不十分。"
        )
    return (
        "Write every scenario assumption so it remains independently understandable "
        "outside its surrounding context. When referring to consensus, guidance, a "
        "target, or a forecast, name the metric subject (for example EPS, revenue, "
        "operating profit, or target price), its value and unit, and any time period "
        "or condition needed to interpret it. Example: 'Analyst EPS consensus rises "
        "to JPY 185-195 per share'; do not write only 'Consensus rises to JPY 185-195'."
    )


def decision_percentage_calculation_guidance() -> str:
    """Return the stable wire contract for decision percentage calculations."""

    return (
        "For unit %, percent, or pct, formulas must return a fractional ratio in "
        "the 0-to-1 convention and must not multiply by 100; stated_value uses "
        "reader-facing percentage points, and the application deterministically "
        "converts the formula result. For example, "
        "(target_price - close_price) / close_price = 0.4546 uses "
        "stated_value=45.46 and unit=%. A decline formula yielding -0.6132 uses "
        "stated_value=-61.32 and unit=%. Percentage-point formulas also return a "
        "fractional difference and use unit=pp; the application multiplies by 100. "
        "Basis-point formulas return a fractional difference and use unit=bps; the "
        "application multiplies by 10,000. Never multiply these formulas by their "
        "reader-facing scale."
    )


def decision_display_scale_guidance() -> str:
    """Return the canonical contract for compact reader-facing quantities."""

    return (
        "Every numeric requirement must declare display_scale separately from its "
        "canonical unit. Display scale describes only the formula result and must "
        "never be inherited from an input's measurement scale. Use base, thousand, "
        "ten_thousand, million, hundred_million, billion, or trillion. Results with "
        "unit %, percent, pct, pp, percentage points, bps, basis points, x, or 倍 "
        "are dimensionless and must use display_scale=base. For example, a growth "
        "formula using net-income inputs 332,129 and 245,447 that are each measured "
        "in million JPY still has unit=% and display_scale=base, not million. For an "
        "amount example, raw result 80,598,000,000 with unit=USD and "
        "display_scale=hundred_million compares with stated_value=805.98. Do not "
        "encode result scale in unit strings such as billion USD, 亿美元, or 百万日元."
    )


def decision_reference_label_guidance(output_language: str) -> str:
    """Return localized naming rules for analyst target references."""

    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        examples = (
            "单个 target_low/min 称为‘目标价下限’，单个 target_high/max 称为"
            "‘目标价上限’，单个 target_mean/average 称为‘目标价均值’。"
        )
    elif output_language == ReportLanguage.JAPANESE.prompt_label:
        examples = (
            "単一の target_low/min は『目標株価下限』、target_high/max は"
            "『目標株価上限』、target_mean/average は『目標株価平均』と呼ぶこと。"
        )
    else:
        examples = (
            "Name a single target_low/min 'analyst target lower bound', a single "
            "target_high/max 'analyst target upper bound', and a single "
            "target_mean/average 'analyst target mean'."
        )
    return (
        examples + " Only call an item an analyst target range when it uses two distinct "
        "low and high endpoints. Never duplicate one value ref to preserve a range label."
    )


def _decision_language_rules(output_language: str) -> str:
    return (
        "Write every human-readable field in the requested report language: "
        f"{output_language}. Keep rating values, schema enums, IDs, formula "
        "variable names, Evidence refs, and unit wire values in "
        "their required schema format. " + decision_scenario_assumption_guidance(output_language)
    )


def _decision_component_text(
    decision: ResearchDecisionCoreDraft,
    component_path: str,
) -> str | None:
    """Resolve the bounded public field paths accepted by numeric requirements."""

    parts = component_path.split(".")
    if component_path in {"executive_summary", "thesis"}:
        return str(getattr(decision, component_path))
    if parts[0] in {"catalysts", "risks", "invalidation_conditions"} and len(parts) == 2:
        values = getattr(decision, parts[0])
        index = int(parts[1])
        return values[index] if index < len(values) else None
    if parts[0] == "scenarios" and len(parts) in {3, 4}:
        scenario = next(
            (item for item in decision.scenarios if item.kind.value == parts[1]),
            None,
        )
        if scenario is None:
            return None
        if parts[2] == "outcome" and len(parts) == 3:
            return scenario.outcome
        if parts[2] == "core_assumptions" and len(parts) == 4:
            index = int(parts[3])
            return (
                scenario.core_assumptions[index] if index < len(scenario.core_assumptions) else None
            )
    if parts[0] == "risk_review_adjustments" and len(parts) == 3 and parts[2] == "explanation":
        index = int(parts[1])
        return (
            decision.risk_review_adjustments[index].explanation
            if index < len(decision.risk_review_adjustments)
            else None
        )
    return None


def _decision_example_text(output_language: str) -> dict[str, str]:
    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return {
            "adjustment_subject": "置信度校准",
            "adjustment_explanation": "最终结论已纳入风险审查意见。",
            "executive_summary": "现有证据支持一项平衡的研究结论。",
            "requirement_thesis": "分析师目标均价对应约 45.5% 的隐含上行空间。",
            "requirement_label": "分析师目标价隐含上行空间",
            "thesis": "该观点取决于一个可验证的经营机制。",
            "risk": "证据支持的下行风险可能会兑现。",
            "invalidation": "新证据直接否定核心论点。",
            "question": "哪一种情景将占据主导？",
            "horizon": "6至12个月",
            "base_assumption": "分析师 EPS 共识维持在每股 185–195 日元。",
            "base_outcome": "核心论点大体按预期演进。",
            "bull_assumption": "未来十二个月分析师 EPS 共识上修至每股 200 日元以上。",
            "bull_outcome": "结果优于基准情景。",
            "bear_assumption": "未来十二个月分析师 EPS 共识下修至每股 175 日元以下。",
            "bear_outcome": "结果弱于基准情景。",
            "valuation_method": "基于证据的盈利倍数法",
            "valuation_limitation": "估值倍数取决于情景假设。",
            "reference_label": "近期观察收盘价",
            "reference_interpretation": "这是直接观察的参考值，并非执行指令。",
            "scenario_range_label": "技术参考区间",
            "scenario_range_interpretation": "该区间来自已观察的市场位置，并非估值结论。",
        }
    if output_language == ReportLanguage.JAPANESE.prompt_label:
        return {
            "adjustment_subject": "確信度の調整",
            "adjustment_explanation": "最終判断にはリスクレビューを反映した。",
            "executive_summary": "現時点の証拠は均衡の取れた判断を支持する。",
            "requirement_thesis": "アナリスト平均目標株価は約45.5%の上昇余地を示す。",
            "requirement_label": "アナリスト目標株価の上昇余地",
            "thesis": "この見解は検証可能な事業メカニズムに依存する。",
            "risk": "証拠に裏付けられた下振れリスクが顕在化し得る。",
            "invalidation": "新たな証拠が中核仮説を直接否定する。",
            "question": "どのシナリオが優勢になるか。",
            "horizon": "6〜12か月",
            "base_assumption": "アナリストのEPSコンセンサスが1株185～195円で維持される。",
            "base_outcome": "仮説は概ね想定どおりに進展する。",
            "bull_assumption": "今後12か月のEPSコンセンサスが1株200円超へ上方修正される。",
            "bull_outcome": "結果は基本シナリオを上回る。",
            "bear_assumption": "今後12か月のEPSコンセンサスが1株175円未満へ下方修正される。",
            "bear_outcome": "結果は基本シナリオを下回る。",
            "valuation_method": "証拠に基づく利益倍率法",
            "valuation_limitation": "倍率はシナリオ前提に左右される。",
            "reference_label": "直近の観測終値",
            "reference_interpretation": "直接観測した参考値であり、執行指示ではない。",
            "scenario_range_label": "テクニカル参考レンジ",
            "scenario_range_interpretation": "観測済みの市場水準であり、企業価値評価ではない。",
        }
    return {
        "adjustment_subject": "Confidence calibration",
        "adjustment_explanation": "The final decision incorporates the risk review.",
        "executive_summary": "The evidence supports a balanced conclusion.",
        "requirement_thesis": "The analyst mean target implies about 45.5% upside.",
        "requirement_label": "Analyst target implied upside",
        "thesis": "The view depends on a testable operating mechanism.",
        "risk": "The evidence-backed downside may materialize.",
        "invalidation": "New evidence directly contradicts the thesis.",
        "question": "Which scenario will dominate?",
        "horizon": "6-12 months",
        "base_assumption": "Analyst EPS consensus remains JPY 185-195 per share.",
        "base_outcome": "The thesis develops broadly as expected.",
        "bull_assumption": "Twelve-month analyst EPS consensus rises above JPY 200 per share.",
        "bull_outcome": "The result exceeds the base case.",
        "bear_assumption": "Twelve-month analyst EPS consensus falls below JPY 175 per share.",
        "bear_outcome": "The result falls below the base case.",
        "valuation_method": "Evidence-backed earnings multiple",
        "valuation_limitation": "The multiple is scenario-dependent.",
        "reference_label": "Observed recent close",
        "reference_interpretation": ("A directly observed reference, not an execution order."),
        "scenario_range_label": "Technical reference range",
        "scenario_range_interpretation": (
            "The range uses observed market levels and is not a valuation conclusion."
        ),
    }


def _numeric_example_pair(
    value_catalog: tuple[NumericValueCatalogEntry, ...],
) -> tuple[NumericValueCatalogEntry, NumericValueCatalogEntry] | None:
    """Return one compatible, strictly ordered pair for the prompt example."""

    for index, first in enumerate(value_catalog):
        for second in value_catalog[index + 1 :]:
            if (
                first.measurement_kind is not second.measurement_kind
                or first.unit != second.unit
                or first.value == second.value
            ):
                continue
            return tuple(sorted((first, second), key=lambda item: item.value))
    return None
