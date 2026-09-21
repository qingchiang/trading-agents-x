"""Japanese-market data-source vendors (fork-only).

J-Quants (equities OHLCV / indicators / financial summaries / TOPIX / investor
flows) and EDINET (statutory disclosures, code map, large-holdings) plus the two
JP fundamental assemblers. Grouped in a subpackage to keep the flat ``dataflows``
namespace legible; these are all files upstream does not have, so moving them
here carries no rebase-conflict cost. Cross-market infrastructure (routing,
multi-region macro, shared utils) stays in ``dataflows`` root.
"""
