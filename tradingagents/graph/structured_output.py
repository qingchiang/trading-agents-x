"""Validated structured-output execution with one bounded recovery attempt."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from tradingagents.application.contracts import ArtifactGenerationMethod
from tradingagents.graph.output_validation import OutputValidationError

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
SemanticValidator = Callable[[StructuredModel], StructuredModel]
EventWriter = Callable[[dict[str, Any]], None]

_JSON_FENCE_RE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n?(?P<body>.*?)[ \t]*\r?\n?```\Z",
    re.IGNORECASE | re.DOTALL,
)


class StructuredOutputError(RuntimeError):
    """A provider response could not satisfy the requested typed contract."""

    def __init__(
        self,
        *,
        node: str,
        schema: str,
        reason_code: str,
        validation_issues: tuple[str, ...] = (),
    ):
        self.node = node
        self.schema = schema
        self.reason_code = reason_code
        self.validation_issues = validation_issues
        issue_suffix = (
            f"; issues={','.join(validation_issues)}"
            if validation_issues
            else ""
        )
        super().__init__(
            f"Validated {schema} output failed for {node} "
            f"({reason_code}{issue_suffix})"
        )


@dataclass(frozen=True)
class StructuredOutputResult(Generic[StructuredModel]):
    value: StructuredModel
    generation_method: ArtifactGenerationMethod


class _InvalidOutput(ValueError):
    def __init__(
        self,
        reason_code: str,
        validation_issues: tuple[str, ...] = (),
    ):
        self.reason_code = reason_code
        self.validation_issues = validation_issues
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
        invoke_config: dict[str, Any] | None = None,
        repair_mode: Literal["json_mode", "preferred"] = "json_mode",
        include_candidate_in_repair: bool = False,
        candidate_only_repair: bool = False,
        repair_instructions: str | None = None,
        truncation_recovery: (
            Callable[[], StructuredOutputResult[StructuredModel]] | None
        ) = None,
    ):
        self.llm = llm
        self.schema = schema
        self.validator = validator
        self.node = node
        self.event_writer = event_writer
        self.invoke_config = invoke_config
        self.repair_mode = repair_mode
        self.include_candidate_in_repair = include_candidate_in_repair
        self.candidate_only_repair = candidate_only_repair
        self.repair_instructions = repair_instructions
        self.truncation_recovery = truncation_recovery

    def invoke(
        self,
        prompt: str,
        *,
        example: dict[str, Any],
        allowed_evidence_refs: tuple[str, ...],
        allowed_memory_refs: tuple[str, ...] = (),
    ) -> StructuredOutputResult[StructuredModel]:
        primary_reason = "structured_binding_error"
        primary_validation_issues: tuple[str, ...] = ()
        primary_candidate: dict[str, Any] | None = None
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
                response = self._invoke(primary, primary_prompt)
            except Exception:
                primary_reason = "provider_error"
            else:
                parsed, raw, parsing_error = _unpack_response(
                    response,
                    self.schema,
                )
                if _is_truncated(raw):
                    primary_reason = "output_truncated"
                elif parsed is not None:
                    primary_candidate = _safe_candidate(parsed)
                    try:
                        value = self._validate(parsed)
                    except _InvalidOutput as exc:
                        primary_reason = exc.reason_code
                        primary_validation_issues = exc.validation_issues
                    else:
                        return StructuredOutputResult(
                            value=value,
                            generation_method=primary_generation_method,
                        )
                else:
                    try:
                        primary_candidate = _strict_json_object(raw)
                        value = self._validate(primary_candidate)
                    except _InvalidOutput as exc:
                        parser_issues = _validation_issues(parsing_error)
                        primary_reason = (
                            "schema_validation"
                            if parser_issues
                            else exc.reason_code
                        )
                        primary_validation_issues = (
                            parser_issues or exc.validation_issues
                        )
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

        if (
            primary_reason == "output_truncated"
            and self.truncation_recovery is not None
        ):
            self._emit(
                "node.output_retry",
                method=ArtifactGenerationMethod.SECTIONED_RECOVERY,
                reason_code=primary_reason,
                validation_issues=primary_validation_issues,
            )
            failure_reason = "sectioned_recovery_failed"
            failure_validation_issues: tuple[str, ...] = ()
            try:
                sectioned = self.truncation_recovery()
                value = self._validate(sectioned.value)
            except StructuredOutputError as exc:
                failure_reason = exc.reason_code
                failure_validation_issues = exc.validation_issues
            except _InvalidOutput as exc:
                failure_reason = exc.reason_code
                failure_validation_issues = exc.validation_issues
            except Exception:
                pass
            else:
                self._emit(
                    "node.output_recovered",
                    method=ArtifactGenerationMethod.SECTIONED_RECOVERY,
                    reason_code=primary_reason,
                    validation_issues=primary_validation_issues,
                )
                return StructuredOutputResult(
                    value=value,
                    generation_method=(
                        ArtifactGenerationMethod.SECTIONED_RECOVERY
                    ),
                )
            self._emit(
                "node.output_failed",
                method=ArtifactGenerationMethod.SECTIONED_RECOVERY,
                reason_code=failure_reason,
                validation_issues=failure_validation_issues,
            )
            raise StructuredOutputError(
                node=self.node,
                schema=self.schema.__name__,
                reason_code=failure_reason,
                validation_issues=failure_validation_issues,
            )

        recovery_method = (
            ArtifactGenerationMethod.TOOL_CALL_RECOVERED
            if (
                self.repair_mode == "preferred"
                and primary_generation_method
                is ArtifactGenerationMethod.TOOL_CALL
            )
            else ArtifactGenerationMethod.JSON_MODE_RECOVERED
        )
        self._emit(
            "node.output_retry",
            method=recovery_method,
            reason_code=primary_reason,
            validation_issues=primary_validation_issues,
        )
        recovery_prompt = _recovery_prompt(
            prompt,
            schema=self.schema,
            example=example,
            allowed_evidence_refs=allowed_evidence_refs,
            allowed_memory_refs=allowed_memory_refs,
            validation_issues=primary_validation_issues,
            candidate=(
                primary_candidate
                if self.include_candidate_in_repair
                else None
            ),
            repair_instructions=self.repair_instructions,
            candidate_only=self.candidate_only_repair,
        )
        try:
            if self.repair_mode == "preferred":
                recovery = self.llm.with_structured_output(
                    self.schema,
                    include_raw=True,
                    **bind_kwargs,
                )
            else:
                recovery = self.llm.with_structured_output(
                    self.schema,
                    method="json_mode",
                    include_raw=True,
                    **bind_kwargs,
                )
        except Exception:
            recovery = None

        failure_reason = "structured_binding_error"
        failure_validation_issues: tuple[str, ...] = ()
        response_available = False
        response = None
        if recovery is not None or self.repair_mode != "preferred":
            try:
                response = (
                    self._invoke(recovery, recovery_prompt)
                    if recovery is not None
                    else self._invoke(
                        self.llm,
                        recovery_prompt,
                        **bind_kwargs,
                    )
                )
            except Exception:
                failure_reason = "provider_error"
            else:
                response_available = True
        if response_available:
            parsed, raw, parsing_error = _unpack_response(
                response,
                self.schema,
            )
            if _is_truncated(raw):
                failure_reason = "output_truncated"
                candidate = None
            else:
                candidate = parsed
            if candidate is None and failure_reason != "output_truncated":
                try:
                    candidate = _strict_json_object(raw)
                except _InvalidOutput as exc:
                    parser_issues = _validation_issues(parsing_error)
                    failure_reason = (
                        "schema_validation"
                        if parser_issues
                        else exc.reason_code
                    )
                    failure_validation_issues = (
                        parser_issues or exc.validation_issues
                    )
                    candidate = None
            if candidate is not None:
                try:
                    value = self._validate(candidate)
                except _InvalidOutput as exc:
                    failure_reason = exc.reason_code
                    failure_validation_issues = exc.validation_issues
                else:
                    self._emit(
                        "node.output_recovered",
                        method=recovery_method,
                        reason_code=primary_reason,
                        validation_issues=primary_validation_issues,
                    )
                    return StructuredOutputResult(
                        value=value,
                        generation_method=recovery_method,
                    )

        if (
            failure_reason == "output_truncated"
            and self.truncation_recovery is not None
        ):
            self._emit(
                "node.output_retry",
                method=ArtifactGenerationMethod.SECTIONED_RECOVERY,
                reason_code=failure_reason,
                validation_issues=failure_validation_issues,
            )
            sectioned_failure = "sectioned_recovery_failed"
            sectioned_validation_issues: tuple[str, ...] = ()
            try:
                sectioned = self.truncation_recovery()
                value = self._validate(sectioned.value)
            except StructuredOutputError as exc:
                sectioned_failure = exc.reason_code
                sectioned_validation_issues = exc.validation_issues
            except _InvalidOutput as exc:
                sectioned_failure = exc.reason_code
                sectioned_validation_issues = exc.validation_issues
            except Exception:
                pass
            else:
                self._emit(
                    "node.output_recovered",
                    method=ArtifactGenerationMethod.SECTIONED_RECOVERY,
                    reason_code=failure_reason,
                    validation_issues=failure_validation_issues,
                )
                return StructuredOutputResult(
                    value=value,
                    generation_method=(
                        ArtifactGenerationMethod.SECTIONED_RECOVERY
                    ),
                )
            failure_reason = sectioned_failure
            failure_validation_issues = sectioned_validation_issues

        self._emit(
            "node.output_failed",
            method=recovery_method,
            reason_code=failure_reason,
            validation_issues=failure_validation_issues,
        )
        raise StructuredOutputError(
            node=self.node,
            schema=self.schema.__name__,
            reason_code=failure_reason,
            validation_issues=failure_validation_issues,
        )

    def _validate(self, candidate: Any) -> StructuredModel:
        try:
            value = self.schema.model_validate(candidate)
        except ValidationError as exc:
            raise _InvalidOutput(
                "schema_validation",
                _validation_issues(exc),
            ) from exc
        except Exception as exc:
            raise _InvalidOutput("schema_validation") from exc
        try:
            return self.validator(value)
        except OutputValidationError as exc:
            raise _InvalidOutput(
                "semantic_validation",
                (f"semantic.{exc.issue_code}",),
            ) from exc
        except Exception as exc:
            raise _InvalidOutput("semantic_validation") from exc

    def _invoke(
        self,
        target: Any,
        prompt: str,
        **kwargs: Any,
    ) -> Any:
        if self.invoke_config is None:
            return target.invoke(prompt, **kwargs)
        return target.invoke(
            prompt,
            config=self.invoke_config,
            **kwargs,
        )

    def _emit(
        self,
        event_type: str,
        *,
        method: ArtifactGenerationMethod,
        reason_code: str,
        validation_issues: tuple[str, ...] = (),
    ) -> None:
        if self.event_writer is None:
            return
        payload = {
            "schema": self.schema.__name__,
            "method": method.value,
            "reason_code": reason_code,
        }
        if validation_issues:
            payload["validation_issues"] = list(validation_issues)
        self.event_writer(
            {
                "event_type": event_type,
                "node": self.node,
                "payload": payload,
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
) -> tuple[Any | None, Any, Any]:
    if isinstance(response, schema):
        return response, None, None
    if isinstance(response, dict) and (
        "parsed" in response or "raw" in response or "parsing_error" in response
    ):
        return (
            response.get("parsed"),
            response.get("raw"),
            response.get("parsing_error"),
        )
    if isinstance(response, dict):
        return response, None, None
    return None, response, None


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
    validation_issues: tuple[str, ...],
    candidate: dict[str, Any] | None,
    repair_instructions: str | None,
    candidate_only: bool,
) -> str:
    return _json_contract_prompt(
        original_prompt,
        schema=schema,
        example=example,
        allowed_evidence_refs=allowed_evidence_refs,
        allowed_memory_refs=allowed_memory_refs,
        validation_issues=validation_issues,
        candidate=candidate,
        repair_instructions=repair_instructions,
        retry=True,
        include_original_prompt=not (candidate_only and candidate is not None),
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
        validation_issues=(),
        candidate=None,
        repair_instructions=None,
        retry=False,
        include_original_prompt=True,
    )


def _json_contract_prompt(
    original_prompt: str,
    *,
    schema: type[BaseModel],
    example: dict[str, Any],
    allowed_evidence_refs: tuple[str, ...],
    allowed_memory_refs: tuple[str, ...],
    validation_issues: tuple[str, ...],
    candidate: dict[str, Any] | None,
    repair_instructions: str | None,
    retry: bool,
    include_original_prompt: bool,
) -> str:
    introduction = (
        "The previous response did not satisfy the required output contract."
        if retry
        else "Produce the required structured result using JSON Output."
    )
    validation_section = (
        "\nPREVIOUS VALIDATION ISSUES:\n"
        + "\n".join(f"- {issue}" for issue in validation_issues)
        + "\nCorrect every listed issue in the replacement object.\n"
        if validation_issues
        else ""
    )
    candidate_section = (
        "\nINVALID CANDIDATE JSON:\n"
        + json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        + "\nPreserve every valid field and change only what is required to "
        "correct the listed issues.\n"
        if candidate is not None
        else ""
    )
    repair_section = (
        "\nAPPLICATION REPAIR RULES:\n"
        + repair_instructions.strip()
        + "\n"
        if repair_instructions
        else ""
    )
    original_task = (
        f"\nORIGINAL TASK:\n{original_prompt}\n"
        if include_original_prompt
        else ""
    )
    return f"""{introduction}
