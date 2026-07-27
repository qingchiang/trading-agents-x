"""Generate the TypeScript schema map consumed by the Web client."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "frontend" / "openapi.json"
DEFAULT_OUTPUT = ROOT / "frontend" / "src" / "api" / "types.generated.ts"
IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _property_name(value: str) -> str:
    return value if IDENTIFIER.fullmatch(value) else json.dumps(value)


def _reference(value: str) -> str:
    prefix = "#/components/schemas/"
    if not value.startswith(prefix):
        return "unknown"
    name = value.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
    return f'components["schemas"][{json.dumps(name)}]'


def _schema_type(schema: dict[str, Any], level: int = 0) -> str:
    if "$ref" in schema:
        return _reference(str(schema["$ref"]))
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    for union_key in ("anyOf", "oneOf"):
        if union_key in schema:
            members = [_schema_type(member, level) for member in schema[union_key]]
            return " | ".join(dict.fromkeys(members))
    if "allOf" in schema:
        return " & ".join(_schema_type(member, level) for member in schema["allOf"])

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(
            dict.fromkeys(
                _schema_type({**schema, "type": member}, level)
                for member in schema_type
            )
        )
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "null":
        return "null"
    if schema_type == "array":
        item_type = _schema_type(schema.get("items", {}), level)
        return f"({item_type})[]" if " | " in item_type else f"{item_type}[]"
    if schema_type == "object" or "properties" in schema:
        return _object_type(schema, level)
    return "unknown"


def _object_type(schema: dict[str, Any], level: int) -> str:
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    indent = "  " * level
    child_indent = "  " * (level + 1)
    lines = ["{"]
    for name, value in properties.items():
        optional = "" if name in required else "?"
        lines.append(
            f"{child_indent}{_property_name(name)}{optional}: "
            f"{_schema_type(value, level + 1)};"
        )
    lines.append(f"{indent}}}")
    object_type = "\n".join(lines)
    additional = schema.get("additionalProperties")
    if additional:
        value_type = "unknown" if additional is True else _schema_type(additional, level)
        if properties:
            return f"({object_type} & Record<string, {value_type}>)"
        return f"Record<string, {value_type}>"
    return object_type


def render(schema_path: Path) -> str:
    document = json.loads(schema_path.read_text(encoding="utf-8"))
    schemas = document.get("components", {}).get("schemas", {})
    lines = [
        "/*",
        " * This file is generated from frontend/openapi.json.",
        " * Run `npm run openapi:generate` from frontend/ to update it.",
        " */",
        "",
        "export interface components {",
        "  schemas: {",
    ]
    for name, schema in schemas.items():
        rendered = _schema_type(schema, 2)
        lines.append(f"    {_property_name(name)}: {rendered};")
    lines.extend(["  };", "}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = render(args.schema)
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != generated:
            print(f"TypeScript API types are stale: {args.output}")
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
