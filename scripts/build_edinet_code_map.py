"""Build the committed EDINET code-map seed from a manually downloaded CSV.

This is a one-off bootstrap tool; it is NOT imported at runtime. It distils
EDINET's full issuer table (``EdinetcodeDlInfo.csv``) into the small
``secCode (4-digit base) -> EDINET code`` snapshot that stage 3.6 uses to
resolve a Tokyo ticker to the ``subjectEdinetCode`` of its large-shareholding
(大量保有報告書) filings.

Why a seed plus runtime write-back instead of scraping the table live:
EDINET no longer offers a clean static download for the code table (the
current ``weee0010.aspx`` button is a GeneXus/ASP.NET AJAX postback). So we
commit a possibly-stale snapshot as a starting point; the runtime then learns
new issuers from the ``documents.json`` it already fetches and merges them into
a local cache. The seed only needs to be regenerated occasionally.

Getting the CSV (manual, in a browser):
    EDINET -> 書類検索 -> EDINETコード一覧 -> ダウンロード (weee0010.aspx).
    You get ``Edinetcode_YYYYMMDD.zip``; unzip to ``EdinetcodeDlInfo.csv``
    (Shift-JIS / cp932, CRLF, line 1 = download info, line 2 = header).

Usage:
    python scripts/build_edinet_code_map.py tmp/EdinetcodeDlInfo.csv
    python scripts/build_edinet_code_map.py tmp/EdinetcodeDlInfo.csv -o <path>
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

# Columns in EdinetcodeDlInfo.csv (header is line 2). Index -> meaning:
COL_EDINET_CODE = 0  # ＥＤＩＮＥＴコード, e.g. "E00004"
COL_LISTED = 2  # 上場区分: "上場" / "非上場" / ""
COL_SEC_CODE = 11  # 証券コード, 5 digits with a trailing market digit (e.g. "13760")

LISTED = "上場"

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parent.parent
    / "tradingagents"
    / "dataflows"
    / "data"
    / "edinet_code_map.json"
)


def build_map(csv_path: Path) -> dict[str, str]:
    """Return ``{4-digit base secCode: EDINET code}`` for listed issuers only."""
    codes: dict[str, str] = {}
    with csv_path.open(encoding="cp932", newline="") as f:
        rows = csv.reader(f)
        next(rows, None)  # line 1: download info
        next(rows, None)  # line 2: header
        for row in rows:
            if len(row) <= COL_SEC_CODE:
                continue
            sec = row[COL_SEC_CODE].strip()
            if row[COL_LISTED].strip() != LISTED or not sec:
                continue
            base = sec[:4]  # all listed common stock codes end in "0"
            edinet = row[COL_EDINET_CODE].strip()
            if base in codes and codes[base] != edinet:
                raise SystemExit(
                    f"secCode base {base} maps to both {codes[base]} and {edinet}"
                )
            codes[base] = edinet
    return dict(sorted(codes.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="path to EdinetcodeDlInfo.csv (cp932)")
    parser.add_argument(
        "-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="output JSON path"
    )
    args = parser.parse_args(argv)

    if not args.csv.exists():
        parser.error(f"CSV not found: {args.csv}")

    codes = build_map(args.csv)
    payload = {
        "schema": "edinet-code-map/1",
        "source": "EDINET EdinetcodeDlInfo.csv (上場 issuers only)",
        "generated": date.today().isoformat(),
        "count": len(codes),
        "note": "key = 4-digit base securities code; value = EDINET code (E######)",
        "codes": codes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(codes)} entries -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
