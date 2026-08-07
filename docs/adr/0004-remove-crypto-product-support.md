---
status: proposed
---

# Remove crypto product support

TradingAgentsX supports investment research for listed equities in the United
States, Japan, and mainland China and will remove crypto as a public analysis
mode. The unreleased Web/SQLite product line inherited only best-effort crypto
compatibility: generic yfinance routes, limited symbol adaptations, no
crypto-specific research domains or live validation, stock-oriented final
research prompts, and an inappropriate default equity benchmark. The previous
v0.4.0 release used the legacy CLI product, so the unreleased product line does
not need to preserve this unvalidated surface as compatibility overhead.

Implementation will remove crypto from public request contracts, Web and CLI
creation paths, routing assumptions, graph/report behavior, outcome and memory
semantics, tests, and product documentation. Low-level symbol or UTC helpers may
remain only when they serve supported non-crypto behavior; they do not imply
crypto product support.
