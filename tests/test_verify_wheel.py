from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

from scripts.verify_wheel import _reachable_web_assets


def test_reachable_web_assets_follow_lazy_chunk_graph(tmp_path: Path) -> None:
    wheel = tmp_path / "assets.zip"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "tradingagents/web/static/index.html",
            '<script src="/assets/index.js"></script>'
            '<link href="/assets/index.css" rel="stylesheet">',
        )
        archive.writestr(
            "tradingagents/web/static/assets/index.js",
            'import("./RunDetail.js");const deps=["assets/Markdown.js"]',
        )
        archive.writestr(
            "tradingagents/web/static/assets/RunDetail.js",
            'import shared from "./shared.js";',
        )
        archive.writestr("tradingagents/web/static/assets/Markdown.js", "")
        archive.writestr("tradingagents/web/static/assets/shared.js", "")
        archive.writestr("tradingagents/web/static/assets/orphan.js", "")

    with ZipFile(wheel) as archive:
        index = archive.read("tradingagents/web/static/index.html").decode("utf-8")
        assert _reachable_web_assets(archive, index) == {
            "tradingagents/web/static/assets/index.js",
            "tradingagents/web/static/assets/index.css",
            "tradingagents/web/static/assets/RunDetail.js",
            "tradingagents/web/static/assets/Markdown.js",
            "tradingagents/web/static/assets/shared.js",
        }
