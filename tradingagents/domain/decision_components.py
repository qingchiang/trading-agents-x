"""Stable component identities shared by Full and Incremental decisions."""

def baseline_component_ids(decision) -> tuple[str, ...]:
    component_ids = ["executive_summary", "thesis"]
    for field in ("catalysts", "risks", "invalidation_conditions"):
        component_ids.extend(f"{field}.{index}" for index, _ in enumerate(getattr(decision, field)))
    for scenario in decision.scenarios:
        component_ids.append(f"scenarios.{scenario.kind.value}.outcome")
        component_ids.extend(
            f"scenarios.{scenario.kind.value}.core_assumptions.{index}"
            for index, _ in enumerate(scenario.core_assumptions)
        )
    component_ids.extend(
        f"risk_review_adjustments.{index}.explanation"
        for index, _ in enumerate(decision.risk_review_adjustments)
    )
    return tuple(component_ids)
