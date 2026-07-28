"""Minimal model ID validation.

Provider model catalogs are discovered at runtime and may change without a
package release. Validation therefore rejects only blank IDs; provider APIs
remain the authority for whether a non-empty model is accessible.
"""


def validate_model(provider: str, model: str) -> bool:
    """Accept every non-empty ID so newly released models work immediately."""
    return bool(provider.strip() and model.strip())