Return exactly one JSON object and no Markdown, prose, or code fence.
The object must satisfy the JSON Schema and all semantic requirements in the
original task. Use only refs from the allowlists below.
{validation_section}
{candidate_section}
{repair_section}

JSON SCHEMA:
{json.dumps(schema.model_json_schema(), ensure_ascii=False, sort_keys=True)}

VALID EXAMPLE:
{json.dumps(example, ensure_ascii=False, sort_keys=True)}

ALLOWED EVIDENCE REFS:
{json.dumps(allowed_evidence_refs, ensure_ascii=False)}

ALLOWED MEMORY REFS:
{json.dumps(allowed_memory_refs, ensure_ascii=False)}
{original_task}
"""


def _validation_issues(error: Any) -> tuple[str, ...]:
    """Return bounded field/type diagnostics without model values or messages."""

    if isinstance(error, OutputValidationError):
        return (f"semantic.{error.issue_code}",)
    if not isinstance(error, ValidationError):
        return ()
    issues = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        location = ".".join(
            _safe_issue_token(part) for part in item.get("loc", ())
        )
        error_type = _safe_issue_token(item.get("type", "invalid"))
        issues.append(
            f"schema.{location or 'root'}.{error_type}"
        )
        if len(issues) == 8:
            break
    return tuple(dict.fromkeys(issues))


def _safe_candidate(value: Any) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if not isinstance(value, dict):
        return None
    try:
        serialized = json.dumps(value, ensure_ascii=False)
        restored = json.loads(serialized)
    except (TypeError, ValueError):
        return None
    return restored if isinstance(restored, dict) else None


def _safe_issue_token(value: Any) -> str:
    if isinstance(value, int):
        return str(value)
    token = str(value)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", token):
        return token
    return "field"
