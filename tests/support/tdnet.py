"""Shared offline fixtures for tdnet contracts."""


def _row(
    code="72030",
    title="2026年3月期決算短信",
    pdf="/inbs/140120260710590974.pdf",
    when="2026/07/10 16:00",
    cls="odd",
):
    return (
        f'<tr class="{cls}">'
        f'<td class="time" nowrap>{when}</td>'
        f'<td class="code" nowrap>{code}</td>'
        f'<td class="companyname">トヨタ自</td>'
        f'<td class="title" align=left><a target="_blank" href="{pdf}">{title}</a></td>'
        f'<td class="xbrl"><br></td>'
        f'<td class="exchange">東</td>'
        f'<td class="update"><br></td>'
        f"</tr>"
    )


def _page(*rows: str, count: int | None = None) -> str:
    # Real TDnet renders the count as ``<span id="result">N件</span>`` (件 inside
    # the span, right after the digits) — keep this in lockstep with the markup.
    n = len(rows) if count is None else count
    return (
        f'<html><body><h4><span id="result">{n}件</span>の結果</h4>'
        f'<table id="maintable">' + "".join(rows) + "</table></body></html>"
    )
