"""Tests for symbol normalization and the no-data routing sentinel."""

import unittest

import pytest

from tradingagents.dataflows.symbol_utils import (
    NoMarketDataError,
    infer_mainland_equity_suffix,
    is_yahoo_safe,
    normalize_symbol,
    tokyo_securities_base,
    unsupported_crypto_base,
)


@pytest.mark.unit
class TestNormalizeSymbol(unittest.TestCase):
    def test_plain_equities_unchanged(self):
        for sym in ("AAPL", "MSFT", "TSM", "BRK.B", "0700.HK", "^GSPC", "GC=F"):
            self.assertEqual(normalize_symbol(sym), sym)

    def test_lowercases_are_upper(self):
        self.assertEqual(normalize_symbol("aapl"), "AAPL")
        self.assertEqual(normalize_symbol("  msft  "), "MSFT")

    def test_metal_aliases_map_to_futures(self):
        self.assertEqual(normalize_symbol("XAUUSD"), "GC=F")
        self.assertEqual(normalize_symbol("XAUUSD+"), "GC=F")   # broker CFD suffix
        self.assertEqual(normalize_symbol("xauusd+"), "GC=F")
        self.assertEqual(normalize_symbol("GOLD"), "GC=F")
        self.assertEqual(normalize_symbol("XAGUSD"), "SI=F")

    def test_energy_and_index_aliases(self):
        self.assertEqual(normalize_symbol("USOIL"), "CL=F")
        self.assertEqual(normalize_symbol("SPX500"), "^GSPC")
        self.assertEqual(normalize_symbol("NAS100"), "^NDX")
        self.assertEqual(normalize_symbol("US30"), "^DJI")

    def test_forex_pairs_get_x_suffix(self):
        self.assertEqual(normalize_symbol("EURUSD"), "EURUSD=X")
        self.assertEqual(normalize_symbol("GBPJPY"), "GBPJPY=X")
        self.assertEqual(normalize_symbol("eurusd"), "EURUSD=X")

    def test_crypto_pairs_are_not_normalized_to_vendor_symbols(self):
        self.assertEqual(normalize_symbol("BTCUSD"), "BTCUSD")
        self.assertEqual(normalize_symbol("ETHUSD"), "ETHUSD")

    def test_six_letter_non_currency_left_alone(self):
        # GOOGLE-style 6-letter tickers that aren't two currency codes
        # must not be mangled into a fake forex pair.
        self.assertEqual(normalize_symbol("ABCDEF"), "ABCDEF")

    def test_empty_input_passthrough(self):
        self.assertEqual(normalize_symbol(""), "")

    def test_mainland_equity_suffix_inference(self):
        self.assertEqual(infer_mainland_equity_suffix("600519"), ".SS")
        self.assertEqual(infer_mainland_equity_suffix("000001"), ".SZ")
        self.assertIsNone(infer_mainland_equity_suffix("510300"))
        self.assertIsNone(infer_mainland_equity_suffix("AAPL"))

    def test_explicit_mainland_equity_suffix_mismatch_fails_loud(self):
        with self.assertRaisesRegex(ValueError, "suffix mismatch"):
            normalize_symbol("600519.SZ")
        with self.assertRaisesRegex(ValueError, "suffix mismatch"):
            normalize_symbol("300750.SS")

    def test_unsupported_mainland_security_types_fail_loud(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            normalize_symbol("510300.SS")
        with self.assertRaisesRegex(ValueError, "not supported"):
            normalize_symbol("399006.SZ")

    def test_configured_mainland_benchmarks_remain_valid(self):
        self.assertEqual(normalize_symbol("000001.SS"), "000001.SS")
        self.assertEqual(normalize_symbol("000001.SH"), "000001.SS")
        self.assertEqual(normalize_symbol("399001.SZ"), "399001.SZ")


@pytest.mark.unit
class TestNoMarketDataError(unittest.TestCase):
    def test_message_includes_resolution(self):
        err = NoMarketDataError("XAUUSD+", "GC=F", "no rows")
        self.assertIn("XAUUSD+", str(err))
        self.assertIn("GC=F", str(err))
        self.assertEqual(err.symbol, "XAUUSD+")
        self.assertEqual(err.canonical, "GC=F")

    def test_canonical_defaults_to_symbol(self):
        err = NoMarketDataError("FOOBAR")
        self.assertEqual(err.canonical, "FOOBAR")

    def test_availability_notes_are_preserved(self):
        err = NoMarketDataError(
            "FOOBAR", availability_notes=("<source unavailable>", "")
        )
        self.assertEqual(err.availability_notes, ("<source unavailable>",))


@pytest.mark.unit
class TestIsYahooSafe(unittest.TestCase):
    def test_accepts_structural_chars(self):
        for sym in ("AAPL", "GC=F", "^GSPC", "BRK.B"):
            self.assertTrue(is_yahoo_safe(sym))

    def test_rejects_slash_and_space(self):
        for sym in ("a/b", "AA PL", ""):
            self.assertFalse(is_yahoo_safe(sym))


@pytest.mark.unit
class TestTokyoSecuritiesBase(unittest.TestCase):
    """Shared 5-digit→4-digit reduction used by both J-Quants (from_jquants_code)
    and EDINET secCode matching, so they stay on the same key."""

    def test_strips_5digit_trailing_zero(self):
        self.assertEqual(tokyo_securities_base("99840"), "9984")

    def test_passes_through_4digit(self):
        self.assertEqual(tokyo_securities_base("7203"), "7203")

    def test_missing_code_is_empty(self):
        self.assertEqual(tokyo_securities_base(None), "")
        self.assertEqual(tokyo_securities_base(""), "")


@pytest.mark.unit
class TestUnsupportedCryptoBase(unittest.TestCase):
    def test_identifies_known_crypto_forms_for_request_rejection(self):
        for raw in ("BTC-USD", "BTCUSD", "btc-usdt", "BTC-USDC", "BTCUSD+"):
            self.assertEqual(unsupported_crypto_base(raw), "BTC")
        self.assertEqual(unsupported_crypto_base("ETH-USD"), "ETH")
        self.assertEqual(unsupported_crypto_base("sol-usd"), "SOL")
        self.assertEqual(unsupported_crypto_base("PEPE-USD"), "PEPE")
        self.assertEqual(unsupported_crypto_base("BTC-JPY"), "BTC")

    def test_non_crypto_returns_none(self):
        # Plain equities, class shares, and real tickers that alias elsewhere
        # (GOLD -> gold future on the Yahoo path) must NOT read as crypto.
        for raw in ("AAPL", "BRK-B", "GOLD", "EURUSD", "", None):
            self.assertIsNone(unsupported_crypto_base(raw))


if __name__ == "__main__":
    unittest.main()
