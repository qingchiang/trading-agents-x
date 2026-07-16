"""Deterministic evidence extraction for company-news candidates.

Rules here enforce objective boundaries (source, template, entity evidence).
Ambiguous natural-language mentions are labelled ``candidate`` and left to the
existing analyst LLM instead of being decided by an expanding lexical ruleset.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

NewsTier = Literal["direct", "candidate", "context", "drop"]
EvidenceLevel = Literal["direct", "candidate", "none"]


@dataclass(frozen=True)
class NewsClassification:
    """A stable relevance tier plus a short machine-testable reason."""

    tier: NewsTier
    reason: str


@dataclass(frozen=True)
class CompanyAliases:
    """Ticker aliases plus strong and analyst-review company-name evidence."""

    ticker: frozenset[str]
    direct_names: frozenset[str]
    candidate_names: frozenset[str]

    @property
    def names(self) -> frozenset[str]:
        """Compatibility union for diagnostics and callers that inspect aliases."""
        return self.direct_names | self.candidate_names


_ENGLISH_SUFFIXES = re.compile(
    r"[\s,.-]+(?:corporation|corp|incorporated|inc|limited|ltd|plc|company|co)$",
    flags=re.IGNORECASE,
)
_DOMAIN_STYLE_NAME = re.compile(
    r"^(?P<base>.+?)\.(?:com|net|org)$",
    flags=re.IGNORECASE,
)
_ENGLISH_BRAND_DESCRIPTORS = re.compile(
    r"[\s,.-]+(?:markets|technologies|technology|platforms)$",
    flags=re.IGNORECASE,
)
_JAPANESE_CORPORATE_MARKERS = ("株式会社", "(株)", "（株）", "㈱")

_ENGLISH_DERIVATIVE_TERMS = (
    "etf",
    "fund",
    "leveraged",
    "inverse",
    "option income",
    "covered call",
    "yieldmax",
    "2x",
    "3x",
)
_JAPANESE_DERIVATIVE_TERMS = ("レバレッジ", "インバース", "投資信託")

TRUSTED_GOOGLE_SOURCES = frozenset({
    "reuters",
    "ロイター",
    "bloomberg",
    "ブルームバーグ",
    "日本経済新聞",
    "nhk",
    "共同通信",
    "時事通信",
    "quickmoneyworld",
    "株探",
    "東洋経済オンライン",
})

_JAPANESE_BUSINESS_TERMS = (
    "株",
    "株価",
    "決算",
    "業績",
    "売上",
    "利益",
    "投資",
    "出資",
    "買収",
    "提携",
    "融資",
    "資金調達",
    "債券",
    "配当",
    "自社株",
    "上場",
    "証券",
    "市場",
    "目標株価",
    "レーティング",
    "社長",
    "会長",
    "経営",
    "事業",
    "産業",
    "半導体",
)
_ENGLISH_BUSINESS_TERMS = (
    "ai",
    "earnings",
    "revenue",
    "profit",
    "investment",
    "financing",
    "acquisition",
    "merger",
    "partnership",
    "management",
    "ceo",
    "industry",
    "market",
)


def normalize_news_text(value: str | None) -> str:
    """NFKC/casefold text with punctuation and whitespace removed."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return "".join(char for char in normalized if char.isalnum())


def _normalize_match_text(value: str | None) -> str:
    """NFKC/casefold text with punctuation collapsed to token boundaries."""
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in normalized).split())


def canonical_headline(title: str) -> str:
    """Return a deterministic title key for exact normalized deduplication."""
    return normalize_news_text(title)


def _is_direct_name(alias: str) -> bool:
    """Treat full/multi-token and Japanese names as strong entity evidence."""
    if not alias.isascii():
        return True
    tokens = alias.split()
    # A stripped legal name such as ``The Gap`` is also ordinary prose. Keep
    # the complete ``The Gap, Inc.`` strong, but let the analyst judge the
    # article-plus-one-word form instead of treating it as a company event.
    if len(tokens) == 2 and tokens[0] in {"a", "an", "the"}:
        return False
    return len(tokens) >= 2


