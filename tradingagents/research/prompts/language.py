"""Output-language instructions for research prompts."""

def get_language_instruction(language: str, response_scope: str = "your entire response") -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to analyst reports so a non-English run produces localized
    user-facing research.
    """
    if language.strip().lower() == "english":
        return ""
    return f" Write {response_scope} in {language}."
