"""jp_news assembler: EDINET disclosures + Google-News media, combined."""
import unittest
from unittest import mock

import pytest

from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.dataflows.jp import jp_news

_EDINET_DATA = "## 4568.T EDINET disclosures, from a to b:\n\n### 有価証券報告書"
_MEDIA_DATA = "## 4568.T News (media, Google News), from a to b:\n\n### 決算を発表"
_EDINET_EMPTY = "No EDINET disclosures found for 4568.T between a and b"
_MEDIA_EMPTY = "No Google News found for 4568.T between a and b"


def _spec(value):
    """A string → mock return_value; an exception → mock side_effect (raises)."""
    is_exc = isinstance(value, BaseException) or (
        isinstance(value, type) and issubclass(value, BaseException)
    )
    return {"side_effect": value} if is_exc else {"return_value": value}


def _run(edinet, media):
    with mock.patch.object(jp_news, "_edinet_news", **_spec(edinet)), \
            mock.patch.object(jp_news, "_google_news", **_spec(media)):
        return jp_news.get_news("4568.T", "a", "b")


@pytest.mark.unit
class JpNewsAssemblerTests(unittest.TestCase):
    def test_both_present_are_combined(self):
        out = _run(_EDINET_DATA, _MEDIA_DATA)
        self.assertIn("有価証券報告書", out)
        self.assertIn("決算を発表", out)
        self.assertLess(out.index("EDINET"), out.index("media"))  # disclosures first

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

    def test_both_empty_raises_no_market_data(self):
        with self.assertRaises(NoMarketDataError):
            _run(_EDINET_EMPTY, _MEDIA_EMPTY)

    def test_edinet_error_and_empty_media_raises(self):
        with self.assertRaises(NoMarketDataError):
            _run(RuntimeError("boom"), _MEDIA_EMPTY)


if __name__ == "__main__":
    unittest.main()
