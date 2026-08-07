---
status: proposed
---

# Use ordinal confidence until calibration exists

The initial revision-chain contracts use low, medium, high, or indeterminate
confidence for Research Claims and the overall Research Opinion rather than
unconstrained values between zero and one. Model-generated decimal confidence
is not a calibrated probability, creates false precision, and makes historical
Materiality comparisons depend on unstable score differences. Numeric
confidence may be added later only with a declared, evaluated calibration
method; Scenario Probability remains a separate concept.

Any validated transition between ordinal tiers for an active,
decision-relevant Research Claim is a Material Change. This prevents a
Revision from reporting No Material Change while silently changing the
confidence of a Claim on which the Research Opinion depends.
