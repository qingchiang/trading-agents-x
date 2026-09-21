"""Full research perspective objectives shared by graph role construction."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RoleSpec:
    key: str
    label: str
    objective: str
    model: Literal["quick", "deep"] = "quick"


PERSPECTIVE_SPECS = {
    "bull": RoleSpec(
        key="bull",
        label="Bull Researcher",
        objective=(
            "Build the strongest evidence-grounded constructive case from the "
            "typed analyst claims. Explain causal mechanisms, identify the "
            "strongest opposing argument, and expose fragile assumptions."
        ),
    ),
    "bear": RoleSpec(
        key="bear",
        label="Bear Researcher",
        objective=(
            "Build the strongest evidence-grounded skeptical case. Separate "
            "real downside mechanisms from data gaps and mere unknowns, while "
            "acknowledging the strongest constructive evidence."
        ),
    ),
    "risk": RoleSpec(
        key="integrated",
        label="Integrated Risk Reviewer",
        objective=(
            "Review upside omissions, base-case consistency, downside paths, "
            "data quality, and invalidation. Recommend explicit changes to the "
            "judge draft without proposing account or execution instructions."
        ),
    ),
    "aggressive": RoleSpec(
        key="aggressive",
        label="Aggressive Risk Lens",
        objective=(
            "Stress-test whether the draft underweights asymmetric upside and "
            "opportunity cost, while explicitly identifying failure conditions."
        ),
    ),
    "neutral": RoleSpec(
        key="neutral",
        label="Neutral Risk Lens",
        objective=(
            "Balance upside and downside mechanisms, surface uncertainty, and "
            "challenge overconfident claims on either side."
        ),
    ),
    "conservative": RoleSpec(
        key="conservative",
        label="Conservative Risk Lens",
        objective=(
            "Stress-test downside, data quality, regime shifts, and thesis "
            "invalidation without giving account-level trading instructions."
        ),
    ),
}
