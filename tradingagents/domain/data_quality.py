"""Pure temporal-scope and data-quality rules for producer metadata."""

from collections.abc import Iterable

from tradingagents.domain.data import ProvenanceQualityIssue, ProvenanceRecord, TemporalScopeName


def temporal_scope_from_records(
    records: Iterable[ProvenanceRecord],
) -> TemporalScopeName:
    """Infer a conservative scope for producer source metadata."""
    scopes = {_temporal_scope_from_record(record) for record in records}
    scopes.discard("unknown")
    return scopes.pop() if len(scopes) == 1 else "unknown"


def _temporal_scope_from_record(record: ProvenanceRecord) -> TemporalScopeName:
    text = " ".join(
        (
            record.evidence,
            record.source,
            record.effective,
            record.timing,
        )
    ).casefold()
    if any(
        token in text
        for token in (
            "live-only",
            "live only",
            "live non-point-in-time",
            "live non point in time",
            "current-only",
            "current snapshot",
            "retrieval-time snapshot",
            "retrieval-time analyst",
            "not historical pit",
            "not point-in-time",
            "non-point-in-time",
        )
    ):
        return "live_only"
    if any(
        token in text
        for token in (
            "point-in-time",
            "date filtered",
            "date-filtered",
            "market-date filtered",
            "trade-date filtered",
            "observation-date filtered",
            "publication-date filtered",
            "publication/update-date filtered",
            "disclosure-date filtered",
            "fiscal period ends",
        )
    ):
        return "point_in_time"
    return "unknown"


def _escape_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip() or "—"


_WARNING_RULES = (
    (("not requested",), "not_requested", "expected evidence was not requested"),
    (
        ("no auditable source metadata",),
        "missing_metadata",
        "no auditable source metadata captured",
    ),
    (("no usable data",), "no_usable_data", "no usable data from configured sources"),
    (("unavailable",), "unavailable", "source unavailable for requested date/window"),
    (("failed",), "retrieval_failed", "source retrieval failed"),
    (("fallback",), "fallback", "fallback source used"),
    (
        ("adjustment provider changed",),
        "adjustment_changed",
        "adjustment provider changed; technical indicators may differ",
    ),
    (
        (
            "non-point-in-time",
            "not point-in-time",
            "not historical pit",
            "non-strict pit",
        ),
        "not_point_in_time",
        "not point-in-time",
    ),
    (("non-vintage",), "non_vintage", "non-vintage series"),
    (("not queried",), "not_queried", "source was not queried"),
    (("truncated",), "truncated", "result set truncated"),
    (("stale",), "stale", "stale data"),
    (("partial",), "partial", "partial coverage"),
    (
        ("future-dated evidence withheld",),
        "future_dated",
        "future-dated evidence withheld",
    ),
)


def _is_successful_empty(timing: str) -> bool:
    """True for a successful query that legitimately produced no evidence."""
    return timing.startswith("available;") and (
        "; no " in timing or "contained no values" in timing
    )


def provenance_quality_issues(
    records: Iterable[ProvenanceRecord],
) -> list[ProvenanceQualityIssue]:
    """Return deterministic warnings for material provenance degradation.

    Routine date filtering and an empty-but-successful news window are not
    warnings. The terms below describe missing evidence, fallback/partial
    coverage, stale/truncated data, or timing that is unsuitable for strict
    historical interpretation.
    """
    issues: list[ProvenanceQualityIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        timing = record.timing.strip()
        timing_search = timing.casefold()
        reasons = list(
            dict.fromkeys(
                (code, label)
                for terms, code, label in _WARNING_RULES
                if any(term in timing_search for term in terms)
            )
        )
        if record.source.strip().casefold() in {"", "unknown", "—"}:
            reasons.append(("unknown_source", "source metadata unknown"))
        if (
            record.effective.strip().casefold() in {"", "unknown", "—"}
            and not _is_successful_empty(timing_search)
        ):
            reasons.append(
                ("unknown_effective", "effective date/window unknown")
            )
        evidence = _escape_cell(record.evidence)
        source = _escape_cell(record.source)
        for code, reason in dict.fromkeys(reasons):
            key = (evidence.casefold(), source.casefold(), reason.casefold())
            if key not in seen:
                issues.append(
                    ProvenanceQualityIssue(
                        evidence=evidence,
                        source=source,
                        code=code,
                        reason=reason,
                    )
                )
                seen.add(key)
    return issues
