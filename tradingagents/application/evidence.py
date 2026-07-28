"""Shared exact-content grouping helpers for evidence presentation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .contracts import EvidenceItem


@dataclass(frozen=True)
class EvidenceContentGroup:
    """Evidence items whose complete non-empty bodies are byte-identical."""

    items: tuple[EvidenceItem, ...]

    @property
    def canonical(self) -> EvidenceItem:
        return self.items[0]

    @property
    def refs(self) -> tuple[str, ...]:
        return tuple(item.ref for item in self.items)

    @property
    def content(self) -> str | None:
        return self.canonical.content


def group_evidence_by_content(
    items: Iterable[EvidenceItem],
) -> tuple[EvidenceContentGroup, ...]:
    """Group only exact, non-empty bodies while retaining stable item order."""
    groups: dict[tuple[str, str], list[EvidenceItem]] = {}
    for item in items:
        key = (
            ("content", item.content)
            if item.content
            else ("ref", item.ref)
        )
        groups.setdefault(key, []).append(item)
    return tuple(
        EvidenceContentGroup(items=tuple(group))
        for group in groups.values()
    )
