import json
import os
from pathlib import Path

from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.qwen_translator import QwenTranslator

SUPPORTED_API_STYLES = {
    "openai": ChatGPTAPI,
    "claude": Claude,
    "gemini": Gemini,
    "qwen": QwenTranslator,
}

GLOBAL_CONFIG_PATH = Path.home() / ".bbm" / "providers.json"
LOCAL_CONFIG_FILENAME = "bbm_providers.json"

REQUIRED_FIELDS = {"api_style"}
OPTIONAL_FIELDS = {"base_url", "default_models", "env_key"}
ALL_VALID_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


def _load_json_file(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge_configs(global_config, local_config):
    if not global_config and not local_config:
        return {"providers": {}}
    if not global_config:
        return local_config
    if not local_config:
        return global_config
    merged = dict(global_config.get("providers", {}))
    merged.update(local_config.get("providers", {}))
    return {"providers": merged}


def load_provider_config():
    global_cfg = _load_json_file(GLOBAL_CONFIG_PATH)
    local_path = os.path.join(os.getcwd(), LOCAL_CONFIG_FILENAME)
    local_cfg = _load_json_file(local_path)
    return _merge_configs(global_cfg, local_cfg)


def get_provider(name):
    config = load_provider_config()
    providers = config.get("providers", {})
    if name not in providers:
        raise ValueError(
            f"Provider '{name}' not found in config. "
            f"Available providers: {list(providers.keys())}"
        )
    provider = providers[name]
    validate_provider(name, provider)
    return provider


def validate_provider(name, provider):
    if not isinstance(provider, dict):
        raise ValueError(f"Provider '{name}' must be a JSON object")

    missing = REQUIRED_FIELDS - set(provider.keys())
    if missing:
        raise ValueError(f"Provider '{name}' missing required fields: {missing}")

    unknown = set(provider.keys()) - ALL_VALID_FIELDS
    if unknown:
        raise ValueError(f"Provider '{name}' has unknown fields: {unknown}")

    api_style = provider["api_style"]
    if api_style not in SUPPORTED_API_STYLES:
        raise ValueError(
            f"Provider '{name}' has unsupported api_style '{api_style}'. "
            f"Supported: {list(SUPPORTED_API_STYLES.keys())}"
        )

    default_models = provider.get("default_models")
    if default_models is not None:
        if not isinstance(default_models, list) or not all(
            isinstance(m, str) for m in default_models
        ):
            raise ValueError(
                f"Provider '{name}': default_models must be a list of strings"
            )
        if len(default_models) == 0:
            raise ValueError(f"Provider '{name}': default_models must not be empty")


def get_translator_class(api_style):
    if api_style not in SUPPORTED_API_STYLES:
        raise ValueError(
            f"Unsupported api_style '{api_style}'. "
            f"Supported: {list(SUPPORTED_API_STYLES.keys())}"
        )
    return SUPPORTED_API_STYLES[api_style]
