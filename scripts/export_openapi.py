"""Export the deterministic FastAPI schema consumed by the React client."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from tradingagents.application.settings import AppSettings
from tradingagents.web import create_app


def render_schema() -> str:
    with tempfile.TemporaryDirectory(prefix="tradingagents-openapi-") as root:
        base = Path(root)
        settings = AppSettings.from_env(
            environ={
                "TRADINGAGENTS_HOME": str(base),
                "TRADINGAGENTS_DATABASE_PATH": str(base / "schema.db"),
            },
            load_env_files=False,
        )
        schema = create_app(settings).openapi()
    return json.dumps(
        schema,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/openapi.json"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render_schema()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != rendered:
            print(f"OpenAPI schema is stale: {args.output}")
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
