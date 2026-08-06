from kaios.config import load_config


MODEL_ENVIRONMENT_KEYS = (
    "KAIOS_MODEL_PROVIDER",
    "KAIOS_LLM_PROVIDER",
    "KAIOS_PRODUCT_INTELLIGENCE_MODEL_PROVIDER",
    "KAIOS_LLM_MODEL",
)


def clear_model_environment(monkeypatch):
    monkeypatch.setattr("kaios.config.load_dotenv", lambda: None)
    for key in MODEL_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_default_config_uses_offline_rules(monkeypatch):
    clear_model_environment(monkeypatch)

    config = load_config()

    assert config.model_provider == "rules"
    assert config.provider_for_agent("product_intelligence") == "rules"


def test_config_supports_agent_specific_provider_selection(tmp_path, monkeypatch):
    clear_model_environment(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model_provider: rules
agent_model_providers:
  product_intelligence: fake
  finance: litellm
model: configured-model
""".strip()
    )

    config = load_config(str(config_path))

    assert config.provider_for_agent("product_intelligence") == "fake"
    assert config.provider_for_agent("finance") == "litellm"
    assert config.provider_for_agent("marketing") == "rules"
    assert config.model == "configured-model"


def test_environment_supports_new_and_legacy_provider_names(monkeypatch):
    clear_model_environment(monkeypatch)
    monkeypatch.setenv("KAIOS_LLM_PROVIDER", "openai")

    legacy = load_config()

    assert legacy.model_provider == "litellm"

    monkeypatch.setenv("KAIOS_MODEL_PROVIDER", "fake")
    monkeypatch.setenv("KAIOS_PRODUCT_INTELLIGENCE_MODEL_PROVIDER", "rules")
    configured = load_config()

    assert configured.model_provider == "fake"
    assert configured.provider_for_agent("product_intelligence") == "rules"
