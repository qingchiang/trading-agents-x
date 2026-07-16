"""Contract tests for deterministic news evidence boundaries."""

import pytest

from tradingagents.dataflows.news_quality import (
    build_company_aliases,
    canonical_headline,
    classify_google_article,
    classify_yahoo_article,
    normalize_news_text,
)


@pytest.mark.unit
def test_text_normalization_is_nfkc_casefold_and_punctuation_insensitive():
    assert normalize_news_text("ＮＶＩＤＩＡ, Inc. ") == "nvidiainc"
    assert canonical_headline("NVIDIA — AI") == canonical_headline("nvidia AI")


@pytest.mark.unit
def test_alias_builder_separates_strong_and_analyst_review_names():
    softbank = build_company_aliases(
        "9984.T",
        "SoftBank Group Corp.",
        "ソフトバンクグループ株式会社",
    )
    assert "softbank group" in softbank.direct_names
    assert "softbank" not in softbank.names
    assert "ソフトバンクg" in softbank.direct_names
    assert "9984" in softbank.ticker

    amazon = build_company_aliases("AMZN", "Amazon.com, Inc.")
    robinhood = build_company_aliases("HOOD", "Robinhood Markets, Inc.")
    assert "amazon com" in amazon.direct_names
    assert "amazon" in amazon.candidate_names
    assert "robinhood markets" in robinhood.direct_names
    assert "robinhood" in robinhood.candidate_names

    gap = build_company_aliases("GAP", "The Gap, Inc.")
    assert "the gap inc" in gap.direct_names
    assert "the gap" in gap.candidate_names


@pytest.mark.unit
def test_yahoo_classifier_exposes_ambiguous_mentions_to_the_analyst():
    target = build_company_aliases("TGT", "Target Corporation")
    now = build_company_aliases("NOW", "ServiceNow, Inc.")
    cases = (
        ("Target Corporation reports earnings", "", target, "direct"),
        ("Target (TGT) reports earnings", "", target, "direct"),
        ("Target stands to gain as Ikea closes stores", "", target, "candidate"),
        ("NVIDIA price target rises", "", target, "candidate"),
        ("Now is the time to review your portfolio", "", now, "candidate"),
        ("Unrelated retail story", "Target may benefit.", target, "candidate"),
        ("Unrelated retail story", "", target, "drop"),
        (
            "The gap between wages and inflation widens",
            "",
            build_company_aliases("GAP", "The Gap, Inc."),
            "candidate",
        ),
        (
            "The Gap, Inc. reports quarterly earnings",
            "",
            build_company_aliases("GAP", "The Gap, Inc."),
            "direct",
        ),
    )
    for title, summary, aliases, expected in cases:
        assert classify_yahoo_article(title, summary, aliases).tier == expected


@pytest.mark.unit
def test_explicit_short_ticker_is_direct_but_bare_ticker_is_candidate():
    aliases = build_company_aliases("AI")
    assert classify_yahoo_article("$AI reports earnings", "", aliases).tier == "direct"
    assert classify_yahoo_article("AI spending accelerates", "", aliases).tier == "candidate"
    assert classify_yahoo_article("London market advances", "", build_company_aliases("ON")).tier == "drop"


@pytest.mark.unit
def test_ticker_only_derivative_is_context_without_substring_false_positive():
    aliases = build_company_aliases("NVDA", "NVIDIA Corporation")
    assert classify_yahoo_article(
        "NVDA Covered Call ETF raises distribution", "", aliases
    ).tier == "context"
    assert classify_yahoo_article(
        "NVDA fundamentals improve", "", aliases
    ).tier == "candidate"


@pytest.mark.unit
def test_google_classifier_keeps_hard_filters_and_evidence_tiers():
    aliases = build_company_aliases("4568.T", "第一三共")
    cases = (
        ("第一三共が新薬へ投資", "Unknown Wire", "direct"),
        ("4568の株価見通し", "Unknown Wire", "candidate"),
        ("製薬業界で大型買収が相次ぐ", "Reuters", "context"),
        ("第一三共のファン交流会", "Unknown Blog", "drop"),
        ("第一三共が投資を発表", "Mshale", "drop"),
        (
            "第一三共(株)【4568】：今の株価の理由は？値動きの背景をAIが解説",
            "Yahoo!ファイナンス",
            "drop",
        ),
        ("[4568]第一三共 適時開示情報", "日経会社情報DIGITAL", "drop"),
    )
    for title, source, expected in cases:
        assert classify_google_article(title, source, aliases).tier == expected
