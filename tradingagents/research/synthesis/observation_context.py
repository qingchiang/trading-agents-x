"""Conservative prompt-only aliases for repeatedly retrieved observations."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

OBSERVATION_ALIAS_RULES = (
    "Items with same_observation_as inherit that item's fields, then apply their own "
    "explicit fields. observation_groups retain each original ref and its retrieval "
    "fields as exact path/value overrides; those times belong to that ref only. "
    "Repeated retrievals are one observation, not independent corroboration. "
    "A query with content_from_ref shares only that referenced query's content. "
    "All original Evidence refs remain valid; never invent or substitute refs. "
)


def observation_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compare every fact except ref and explicitly known retrieval metadata.

    In particular, do not recursively remove fields called retrieved_at inside
    producer values: they can themselves be facts. Unknown provenance differences
    conservatively prevent grouping.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        provenance = item.get("provenance") or {}
        identity = provenance.get("observation_identity")
        if not isinstance(identity, str) or not re.fullmatch(r"ob_[a-f0-9]{16}", identity):
            continue
        if not isinstance(provenance.get("observation"), dict):
            continue
        comparison = deepcopy(item)
        comparison.pop("ref", None)
        paths = [
            ["provenance", "observation", "retrieved_at"],
            ["provenance", "retrieved_at"],
            *[["origins", index, "retrieved_at"] for index in range(len(item.get("origins", [])))],
        ]
        retrievals = []
        for path in paths:
            parent = comparison
            for part in path[:-1]:
                if isinstance(parent, dict):
                    parent = parent.get(part)
                elif isinstance(parent, list) and isinstance(part, int):
                    parent = parent[part]
                else:
                    parent = None
                    break
            if isinstance(parent, dict) and path[-1] in parent:
                retrievals.append({"path": path, "value": parent[path[-1]]})
                parent[path[-1]] = None
        digest = hashlib.sha256(json.dumps(comparison, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        groups.setdefault(digest, []).append({"ref": item["ref"], "fields": retrievals})
    return [{"canonical_ref": members[0]["ref"], "retrievals": members}
            for members in groups.values() if len(members) > 1]


def observation_aliases(groups: list[dict[str, Any]]) -> dict[str, str]:
    return {entry["ref"]: group["canonical_ref"]
            for group in groups for entry in group["retrievals"][1:]}