def _name_aliases(name: str) -> tuple[set[str], set[str]]:
    """Return strong aliases and ambiguous/derived aliases for analyst review."""
    raw = unicodedata.normalize("NFKC", name).strip()
    legal_variants = {raw}
    stripped = raw
    while True:
        shorter = _ENGLISH_SUFFIXES.sub("", stripped).strip(" ,.-")
        if shorter == stripped:
            break
        legal_variants.add(shorter)
        stripped = shorter

    direct: set[str] = set()
    candidate: set[str] = set()
    for value in legal_variants:
        alias = _normalize_match_text(value)
        if len(alias) < 2:
            continue
        (direct if _is_direct_name(alias) else candidate).add(alias)

    # Common headline forms derived from legal metadata remain candidates: the
    # analyst decides whether Amazon, Robinhood, Palantir, monday, etc. denotes
    # the target company in this particular headline.
    for value in tuple(legal_variants):
        domain_match = _DOMAIN_STYLE_NAME.fullmatch(value)
        if domain_match:
            base = _normalize_match_text(domain_match.group("base"))
            if len(base) >= 4:
                candidate.add(base)
        brand = _ENGLISH_BRAND_DESCRIPTORS.sub("", value).strip(" ,.-")
        brand_alias = _normalize_match_text(brand)
        if brand != value and len(brand_alias) >= 4:
            candidate.add(brand_alias)

    japanese_variants = set(legal_variants)
    for marker in _JAPANESE_CORPORATE_MARKERS:
        japanese_variants |= {
            value.replace(marker, "").strip() for value in tuple(japanese_variants)
        }
    for value in tuple(japanese_variants):
        japanese_variants.add(value.replace("ホールディングス", "HD"))
        japanese_variants.add(value.replace("フィナンシャルグループ", "FG"))
        japanese_variants.add(value.replace("グループ", "G"))
    for value in japanese_variants - legal_variants:
        alias = _normalize_match_text(value)
        if len(alias) >= 2:
            direct.add(alias)

    candidate -= direct
    return direct, candidate


def build_company_aliases(
    ticker: str,
    *names: str | None,
    ticker_aliases: Iterable[str] = (),
) -> CompanyAliases:
    """Build ticker, strong-name, and analyst-review name aliases."""
    ticker_values = set()
    for value in (ticker, *ticker_aliases):
        if value:
            ticker_values.update((value, value.split(".", 1)[0]))
    tickers = frozenset(
        value
        for raw in ticker_values
        if len(value := unicodedata.normalize("NFKC", raw).casefold().strip()) >= 2
    )

    direct_names: set[str] = set()
    candidate_names: set[str] = set()
    for name in names:
        if not name:
            continue
        direct, candidate = _name_aliases(name)
        direct_names.update(direct)
        candidate_names.update(candidate)
    candidate_names -= direct_names
    return CompanyAliases(
        ticker=tickers,
        direct_names=frozenset(direct_names),
        candidate_names=frozenset(candidate_names),
    )


def _contains_ascii_term(text: str, term: str) -> bool:
    """Match an ASCII term as tokens, never inside another ASCII word."""
    pattern = r"(?<![a-z0-9])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _matches_alias(text: str | None, aliases: frozenset[str]) -> bool:
    haystack = _normalize_match_text(text)
    if not haystack:
        return False
    compact_haystack = haystack.replace(" ", "")
    return any(
        _contains_ascii_term(haystack, alias)
        if alias.isascii()
        else alias.replace(" ", "") in compact_haystack
        for alias in aliases
        if alias
    )


def _name_evidence(text: str | None, aliases: CompanyAliases) -> EvidenceLevel:
    if _matches_alias(text, aliases.direct_names):
        return "direct"
    if _matches_alias(text, aliases.candidate_names):
        return "candidate"
    return "none"


