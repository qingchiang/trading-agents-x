import unittest
import warnings

import pytest

from tradingagents.llm_clients.base_client import BaseLLMClient
from tradingagents.llm_clients.validators import validate_model


class DummyLLMClient(BaseLLMClient):
    def __init__(self, provider: str, model: str):
        self.provider = provider
        super().__init__(model)

    def get_llm(self):
        self.warn_if_unknown_model()
        return object()

    def validate_model(self) -> bool:
        return validate_model(self.provider, self.model)


@pytest.mark.unit
class ModelValidationTests(unittest.TestCase):
    def test_new_provider_models_are_accepted_without_a_package_release(self):
        client = DummyLLMClient("openai", "not-a-real-openai-model")

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client.get_llm()

        self.assertEqual(caught, [])

    def test_all_providers_accept_custom_models_without_warning(self):
        for provider in ("openai", "anthropic", "google", "openrouter", "ollama"):
            client = DummyLLMClient(provider, "custom-model-name")

            with self.subTest(provider=provider):
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    client.get_llm()

                self.assertEqual(caught, [])

    def test_blank_provider_or_model_is_rejected(self):
        self.assertFalse(validate_model("", "model"))
        self.assertFalse(validate_model("openai", " "))
