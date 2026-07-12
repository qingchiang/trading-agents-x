"""Ticker → Japanese company name resolution via J-Quants /equities/master."""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows.jp import company_info as ci, jquants_common


def _patch(records=None, side_effect=None):
    return mock.patch.object(
        jquants_common, "fetch_records", return_value=records, side_effect=side_effect
    )


@pytest.mark.unit
class CompanyNameTests(unittest.TestCase):
    def setUp(self):
        ci._MASTER_CACHE.clear()

    def test_returns_clean_short_name(self):
        with _patch([{"Code": "45680", "CoName": "第一三共"}]):
            self.assertEqual(ci.get_company_name("4568.T"), "第一三共")

    def test_memoized_single_fetch_per_code(self):
        with _patch([{"CoName": "トヨタ自動車"}]) as fr:
            ci.get_company_name("7203.T")
            ci.get_company_name("7203.T")
        self.assertEqual(fr.call_count, 1)

    def test_latest_row_by_date_holds_current_name(self):
        # Rows may be unordered; the latest Date wins regardless of position, so a
        # renamed company resolves to its current name (here the newer row is first).
        with _patch([
            {"CoName": "新名", "Date": "2024-01-01"},
            {"CoName": "旧名", "Date": "2020-01-01"},
        ]):
            self.assertEqual(ci.get_company_name("1234.T"), "新名")

    def test_no_records_returns_none(self):
        with _patch([]):
            self.assertIsNone(ci.get_company_name("9999.T"))

    def test_missing_coname_returns_none(self):
        with _patch([{"Code": "12340"}]):
            self.assertIsNone(ci.get_company_name("1234.T"))

    def test_fetch_error_degrades_to_none(self):
        with _patch(side_effect=RuntimeError("boom")):
            self.assertIsNone(ci.get_company_name("7203.T"))


if __name__ == "__main__":
    unittest.main()
