"""Validated structured-output execution with one bounded recovery attempt."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from tradingagents.application.contracts import ArtifactGenerationMethod

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
SemanticValidator = Callable[[StructuredModel], StructuredModel]
EventWriter = Callable[[dict[str, Any]], None]

_JSON_FENCE_RE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n?(?P<body>.*?)[ \t]*\r?\n?```\Z",
    re.IGNORECASE | re.DOTALL,
)


class StructuredOutputError(RuntimeError):
    """A provider response could not satisfy the requested typed contract."""

    def __init__(self, *, node: str, schema: str, reason_code: str):
        self.node = node
        self.schema = schema
        self.reason_code = reason_code
        super().__init__(
            f"Validated {schema} output failed for {node} ({reason_code})"
        )


@dataclass(frozen=True)
class StructuredOutputResult(Generic[StructuredModel]):
    value: StructuredModel
    generation_method: ArtifactGenerationMethod


class _InvalidOutput(ValueError):
    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class StructuredOutputRunner(Generic[StructuredModel]):
    """Run the preferred typed transport, local recovery, then one JSON retry."""

    def __init__(
        self,
        *,
        llm: Any,
        schema: type[StructuredModel],
        validator: SemanticValidator[StructuredModel],
        node: str,
        event_writer: EventWriter | None = None,
    ):
        self.llm = llm
        self.schema = schema
        self.validator = validator
        self.node = node
        self.event_writer = event_writer

    def invoke(
        self,
        prompt: str,
        *,
        example: dict[str, Any],
        allowed_evidence_refs: tuple[str, ...],
        allowed_memory_refs: tuple[str, ...] = (),
    ) -> StructuredOutputResult[StructuredModel]:
        primary_reason = "structured_binding_error"
        primary_generation_method = _primary_generation_method(self.llm)
        bind_kwargs = _structured_output_kwargs(self.llm)
        primary_prompt = (
            _primary_json_prompt(
                prompt,
                schema=self.schema,
                example=example,
                allowed_evidence_refs=allowed_evidence_refs,
                allowed_memory_refs=allowed_memory_refs,
            )
            if primary_generation_method is ArtifactGenerationMethod.JSON_MODE
            else prompt
        )
        try:
            primary = self.llm.with_structured_output(
                self.schema,
                include_raw=True,
                **bind_kwargs,
            )
        except Exception:
            primary = None
        if primary is not None:
            try:
                response = primary.invoke(primary_prompt)
            except Exception:
                primary_reason = "provider_error"
            else:
                parsed, raw = _unpack_response(response, self.schema)
                if _is_truncated(raw):
                    primary_reason = "output_truncated"
                elif parsed is not None:
                    try:
                        value = self._validate(parsed)
                    except _InvalidOutput as exc:
                        primary_reason = exc.reason_code
                    else:
                        return StructuredOutputResult(
                            value=value,
                            generation_method=primary_generation_method,
                        )
                else:
                    try:
                        value = self._validate(_strict_json_object(raw))
                    except _InvalidOutput as exc:
                        primary_reason = exc.reason_code
                    else:
                        self._emit(
                            "node.output_recovered",
                            method=(
                                ArtifactGenerationMethod.RAW_JSON_RECOVERED
                            ),
                            reason_code="structured_parse_recovered",
                        )
                        return StructuredOutputResult(
                            value=value,
                            generation_method=(
                                ArtifactGenerationMethod.RAW_JSON_RECOVERED
                            ),
                        )

        self._emit(
            "node.output_retry",
            method=ArtifactGenerationMethod.JSON_MODE_RECOVERED,
            reason_code=primary_reason,
        )
        recovery_prompt = _recovery_prompt(
            prompt,
            schema=self.schema,
            example=example,
            allowed_evidence_refs=allowed_evidence_refs,
            allowed_memory_refs=allowed_memory_refs,
        )
        try:
            recovery = self.llm.with_structured_output(
                self.schema,
                method="json_mode",
                include_raw=True,
                **bind_kwargs,
            )
        except Exception:
            recovery = None

        failure_reason = "structured_binding_error"
        try:
            response = (
                recovery.invoke(recovery_prompt)
                if recovery is not None
                else self.llm.invoke(recovery_prompt, **bind_kwargs)
            )
        except Exception:
            failure_reason = "provider_error"
        else:
            parsed, raw = _unpack_response(response, self.schema)
            if _is_truncated(raw):
                failure_reason = "output_truncated"
                candidate = None
            else:
                candidate = parsed
            if candidate is None and failure_reason != "output_truncated":
                try:
                    candidate = _strict_json_object(raw)
                except _InvalidOutput as exc:
                    failure_reason = exc.reason_code
                    candidate = None
            if candidate is not None:
                try:
                    value = self._validate(candidate)
                except _InvalidOutput as exc:
                    failure_reason = exc.reason_code
                else:
                    self._emit(
                        "node.output_recovered",
                        method=(
                            ArtifactGenerationMethod.JSON_MODE_RECOVERED
                        ),
                        reason_code=primary_reason,
                    )
                    return StructuredOutputResult(
                        value=value,
                        generation_method=(
                            ArtifactGenerationMethod.JSON_MODE_RECOVERED
                        ),
                    )

        self._emit(
            "node.output_failed",
            method=ArtifactGenerationMethod.JSON_MODE_RECOVERED,
            reason_code=failure_reason,
        )
        raise StructuredOutputError(
            node=self.node,
            schema=self.schema.__name__,
            reason_code=failure_reason,
        )

    def _validate(self, candidate: Any) -> StructuredModel:
        try:
            value = self.schema.model_validate(candidate)
        except Exception as exc:
            raise _InvalidOutput("schema_validation") from exc
        try:
            return self.validator(value)
        except Exception as exc:
            raise _InvalidOutput("semantic_validation") from exc

    def _emit(
        self,
        event_type: str,
        *,
        method: ArtifactGenerationMethod,
        reason_code: str,
    ) -> None:
        if self.event_writer is None:
            return
        self.event_writer(
            {
                "event_type": event_type,
                "node": self.node,
                "payload": {
                    "schema": self.schema.__name__,
                    "method": method.value,
                    "reason_code": reason_code,
                },
            }
        )


def _primary_generation_method(llm: Any) -> ArtifactGenerationMethod:
    preferred = getattr(llm, "preferred_structured_output_method", None)
    if preferred == "json_mode":
        return ArtifactGenerationMethod.JSON_MODE
    return ArtifactGenerationMethod.TOOL_CALL


def _structured_output_kwargs(llm: Any) -> dict[str, Any]:
    max_tokens = getattr(llm, "structured_output_max_tokens", None)
    if isinstance(max_tokens, int) and max_tokens > 0:
        return {"max_tokens": max_tokens}
    return {}


def _unpack_response(
    response: Any,
    schema: type[StructuredModel],
) -> tuple[Any | None, Any]:
    if isinstance(response, schema):
        return response, None
    if isinstance(response, dict) and (
        "parsed" in response or "raw" in response or "parsing_error" in response
    ):
        return response.get("parsed"), response.get("raw")
    if isinstance(response, dict):
        return response, None
    return None, response


def _is_truncated(raw: Any) -> bool:
    metadata = getattr(raw, "response_metadata", None)
    if not isinstance(metadata, dict) and isinstance(raw, dict):
        metadata = raw.get("response_metadata")
    if not isinstance(metadata, dict):
        return False
    finish_reason = metadata.get("finish_reason")
    return finish_reason in {"length", "max_tokens", "max_output_tokens"}


def _strict_json_object(raw: Any) -> dict[str, Any]:
    content = _raw_content(raw).strip()
    if not content:
        raise _InvalidOutput("empty_response")
    fenced = _JSON_FENCE_RE.fullmatch(content)
    if fenced:
        content = fenced.group("body").strip()
    try:
        value = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise _InvalidOutput("non_json_response") from exc
    if not isinstance(value, dict):
        raise _InvalidOutput("schema_validation")
    return value


def _raw_content(raw: Any) -> str:
    content = getattr(raw, "content", raw)
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _recovery_prompt(
    original_prompt: str,
    *,
    schema: type[BaseModel],
    example: dict[str, Any],
    allowed_evidence_refs: tuple[str, ...],
    allowed_memory_refs: tuple[str, ...],
) -> str:
    return _json_contract_prompt(
        original_prompt,
        schema=schema,
        example=example,
        allowed_evidence_refs=allowed_evidence_refs,
        allowed_memory_refs=allowed_memory_refs,
        retry=True,
    )


def _primary_json_prompt(
    original_prompt: str,
    *,
    schema: type[BaseModel],
    example: dict[str, Any],
    allowed_evidence_refs: tuple[str, ...],
    allowed_memory_refs: tuple[str, ...],
) -> str:
    return _json_contract_prompt(
        original_prompt,
        schema=schema,
        example=example,
        allowed_evidence_refs=allowed_evidence_refs,
        allowed_memory_refs=allowed_memory_refs,
        retry=False,
    )


def _json_contract_prompt(
    original_prompt: str,
    *,
    schema: type[BaseModel],
    example: dict[str, Any],
    allowed_evidence_refs: tuple[str, ...],
    allowed_memory_refs: tuple[str, ...],
    retry: bool,
) -> str:
    introduction = (
        "The previous response did not satisfy the required output contract."
        if retry
        else "Produce the required structured result using JSON Output."
    )
    return f"""{introduction}
Return exactly one JSON object and no Markdown, prose, or code fence.
The object must satisfy the JSON Schema and all semantic requirements in the
original task. Use only refs from the allowlists below.

JSON SCHEMA:
{json.dumps(schema.model_json_schema(), ensure_ascii=False, sort_keys=True)}

VALID EXAMPLE:
{json.dumps(example, ensure_ascii=False, sort_keys=True)}

ALLOWED EVIDENCE REFS:
{json.dumps(allowed_evidence_refs, ensure_ascii=False)}

ALLOWED MEMORY REFS:
{json.dumps(allowed_memory_refs, ensure_ascii=False)}

ORIGINAL TASK:
{original_prompt}
"""
