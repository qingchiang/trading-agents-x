"""Canonical evidence references for human-readable research Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass

from tradingagents.application.contracts import ResearchWarning

_EVIDENCE_CITATION_RE = re.compile(r"\[\^(ev_[A-Za-z0-9_-]+)\]")
_EVIDENCE_DEFINITION_RE = re.compile(
    r"^(?P<indent> {0,3})\[\^(?P<ref>ev_[A-Za-z0-9_-]+)\]:"
)
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})")
_INDENTED_CONTINUATION_RE = re.compile(r"^(?: {4}|\t)")
_INLINE_CODE_RE = re.compile(r"(`+)(.*?)(\1)")


@dataclass(frozen=True)
class NormalizedEvidenceMarkdown:
    """Readable Markdown plus its validated, first-use evidence references."""

    markdown: str
    evidence_refs: tuple[str, ...]
    warnings: tuple[ResearchWarning, ...]


def normalize_evidence_markdown(
    markdown: str,
    *,
    allowed_refs: set[str],
    source: str,
    warning_code: str = "research.unknown_evidence_ref",
) -> NormalizedEvidenceMarkdown:
    """Remove model-authored definitions and validate inline evidence refs.

    Evidence definitions are deliberately discarded because the sealed ledger,
    not model-authored source prose, is the audit authority. Fenced and inline
    code are left untouched.
    """

    without_definitions = _remove_evidence_definitions(markdown)
    cited_refs: list[str] = []
    warnings: list[ResearchWarning] = []
    output_lines: list[str] = []
    fence: str | None = None

    for line in without_definitions.splitlines(keepends=True):
        marker = _FENCE_RE.match(line)
        if marker is not None:
            token = marker.group("fence")
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            output_lines.append(line)
            continue
        if fence is not None:
            output_lines.append(line)
            continue
        output_lines.append(
            _normalize_inline_citations(
                line,
                allowed_refs=allowed_refs,
                source=source,
                warning_code=warning_code,
                cited_refs=cited_refs,
                warnings=warnings,
            )
        )

    return NormalizedEvidenceMarkdown(
        markdown="".join(output_lines).strip(),
        evidence_refs=tuple(dict.fromkeys(cited_refs)),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _remove_evidence_definitions(markdown: str) -> str:
    lines = markdown.splitlines(keepends=True)
    output: list[str] = []
    index = 0
    fence: str | None = None
    while index < len(lines):
        line = lines[index]
        marker = _FENCE_RE.match(line)
        if marker is not None:
            token = marker.group("fence")
            if fence is None:
                fence = token[0]
            elif token[0] == fence:
                fence = None
            output.append(line)
            index += 1
            continue
        if fence is not None or _EVIDENCE_DEFINITION_RE.match(line) is None:
            output.append(line)
            index += 1
            continue

        index += 1
        while index < len(lines):
            if _INDENTED_CONTINUATION_RE.match(lines[index]):
                index += 1
                continue
            if (
                not lines[index].strip()
                and index + 1 < len(lines)
                and _INDENTED_CONTINUATION_RE.match(lines[index + 1])
            ):
                index += 1
                continue
            break
        while index < len(lines) and not lines[index].strip():
            index += 1
        if output and output[-1].strip() and index < len(lines):
            output.append("\n")
    return "".join(output)


def _normalize_inline_citations(
    line: str,
    *,
    allowed_refs: set[str],
    source: str,
    warning_code: str,
    cited_refs: list[str],
    warnings: list[ResearchWarning],
) -> str:
    parts: list[str] = []
    cursor = 0
    for code in _INLINE_CODE_RE.finditer(line):
        parts.append(
            _replace_citations(
                line[cursor : code.start()],
                allowed_refs=allowed_refs,
                source=source,
                warning_code=warning_code,
                cited_refs=cited_refs,
                warnings=warnings,
            )
        )
        parts.append(code.group(0))
        cursor = code.end()
    parts.append(
        _replace_citations(
            line[cursor:],
            allowed_refs=allowed_refs,
            source=source,
            warning_code=warning_code,
            cited_refs=cited_refs,
            warnings=warnings,
        )
    )
    return "".join(parts)


def _replace_citations(
    text: str,
    *,
    allowed_refs: set[str],
    source: str,
    warning_code: str,
    cited_refs: list[str],
    warnings: list[ResearchWarning],
) -> str:
    def replace(match: re.Match[str]) -> str:
        ref = match.group(1)
        if ref in allowed_refs:
            cited_refs.append(ref)
            return match.group(0)
        warnings.append(
            ResearchWarning(
                code=warning_code,
                message="An unknown research evidence reference was ignored.",
                source=source,
            )
        )
        return ""

    return _EVIDENCE_CITATION_RE.sub(replace, text)