def _ticker_evidence(text: str | None, aliases: frozenset[str]) -> EvidenceLevel:
    haystack = unicodedata.normalize("NFKC", text or "").casefold()
    if not haystack:
        return "none"
    candidate = False
    for alias in aliases:
        escaped = re.escape(alias)
        explicit = (
            rf"(?:[$＄]|(?:nyse|nasdaq|amex|ticker)\s*[:：]\s*){escaped}"
            rf"(?![a-z0-9])|[\(\[【]{escaped}[\)\]】]"
        )
        if re.search(explicit, haystack):
            return "direct"
        if re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", haystack):
            candidate = True
    return "candidate" if candidate else "none"


def _higher_evidence(*levels: EvidenceLevel) -> EvidenceLevel:
    if "direct" in levels:
        return "direct"
    if "candidate" in levels:
        return "candidate"
    return "none"


def _is_derivative_title(title: str) -> bool:
    normalized = _normalize_match_text(title)
    return any(
        _contains_ascii_term(normalized, term) for term in _ENGLISH_DERIVATIVE_TERMS
    ) or any(term in normalized for term in _JAPANESE_DERIVATIVE_TERMS)


def classify_yahoo_article(
    title: str,
    summary: str,
    aliases: CompanyAliases,
) -> NewsClassification:
    """Classify objective evidence; leave ambiguous mentions to the analyst."""
    title_name = _name_evidence(title, aliases)
    title_ticker = _ticker_evidence(title, aliases.ticker)
    title_evidence = _higher_evidence(title_name, title_ticker)

    if title_name == "none" and title_ticker != "none" and _is_derivative_title(title):
        return NewsClassification("context", "ticker-only derivative product")
    if title_evidence == "direct":
        return NewsClassification("direct", "explicit entity evidence in title")
    if title_evidence == "candidate":
        return NewsClassification("candidate", "ambiguous entity evidence in title")

    summary_evidence = _higher_evidence(
        _name_evidence(summary, aliases),
        _ticker_evidence(summary, aliases.ticker),
    )
    if summary_evidence != "none":
        return NewsClassification("candidate", "entity evidence only in summary")
    return NewsClassification("drop", "no company evidence")


def _trusted_google_source(source: str) -> bool:
    normalized = normalize_news_text(source)
    return any(value in normalized for value in TRUSTED_GOOGLE_SOURCES)


def _business_context(title: str) -> bool:
    normalized = _normalize_match_text(title)
    return any(term in normalized for term in _JAPANESE_BUSINESS_TERMS) or any(
        _contains_ascii_term(normalized, term) for term in _ENGLISH_BUSINESS_TERMS
    )


def classify_google_article(
    title: str,
    source: str,
    aliases: CompanyAliases,
) -> NewsClassification:
    """Apply hard feed filters, then classify strong or analyst-review evidence."""
    normalized_source = normalize_news_text(source)
    normalized_title = unicodedata.normalize("NFKC", title).casefold()

    if "mshale" in normalized_source:
        return NewsClassification("drop", "blocked source")
    if "】：" in title or "今の株価の理由は" in title or "値動きの背景をaiが解説" in normalized_title:
        return NewsClassification("drop", "automated quote template")
    if (
        "日経会社情報digital" in normalized_title
        or "日経会社情報digital" in normalized_source
    ) and (
        "適時開示" in title or "ガバナンス" in title or "コーポレートガバナンス" in title
    ):
        return NewsClassification("drop", "official disclosure mirror")

    entity = _higher_evidence(
        _name_evidence(title, aliases),
        _ticker_evidence(title, aliases.ticker),
    )
    trusted = _trusted_google_source(source)
    business = _business_context(title)
    if entity == "direct" and (trusted or business):
        return NewsClassification("direct", "explicit company evidence in title")
    if entity == "candidate" and (trusted or business):
        return NewsClassification("candidate", "ambiguous company evidence in title")
    if trusted and business:
        return NewsClassification("context", "trusted business context")
    if entity != "none":
        return NewsClassification("drop", "unknown source without business context")
    return NewsClassification("drop", "no company evidence")
