from tradingagents.application.markdown_evidence import normalize_evidence_markdown


def test_normalizes_inline_refs_and_removes_model_authored_definitions() -> None:
    valid = "ev_0123456789ab"
    unknown = "ev_ffffffffffff"
    result = normalize_evidence_markdown(
        (
            f"Finding.[^{valid}] Unknown.[^{unknown}]\n\n"
            f"[^{valid}]: Model-authored source summary.\n"
            "    Continued source summary.\n\n"
            "## Risks\n\nReadable risk.\n"
        ),
        allowed_refs={valid},
        source="fixture",
    )

    assert result.markdown == f"Finding.[^{valid}] Unknown.\n\n## Risks\n\nReadable risk."
    assert result.evidence_refs == (valid,)
    assert [warning.code for warning in result.warnings] == [
        "research.unknown_evidence_ref"
    ]


def test_leaves_fenced_and_inline_code_references_untouched() -> None:
    valid = "ev_0123456789ab"
    result = normalize_evidence_markdown(
        (
            f"Text.[^{valid}] and `literal [^ev_deadbeefdead]`.\n\n"
            "```markdown\n"
            "[^ev_deadbeefdead]: example definition\n"
            "```\n"
        ),
        allowed_refs={valid},
        source="fixture",
    )

    assert result.evidence_refs == (valid,)
    assert "`literal [^ev_deadbeefdead]`" in result.markdown
    assert "[^ev_deadbeefdead]: example definition" in result.markdown
    assert result.warnings == ()
