from datetime import date

from tradingagents.domain.evidence import EvidenceItem, MeasurementKind, TableDataType
from tradingagents.domain.evidence_tables import extract_evidence_tables


def test_table_parser_keeps_metadata_carriers_unmeasured() -> None:
    item = EvidenceItem(
        ref="ev_0123456789ab",
        source="fixture",
        evidence_type="verified market snapshot",
        requested_date=date(2026, 8, 1),
        effective_date=date(2026, 7, 31),
        content=(
            "| Indicator | Value | Measurement | Unit |\n"
            "|---|---:|---|---|\n"
            "| close | 3075 | currency | JPY |"
        ),
    )

    table = extract_evidence_tables((item,))[0]
    columns = {column.key: column for column in table.columns}

    assert columns["measurement"].data_type is TableDataType.TEXT
    assert columns["measurement"].unit is None
    assert columns["unit"].data_type is TableDataType.TEXT
    assert columns["unit"].measurement_kind is MeasurementKind.UNKNOWN
    assert columns["unit"].unit is None
