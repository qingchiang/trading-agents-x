"""Shared model invocation and readable-output helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from tradingagents.domain.common import (
    ArtifactGenerationMethod,
    ReportLanguage,
)
from tradingagents.domain.evidence import (
    EvidenceBundle,
)
from tradingagents.research.synthesis.drafts import EventWriter
from tradingagents.research.synthesis.structured_output import (
    StructuredOutputRunner,
)


def _configured_generation_method(llm: Any) -> ArtifactGenerationMethod:
    if getattr(llm, "preferred_structured_output_method", None) == "json_mode":
        return ArtifactGenerationMethod.JSON_MODE
    return ArtifactGenerationMethod.TOOL_CALL


def _runner(
    llm: Any,
    schema: Any,
    validator: Callable[[Any], Any],
    node: str,
    event_writer: EventWriter | None,
    repair_instructions: str | None = None,
    *,
    candidate_only_repair: bool = False,
) -> StructuredOutputRunner[Any]:
    return StructuredOutputRunner(
        llm=llm,
        schema=schema,
        validator=validator,
        node=node,
        event_writer=event_writer,
        invoke_config={"metadata": {"research_node": node}},
        repair_mode="preferred",
        include_candidate_in_repair=True,
        candidate_only_repair=candidate_only_repair,
        repair_instructions=repair_instructions
        or (
            "Repair only invalid shallow routing metadata such as issue IDs, "
            "confidence, or dispositions. The readable Markdown is already "
            "complete and must not be regenerated."
        ),
    )


def _agenda_example_text(output_language: str) -> dict[str, str]:
    if output_language == ReportLanguage.SIMPLIFIED_CHINESE.prompt_label:
        return {
            "summary": "多空案例对一个重要经营机制存在分歧。",
            "question": "有争议的经营机制能否持续？",
            "fallback_summary": "已完成的多空案例存在重要分歧，但议程导航审计不完整。",
            "fallback_question": "多空案例之间仍未解决的核心分歧是什么？",
        }
    if output_language == ReportLanguage.JAPANESE.prompt_label:
        return {
            "summary": "強気・弱気ケースは重要な事業メカニズムについて対立している。",
            "question": "争点となる事業メカニズムは持続するか。",
            "fallback_summary": "強気・弱気ケースには重要な対立があるが、議題監査は不完全である。",
            "fallback_question": "両ケース間で未解決の重要な対立は何か。",
        }
    return {
        "summary": "The cases disagree on one material operating mechanism.",
        "question": "Will the disputed operating mechanism persist?",
        "fallback_summary": (
            "The completed bull and bear cases contain a material disagreement "
            "whose agenda audit is incomplete."
        ),
        "fallback_question": (
            "Which material disagreement between the completed cases remains unresolved?"
        ),
    }


def _is_standard_output_language(output_language: str) -> bool:
    return output_language in {item.prompt_label for item in ReportLanguage}


def _evidence_refs(state: Mapping[str, Any]) -> tuple[str, ...]:
    bundle = EvidenceBundle.model_validate(state["evidence_bundle"])
    return tuple(item.ref for item in bundle.items)


def _mentioned_ids(markdown: str, valid_ids: set[str]) -> tuple[str, ...]:
    """Return exact valid IDs in first-appearance order."""

    matches: list[tuple[int, str]] = []
    for candidate in valid_ids:
        match = re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(candidate)}"
            r"(?![A-Za-z0-9_-])",
            markdown,
        )
        if match is not None:
            matches.append((match.start(), candidate))
    return tuple(candidate for _, candidate in sorted(matches))


def _message_text(response: Any) -> str:
    content = getattr(response, "content", response)
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
    return "\n".join(parts)


def _is_truncated(response: Any) -> bool:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, dict) and isinstance(response, dict):
        metadata = response.get("response_metadata")
    if not isinstance(metadata, dict):
        return False
    return metadata.get("finish_reason") in {
        "length",
        "max_tokens",
        "max_output_tokens",
    }
