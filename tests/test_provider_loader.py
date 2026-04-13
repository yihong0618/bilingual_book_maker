import json
import os
import pytest

from book_maker.provider_loader import (
    _merge_configs,
    get_provider,
    get_translator_class,
    load_provider_config,
    validate_provider,
    SUPPORTED_API_STYLES,
)
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.qwen_translator import QwenTranslator

VALID_DEEPSEEK = {
    "api_style": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "default_models": ["deepseek-chat"],
    "env_key": "BBM_DEEPSEEK_API_KEY",
}

VALID_MINIMAL = {
    "api_style": "openai",
}

VALID_CLAUDE_STYLE = {
    "api_style": "claude",
    "base_url": "https://api.anthropic.com",
    "default_models": ["claude-sonnet-4-20250514"],
}


@pytest.fixture
def tmp_global_config(tmp_path, monkeypatch):
    import book_maker.provider_loader as mod

    config_dir = tmp_path / ".bbm"
    config_dir.mkdir()
    config_file = config_dir / "providers.json"
    monkeypatch.setattr(mod, "GLOBAL_CONFIG_PATH", config_file)
    return config_file


@pytest.fixture
def tmp_local_config(tmp_path, monkeypatch):
    import book_maker.provider_loader as mod

    config_file = tmp_path / "bbm_providers.json"
    monkeypatch.setattr(mod, "LOCAL_CONFIG_FILENAME", "bbm_providers.json")
    monkeypatch.chdir(tmp_path)
    return config_file


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestValidateProvider:
    def test_valid_full_config(self):
        validate_provider("deepseek", VALID_DEEPSEEK)

    def test_valid_minimal(self):
        validate_provider("myprovider", VALID_MINIMAL)

    def test_valid_claude_style(self):
        validate_provider("my_claude", VALID_CLAUDE_STYLE)

    def test_missing_api_style(self):
        with pytest.raises(ValueError, match="missing required fields"):
            validate_provider("bad", {"base_url": "https://example.com"})

    def test_unsupported_api_style(self):
        with pytest.raises(ValueError, match="unsupported api_style"):
            validate_provider("bad", {"api_style": "nonexistent"})

    def test_unknown_field(self):
        with pytest.raises(ValueError, match="unknown fields"):
            validate_provider("bad", {"api_style": "openai", "typo_field": "x"})

    def test_default_models_not_list(self):
        with pytest.raises(ValueError, match="must be a list of strings"):
            validate_provider("bad", {"api_style": "openai", "default_models": "gpt-4"})

    def test_default_models_empty(self):
        with pytest.raises(ValueError, match="must not be empty"):
            validate_provider("bad", {"api_style": "openai", "default_models": []})

    def test_default_models_non_string_element(self):
        with pytest.raises(ValueError, match="must be a list of strings"):
            validate_provider("bad", {"api_style": "openai", "default_models": [123]})

    def test_non_dict_provider(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            validate_provider("bad", "not a dict")


class TestMergeConfigs:
    def test_both_none(self):
        result = _merge_configs(None, None)
        assert result == {"providers": {}}

    def test_global_only(self):
        global_cfg = {"providers": {"deepseek": VALID_DEEPSEEK}}
        result = _merge_configs(global_cfg, None)
        assert "deepseek" in result["providers"]

    def test_local_only(self):
        local_cfg = {"providers": {"deepseek": VALID_DEEPSEEK}}
        result = _merge_configs(None, local_cfg)
        assert "deepseek" in result["providers"]

    def test_local_overrides_global(self):
        global_cfg = {
            "providers": {
                "deepseek": {"api_style": "openai", "base_url": "https://old.com"}
            }
        }
        local_cfg = {"providers": {"deepseek": VALID_DEEPSEEK}}
        result = _merge_configs(global_cfg, local_cfg)
        assert (
            result["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
        )

    def test_merged_has_both(self):
        global_cfg = {"providers": {"provider_a": VALID_DEEPSEEK}}
        local_cfg = {"providers": {"provider_b": VALID_MINIMAL}}
        result = _merge_configs(global_cfg, local_cfg)
        assert "provider_a" in result["providers"]
        assert "provider_b" in result["providers"]


class TestLoadProviderConfig:
    def test_no_config_files(self, tmp_global_config, tmp_local_config):
        config = load_provider_config()
        assert config == {"providers": {}}

    def test_load_global_only(self, tmp_global_config, tmp_local_config):
        _write_json(tmp_global_config, {"providers": {"deepseek": VALID_DEEPSEEK}})
        config = load_provider_config()
        assert "deepseek" in config["providers"]

    def test_load_local_only(self, tmp_global_config, tmp_local_config):
        _write_json(tmp_local_config, {"providers": {"siliconflow": VALID_MINIMAL}})
        config = load_provider_config()
        assert "siliconflow" in config["providers"]

    def test_local_overrides_global(self, tmp_global_config, tmp_local_config):
        _write_json(
            tmp_global_config,
            {
                "providers": {
                    "deepseek": {"api_style": "openai", "base_url": "https://old.com"}
                }
            },
        )
        _write_json(tmp_local_config, {"providers": {"deepseek": VALID_DEEPSEEK}})
        config = load_provider_config()
        assert (
            config["providers"]["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
        )


class TestGetProvider:
    def test_existing_provider(self, tmp_global_config, tmp_local_config):
        _write_json(tmp_local_config, {"providers": {"deepseek": VALID_DEEPSEEK}})
        result = get_provider("deepseek")
        assert result == VALID_DEEPSEEK

    def test_nonexistent_provider(self, tmp_global_config, tmp_local_config):
        _write_json(tmp_local_config, {"providers": {"deepseek": VALID_DEEPSEEK}})
        with pytest.raises(ValueError, match="not found in config"):
            get_provider("nonexistent")

    def test_empty_config(self, tmp_global_config, tmp_local_config):
        with pytest.raises(ValueError, match="not found in config"):
            get_provider("anything")


class TestGetTranslatorClass:
    def test_openai_style(self):
        assert get_translator_class("openai") is ChatGPTAPI

    def test_claude_style(self):
        assert get_translator_class("claude") is Claude

    def test_gemini_style(self):
        assert get_translator_class("gemini") is Gemini

    def test_qwen_style(self):
        assert get_translator_class("qwen") is QwenTranslator

    def test_unsupported_style(self):
        with pytest.raises(ValueError, match="Unsupported api_style"):
            get_translator_class("nonexistent")

    def test_all_styles_mapped(self):
        for style in SUPPORTED_API_STYLES:
            cls = get_translator_class(style)
            assert cls is not None
