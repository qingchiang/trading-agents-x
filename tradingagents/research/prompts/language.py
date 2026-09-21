"""Output-language instructions for research prompts."""

from tradingagents.domain.common import ReportLanguage, report_language_prompt_label


def get_language_instruction(language: str | ReportLanguage, response_scope: str = "your entire response") -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to analyst reports so a non-English run produces localized
    user-facing research.
    """
    language = report_language_prompt_label(language)
    if language.strip().lower() == "english":
        return ""
    return f" Write {response_scope} in {language}."
