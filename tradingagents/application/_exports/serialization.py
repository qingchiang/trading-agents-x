"""Shared deterministic serialization and archive helpers."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Mapping
from typing import Any


def json_bytes(value: Any) -> bytes:
    """Serialize a durable export payload with the established JSON format."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def zip_bytes(
    payloads: Mapping[str, bytes | str],
    *,
    compresslevel: int | None = None,
) -> bytes:
    """Write payloads in caller-provided order using the established ZIP format."""
    output = io.BytesIO()
    options = {"compresslevel": compresslevel} if compresslevel is not None else {}
    with zipfile.ZipFile(
        output,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        **options,
    ) as archive:
        for path, content in payloads.items():
            archive.writestr(path, content)
    return output.getvalue()
