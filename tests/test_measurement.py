"""Deterministic measurement metadata supplied by data adapters."""

from tradingagents.dataflows.measurement import classify_vendor_unit, instrument_currency


def test_instrument_currency_covers_supported_market_shapes() -> None:
    assert instrument_currency("3778.T") == "JPY"
    assert instrument_currency("NVDA") == "USD"
    assert instrument_currency("600309.SS") == "CNY"
    assert instrument_currency("000001.SZ") == "CNY"


def test_vendor_units_are_classified_only_when_explicit() -> None:
    assert classify_vendor_unit("%") == ("percent", "%")
    assert classify_vendor_unit("2020=100") == ("index", "2020=100")
    assert classify_vendor_unit("basis points") == ("basis_points", "basis points")
    assert classify_vendor_unit("JPY") == ("currency", "JPY")
    assert classify_vendor_unit("Millions of yen") == ("unknown", "Millions of yen")
    assert classify_vendor_unit("") == ("unknown", None)
