"""jp_news assembler: EDINET disclosures + Google-News media, combined."""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.jp import jp_news

_EDINET_DATA = "## 4568.T EDINET disclosures, from a to b:\n\n### 有価証券報告書"
_TDNET_DATA = "## 4568.T timely disclosures (TDnet 適時開示), from a to b:\n\n### 自己株式の取得"
_MEDIA_DATA = "## 4568.T News (media, Google News), from a to b:\n\n### 決算を発表"
_EDINET_EMPTY = "No EDINET disclosures found for 4568.T between a and b"
_TDNET_EMPTY = "No TDnet disclosures found for 4568.T between a and b"
_MEDIA_EMPTY = "No Google News found for 4568.T between a and b"


def _spec(value):
    """A string → mock return_value; an exception → mock side_effect (raises)."""
    is_exc = isinstance(value, BaseException) or (
        isinstance(value, type) and issubclass(value, BaseException)
    )
    return {"side_effect": value} if is_exc else {"return_value": value}


def _run(edinet, media, tdnet=_TDNET_EMPTY):
    with mock.patch.object(jp_news, "_edinet_news", **_spec(edinet)), \
            mock.patch.object(jp_news, "_tdnet_news", **_spec(tdnet)), \
            mock.patch.object(jp_news, "_google_news", **_spec(media)):
        return jp_news.get_news("4568.T", "a", "b")


@pytest.mark.unit
class JpNewsAssemblerTests(unittest.TestCase):
    def test_all_present_are_combined_in_order(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=_TDNET_DATA)
        self.assertIn("有価証券報告書", out)
        self.assertIn("自己株式の取得", out)
        self.assertIn("決算を発表", out)
        # statutory filings, then timely disclosures, then media
        self.assertLess(out.index("EDINET"), out.index("TDnet"))
        self.assertLess(out.index("TDnet"), out.index("media"))

    def test_tdnet_only_present(self):
        out = _run(_EDINET_EMPTY, _MEDIA_EMPTY, tdnet=_TDNET_DATA)
        self.assertIn("自己株式の取得", out)
        self.assertNotIn("No TDnet disclosures found", out)

    def test_tdnet_error_does_not_suppress_others(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA, tdnet=RuntimeError("boom"))
        self.assertIn("有価証券報告書", out)
        self.assertIn("決算を発表", out)

    def test_only_edinet_present_drops_empty_media_line(self):
        out = _run(_EDINET_DATA, _MEDIA_EMPTY)
        self.assertIn("有価証券報告書", out)
        self.assertNotIn("No Google News found", out)  # empty block omitted

    def test_only_media_present_drops_empty_edinet_line(self):
        out = _run(_EDINET_EMPTY, _MEDIA_DATA)
        self.assertIn("決算を発表", out)
        self.assertNotIn("No EDINET disclosures found", out)

    def test_edinet_error_does_not_suppress_media(self):
        # EDINET needs a key and can raise; the keyless media feed must survive.
        out = _run(VendorNotConfiguredError("EDINET_API_KEY unset"), _MEDIA_DATA)
        self.assertIn("決算を発表", out)

    def test_media_error_does_not_suppress_edinet(self):
        out = _run(_EDINET_DATA, RuntimeError("boom"))
        self.assertIn("有価証券報告書", out)

    def test_extended_window_is_clamped_only_for_tdnet(self):
        with mock.patch.object(jp_news, "_edinet_news", return_value=_EDINET_DATA) as edinet, \
                mock.patch.object(jp_news, "_tdnet_news", return_value=_TDNET_DATA) as tdnet, \
                mock.patch.object(jp_news, "_google_news", return_value=_MEDIA_DATA) as media:
            jp_news.get_news("4568.T", "2026-04-19", "2026-07-17")

        edinet.assert_called_once_with("4568.T", "2026-04-19", "2026-07-17")
        tdnet.assert_called_once_with("4568.T", "2026-06-17", "2026-07-17")
        media.assert_called_once_with("4568.T", "2026-04-19", "2026-07-17")

    def test_both_empty_raises_no_market_data(self):
        with self.assertRaises(NoMarketDataError):
            _run(_EDINET_EMPTY, _MEDIA_EMPTY)

    def test_edinet_error_and_empty_media_raises(self):
        with self.assertRaises(NoMarketDataError) as ctx:
            _run(RuntimeError("boom"), _MEDIA_EMPTY)
        self.assertIn("<EDINET unavailable: RuntimeError>", ctx.exception.availability_notes)


if __name__ == "__main__":
    unittest.main()
