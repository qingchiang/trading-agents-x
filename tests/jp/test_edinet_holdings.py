"""EDINET large-shareholding (大量保有) signal: subject matching, self-heal,
windowing, graceful degradation. Network and code resolution are mocked."""
import tempfile
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.jp import edinet_code_map as cm, edinet_common, edinet_holdings


def _holding(subject="E02778", *, doc_type="350", filer="ブラックロック・ジャパン株式会社",
             when="2026-06-22 15:00", doc_id="S100HOLD", sec_code="", edinet_code="E11111"):
    """A large-shareholding filing record (filer = the shareholder)."""
    return {
        "subjectEdinetCode": subject, "docTypeCode": doc_type, "filerName": filer,
        "submitDateTime": when, "docID": doc_id, "secCode": sec_code,
        "edinetCode": edinet_code,
    }


def _own_report(sec_code="13010", edinet_code="E00012"):
    """A company's own filing (carries its own secCode + edinetCode)."""
    return {
        "subjectEdinetCode": "", "docTypeCode": "120", "secCode": sec_code,
        "edinetCode": edinet_code, "submitDateTime": "2026-06-22 09:00", "docID": "S100OWN",
    }


def _by_date(mapping):
    return lambda date_str: mapping.get(date_str, [])


@pytest.mark.unit
class HoldingsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        set_config({"data_cache_dir": self._tmp.name})
        cm._reset_for_tests()
        edinet_common._documents_cache.clear()

    def tearDown(self):
        cm._reset_for_tests()
        edinet_common._documents_cache.clear()
        config_module._config = __import__("copy").deepcopy(default_config.DEFAULT_CONFIG)
        self._tmp.cleanup()

    def _patch(self, mapping):
        return mock.patch.object(
            edinet_common, "fetch_documents", side_effect=_by_date(mapping)
        )

    def test_non_tokyo_returns_empty(self):
        with mock.patch.object(edinet_common, "fetch_documents") as fd:
            self.assertEqual(edinet_holdings.get_large_holdings("AAPL", "2026-06-25"), "")
        fd.assert_not_called()

    def test_default_window_is_exactly_90_calendar_dates(self):
        with mock.patch.object(
            edinet_holdings, "iter_window_dates", return_value=[]
        ) as window:
            edinet_holdings.get_large_holdings("9984.T", "2026-06-22")
        window.assert_called_once_with("2026-03-25", "2026-06-22")

    def test_matches_subject_edinet_code(self):
        # 9984.T resolves (seed) to E02778; keep filings about it, drop others.
        mapping = {"2026-06-22": [
            _holding(subject="E02778", filer="MINE"),
            _holding(subject="E99999", filer="OTHER"),  # about a different company
        ]}
        with self._patch(mapping):
            out = edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=0)
        self.assertIn("大量保有", out)
        self.assertIn("MINE", out)
        self.assertNotIn("OTHER", out)

    def test_doc_type_label_distinguishes_new_and_change(self):
        mapping = {"2026-06-22": [
            _holding(doc_type="350", doc_id="NEW"),
            _holding(doc_type="360", doc_id="CHG", when="2026-06-22 16:00"),
        ]}
        with self._patch(mapping):
            out = edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=0)
        self.assertIn("5%+ position", out)
        self.assertIn("change report", out)

    def test_unknown_code_skips_without_scanning(self):
        fd = mock.Mock(side_effect=_by_date({}))
        with mock.patch.object(edinet_common, "fetch_documents", fd):
            out = edinet_holdings.get_large_holdings("0000.T", "2026-06-22", look_back_days=10)
        self.assertIn("no EDINET code on file", out)
        fd.assert_not_called()  # don't scan dates for a subject we can't match

    def test_no_reports_returns_informative_line(self):
        with self._patch({"2026-06-22": [_holding(subject="E99999")]}):
            out = edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=0)
        self.assertIn("No EDINET large-shareholding or tender-offer filings", out)

    def test_surfaces_tender_offer_family_with_correct_labels(self):
        # TOB filings tag the target in subjectEdinetCode too (verified live), so
        # they surface alongside 大量保有. Lock the code→label mapping (240 = launch,
        # 270 = result) and confirm a 訂正 amendment (250) is kept — a TOB correction
        # can change price/terms — while an unmapped docType is still dropped.
        mapping = {"2026-06-22": [
            _holding(subject="E02778", doc_type="240", filer="BIDDER", doc_id="LAUNCH"),
            _holding(subject="E02778", doc_type="290", filer="TARGET", doc_id="OPINION",
                     when="2026-06-22 15:30"),
            _holding(subject="E02778", doc_type="260", filer="BIDDER", doc_id="WITHDRAW",
                     when="2026-06-22 15:45"),
            _holding(subject="E02778", doc_type="270", filer="BIDDER", doc_id="RESULT",
                     when="2026-06-22 16:00"),
            _holding(subject="E02778", doc_type="250", filer="BIDDER", doc_id="AMEND",
                     when="2026-06-22 17:00"),
            _holding(subject="E02778", doc_type="030", filer="UNRELATED", doc_id="OTHER",
                     when="2026-06-22 18:00"),  # not an ownership/control docType
        ]}
        with self._patch(mapping):
            out = edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=0)
        self.assertIn("Takeover bid launched", out)
        self.assertIn("Target board opinion on TOB", out)
        self.assertIn("Takeover bid withdrawn", out)  # 260 is material, not noise
        self.assertIn("Takeover bid result", out)
        self.assertIn("Takeover bid amended", out)  # 訂正 250 kept (price/terms may change)
        self.assertNotIn("UNRELATED", out)  # unmapped docType still filtered out

    def test_self_heal_learns_issuer_codes_while_scanning(self):
        # Scanning to find holdings about 9984 also learns other issuers' own
        # codes from their filings — even ones not in the seed.
        self.assertIsNone(cm.resolve_edinet_code("0000.T"))
        mapping = {"2026-06-22": [
            _holding(subject="E02778"),
            _own_report(sec_code="00000", edinet_code="E70000"),  # new issuer
        ]}
        with self._patch(mapping):
            edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=0)
        self.assertEqual(cm.resolve_edinet_code("0000.T"), "E70000")

    def test_fetch_error_degrades_without_raising(self):
        with mock.patch.object(edinet_common, "fetch_documents", side_effect=RuntimeError("boom")):
            out = edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=0)
        self.assertIn("<large-shareholding data unavailable: RuntimeError>", out)

    def test_malformed_curr_date_degrades_without_raising(self):
        with self._patch({}):
            out = edinet_holdings.get_large_holdings("9984.T", "not-a-date")
        self.assertIn("<large-shareholding data unavailable: ValueError>", out)

    def test_window_shares_cache_with_news(self):
        # documents_on memoization is shared via edinet_common, so a date fetched
        # here is not re-fetched on a second scan.
        fd = mock.Mock(side_effect=_by_date({"2026-06-20": [], "2026-06-21": [], "2026-06-22": []}))
        with mock.patch.object(edinet_common, "fetch_documents", fd):
            edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=2)
            first = fd.call_count
            edinet_holdings.get_large_holdings("9984.T", "2026-06-22", look_back_days=2)
        self.assertEqual(fd.call_count, first)  # second scan fully cached


if __name__ == "__main__":
    unittest.main()
