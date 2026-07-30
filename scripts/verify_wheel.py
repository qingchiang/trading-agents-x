"""Verify that a release wheel contains only the independent runtime surface."""

from __future__ import annotations

import argparse
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

_REQUIRED_FILES = {
    "cli/main.py",
    "tradingagents/client.py",
    "tradingagents/graph/research_graph.py",
    "tradingagents/persistence/alembic/versions/0001_markdown_research.py",
    "tradingagents/web/static/index.html",
}
_FORBIDDEN_FILES = {
    "tradingagents/reporting.py",
    "tradingagents/agents/utils/memory.py",
    "tradingagents/graph/trading_graph.py",
    "tradingagents/graph/checkpointer.py",
    "tradingagents/graph/reflection.py",
}
_FORBIDDEN_REQUIREMENTS = {
    "backtrader",
    "langchain-experimental",
    "pytz",
    "redis",
    "setuptools",
    "tqdm",
}


def verify(wheel: Path) -> None:
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(_REQUIRED_FILES - names)
        forbidden = sorted(_FORBIDDEN_FILES & names)
        if missing:
            raise ValueError(f"wheel is missing required files: {', '.join(missing)}")
        if forbidden:
            raise ValueError(
                f"wheel contains removed runtime files: {', '.join(forbidden)}"
            )
        if not any(
            name.startswith("tradingagents/web/static/assets/")
            and name.endswith(".js")
            for name in names
        ):
            raise ValueError("wheel is missing the compiled Web JavaScript asset")
        if not any(
            name.startswith("tradingagents/web/static/assets/")
            and name.endswith(".css")
            for name in names
        ):
            raise ValueError("wheel is missing the compiled Web CSS asset")
        if not any(name.endswith(".dist-info/licenses/LICENSE") for name in names):
            raise ValueError("wheel is missing LICENSE")
        if not any(name.endswith(".dist-info/licenses/NOTICE") for name in names):
            raise ValueError("wheel is missing NOTICE")

        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        if metadata["License-Expression"] != "Apache-2.0":
            raise ValueError("wheel License-Expression must be Apache-2.0")
        requirements = {
            requirement.split(";", 1)[0]
            .split("[", 1)[0]
            .split(">", 1)[0]
            .split("<", 1)[0]
            .split("=", 1)[0]
            .strip()
            .casefold()
            for requirement in metadata.get_all("Requires-Dist", [])
        }
        stale = sorted(requirements & _FORBIDDEN_REQUIREMENTS)
        if stale:
            raise ValueError(
                f"wheel declares removed runtime dependencies: {', '.join(stale)}"
            )

        entry_points_name = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_name).decode("utf-8")
        if "tradingagents = cli.main:app" not in entry_points:
            raise ValueError("wheel is missing the tradingagents CLI entry point")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    verify(args.wheel.expanduser().resolve())
    print(f"Verified wheel: {args.wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
