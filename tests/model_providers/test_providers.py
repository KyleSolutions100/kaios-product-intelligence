from copy import deepcopy

import pytest

from kaios.analyzer import synthesize
from kaios.model_providers import (
    FakeModelProvider,
    LiteLLMModelProvider,
    ModelProvider,
    ModelProviderConfigurationError,
    ModelProviderResponseError,
    ModelRequest,
    RulesModelProvider,
    create_model_provider,
)


def product_request() -> ModelRequest:
    return ModelRequest(
        task="product_intelligence.synthesize",
        system_prompt="Return structured opportunities",
        input_data={
            "seed": "eco wedding invitation",
            "evidence": [
                {
                    "title": "Eco Wedding Invitation",
                    "url": "https://example.invalid/listing",
                    "content": "Popular recycled invitation with many reviews " * 5,
                    "metadata": {"price_range": "£12-£24"},
                }
            ],
        },
    )


def valid_opportunity(title: str = "Predictable Product") -> list[dict]:
    return [
        {
            "title": title,
            "evidence_urls": ["https://example.invalid/evidence"],
            "price_range": "£10-£20",
            "competitor_count_estimate": "20",
            "demand_signal": "High",
            "profitability_hint": "Promising",
            "confidence": "High",
            "recommended": True,
            "why_recommended": "Test evidence",
        }
    ]


def test_model_provider_interface_is_abstract():
    with pytest.raises(TypeError):
        ModelProvider()


def test_rules_provider_is_offline_free_and_deterministic(monkeypatch):
    monkeypatch.delenv("KAIOS_LLM_API_KEY", raising=False)
    provider = RulesModelProvider()
    request = product_request()

    first = provider.generate(request)
    second = provider.generate(deepcopy(request))

    assert provider.provider_id == "rules"
    assert provider.requires_network is False
    assert provider.requires_paid_access is False
    assert first == second
    assert first.provider_id == "rules"
    assert isinstance(first.data, list)
    assert first.data[0]["title"] == "Eco Wedding Invitation"
    assert first.data[0]["recommended"] is True


def test_rules_provider_and_analyzer_never_load_litellm(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("LiteLLM must not load during offline analysis")

    monkeypatch.setattr(
        "kaios.model_providers.litellm._load_litellm_completion", fail_if_loaded
    )

    opportunities = synthesize(
        product_request().input_data["evidence"],
        "eco wedding invitation",
        provider=RulesModelProvider(),
    )

    assert len(opportunities) == 1
    assert opportunities[0].title == "Eco Wedding Invitation"


def test_analyzer_defaults_to_rules_without_api_configuration(monkeypatch):
    monkeypatch.delenv("KAIOS_LLM_API_KEY", raising=False)

    def fail_if_loaded():
        raise AssertionError("default offline analysis must not load LiteLLM")

    monkeypatch.setattr(
        "kaios.model_providers.litellm._load_litellm_completion", fail_if_loaded
    )

    opportunities = synthesize(
        product_request().input_data["evidence"], "eco wedding invitation"
    )

    assert len(opportunities) == 1
    assert opportunities[0].title == "Eco Wedding Invitation"


def test_fake_provider_returns_outputs_in_order_and_records_deep_copied_calls():
    first_output = {"sequence": 1}
    second_output = {"sequence": 2}
    provider = FakeModelProvider(outputs=[first_output, second_output])
    first_request = ModelRequest(task="first", input_data={"nested": {"value": 1}})
    second_request = ModelRequest(task="second", input_data={"value": 2})

    first = provider.generate(first_request)
    second = provider.generate(second_request)
    repeated = provider.generate(second_request)
    first_request.input_data["nested"]["value"] = 99

    assert first.data == first_output
    assert second.data == second_output
    assert repeated.data == second_output
    assert [call.task for call in provider.calls] == ["first", "second", "second"]
    assert provider.calls[0].input_data == {"nested": {"value": 1}}
    assert provider.requires_network is False
    assert provider.requires_paid_access is False


def test_fake_provider_failure_is_recorded_and_propagates():
    failure = RuntimeError("simulated provider failure")
    provider = FakeModelProvider(failure=failure)
    request = ModelRequest(task="failure", input_data={})

    with pytest.raises(RuntimeError, match="simulated provider failure"):
        provider.generate(request)

    assert provider.calls == [request]


def test_analyzer_uses_injected_fake_provider_and_propagates_failure():
    provider = FakeModelProvider(output=valid_opportunity())
    opportunities = synthesize(
        [{"title": "Evidence", "url": "https://example.invalid"}],
        "seed",
        provider=provider,
    )

    assert [opportunity.title for opportunity in opportunities] == [
        "Predictable Product"
    ]
    assert provider.calls[0].task == "product_intelligence.synthesize"
    assert provider.calls[0].input_data["seed"] == "seed"

    failing = FakeModelProvider(failure=RuntimeError("analysis failed"))
    with pytest.raises(RuntimeError, match="analysis failed"):
        synthesize([{"title": "Evidence"}], "seed", provider=failing)


def test_litellm_provider_requires_explicit_configuration():
    with pytest.raises(ModelProviderConfigurationError, match="model"):
        LiteLLMModelProvider(model=None, api_key="key")
    with pytest.raises(ModelProviderConfigurationError, match="API_KEY"):
        LiteLLMModelProvider(model="example-model", api_key=None)


def test_litellm_adapter_preserves_external_call_shape_without_network():
    captured = {}

    class Message:
        content = f"```json\n{valid_opportunity()}\n```".replace("'", '"').replace(
            "True", "true"
        )

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    def completion_stub(**kwargs):
        captured.update(kwargs)
        return Response()

    provider = LiteLLMModelProvider(
        model="external-model",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        completion_fn=completion_stub,
    )
    output = provider.generate(product_request())

    assert output.data == valid_opportunity()
    assert provider.provider_id == "litellm"
    assert provider.requires_network is True
    assert provider.requires_paid_access is True
    assert captured["model"] == "external-model"
    assert captured["api_key"] == "test-key"
    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 2000
    assert captured["messages"][0]["role"] == "system"


def test_litellm_adapter_rejects_invalid_structured_output():
    class Message:
        content = "not-json"

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    provider = LiteLLMModelProvider(
        model="external-model",
        api_key="test-key",
        completion_fn=lambda **kwargs: Response(),
    )

    with pytest.raises(ModelProviderResponseError, match="invalid structured"):
        provider.generate(product_request())


def test_legacy_analyzer_model_argument_uses_litellm_adapter(monkeypatch):
    captured = {}

    class Message:
        content = "[]"

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    def completion_stub(**kwargs):
        captured.update(kwargs)
        return Response()

    monkeypatch.setenv("KAIOS_LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "kaios.model_providers.litellm._load_litellm_completion",
        lambda: completion_stub,
    )

    assert synthesize([{"title": "Evidence"}], "seed", "legacy-model") == []
    assert captured["model"] == "legacy-model"


def test_provider_factory_supports_all_configured_names(monkeypatch):
    monkeypatch.setenv("KAIOS_LLM_API_KEY", "test-key")

    assert isinstance(create_model_provider("rules"), RulesModelProvider)
    assert isinstance(create_model_provider("fake"), FakeModelProvider)
    assert isinstance(
        create_model_provider("litellm", model="external-model"),
        LiteLLMModelProvider,
    )
    assert isinstance(
        create_model_provider("openai", model="external-model"),
        LiteLLMModelProvider,
    )
    with pytest.raises(ModelProviderConfigurationError, match="unknown"):
        create_model_provider("unsupported")
