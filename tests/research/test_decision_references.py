"""Decision serialization retains research references without a numeric phase."""

from datetime import date

from tests.support.factories import research_decision
from tradingagents.domain.evidence import EvidenceBundle, EvidenceItem
from tradingagents.research.synthesis.decision import invoke_research_decision


class ResponseModel:
    preferred_structured_output_method = "function_calling"

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, prompt, config=None):
        self.prompts.append(prompt)
        if len(self.prompts) > 1:
            raise AssertionError("Optional references must not cause another model call")
        return {"raw": None, "parsed": self.payload}


def reference_fixture():
    item = EvidenceItem.create(
        source="fixture",
        evidence_type="market",
        requested_date=date(2026, 7, 24),
        effective_date=date(2026, 7, 24),
        value=100,
        unit="USD",
    )
    bundle = EvidenceBundle(instrument="NVDA", analysis_date=date(2026, 7, 24), items=(item,))
    payload = research_decision(evidence_refs=(item.ref,)).model_dump(mode="json")
    endpoint = {
        "value": 120,
        "basis": "derived",
        "evidence_refs": [item.ref],
        "date_evidence_refs": [item.ref],
        "as_of_date": "2026-07-24",
    }
    reference = {
        "category": "fundamental",
        "label": "Earnings scenario",
        "low": endpoint,
        "high": {**endpoint, "value": 150},
        "unit": "USD",
        "interpretation": "Conditional earnings multiple range.",
        "limitations": ["Depends on assumptions."],
    }
    payload["scenarios"][0]["reference_ranges"] = [reference]
    payload["market_reference_levels"] = [
        {
            **endpoint,
            "label": "Scenario reference",
            "unit": "USD",
            "interpretation": "Conditional reference.",
        }
    ]
    # Deliberately omit all retired audit fields, even on the predecessor schema.
    for key in ("valuation_assessment", "calculation_records", "numeric_audit_status"):
        payload.pop(key, None)
    return payload, bundle


def test_decision_keeps_derived_ranges_without_a_calculator_or_extra_model_call():
    payload, bundle = reference_fixture()
    model = ResponseModel(payload)
    output = invoke_research_decision(
        model,
        prompt="Serialize the adopted conclusion.",
        state={"evidence_bundle": bundle.model_dump(mode="json")},
        node="committee.final.serialize",
        require_risk_adjustments=False,
    )
    assert output.value.scenarios[0].reference_ranges[0].high.value == 150
    assert output.value.market_reference_levels[0].value == 120
    assert output.value.thesis == payload["thesis"]
    assert len(model.prompts) == 1
    assert (
        not {"valuation_assessment", "calculation_records", "numeric_audit_status"}
        & output.value.model_dump().keys()
    )


def test_invalid_optional_reference_is_omitted_without_losing_valid_siblings():
    payload, bundle = reference_fixture()
    good = payload["market_reference_levels"][0]
    payload["market_reference_levels"].extend(
        [
            {**good, "value": True},
            {
                **good,
                "evidence_refs": ["ev_ffffffffffff"],
                "date_evidence_refs": ["ev_ffffffffffff"],
            },
            {**good, "as_of_date": "2027-01-01"},
            {**good, "value": float("inf")},
        ]
    )
    events = []
    output = invoke_research_decision(
        ResponseModel(payload),
        prompt="Serialize research.",
        state={"evidence_bundle": bundle.model_dump(mode="json")},
        node="decision",
        require_risk_adjustments=False,
        event_writer=events.append,
    )
    assert [level.value for level in output.value.market_reference_levels] == [120]
    assert output.value.scenarios[0].reference_ranges[0].high.value == 150
    omissions = [event for event in events if event["event_type"] == "decision.reference_omitted"]
    assert [event["payload"]["field_path"] for event in omissions] == [
        f"market_reference_levels.{index}" for index in range(1, 5)
    ]
    assert all("input" not in event["payload"] for event in omissions)


def test_invalid_range_and_non_array_references_preserve_core_and_other_scenarios():
    payload, bundle = reference_fixture()
    good = payload["scenarios"][0]["reference_ranges"][0]
    payload["scenarios"][0]["reference_ranges"].append({**good, "high": good["low"]})
    payload["scenarios"][1]["reference_ranges"] = {"unexpected": "not an array"}
    payload["market_reference_levels"] = None
    events = []
    output = invoke_research_decision(
        ResponseModel(payload),
        prompt="Serialize research.",
        state={"evidence_bundle": bundle.model_dump(mode="json")},
        node="decision",
        require_risk_adjustments=False,
        event_writer=events.append,
    )
    assert len(output.value.scenarios[0].reference_ranges) == 1
    assert output.value.scenarios[1].reference_ranges == ()
    assert output.value.market_reference_levels == ()
    assert output.value.thesis == payload["thesis"]
    omissions = [event for event in events if event["event_type"] == "decision.reference_omitted"]
    assert len(omissions) == 3
