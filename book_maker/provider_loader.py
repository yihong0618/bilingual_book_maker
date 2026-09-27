"""Named endpoints: `--provider NAME`, read from a JSON file.

A provider entry is shorthand for the endpoint flags — where the requests go
(`base_url`), what wire format the host speaks (`api_style`), which models to
run (`default_models`) and which variable holds the key (`env_key`). It saves
repeating `--api_base` and a key on every command; it decides nothing the
command itself said, because anything passed explicitly wins.

Three files are read, lowest precedence first: the shipped
`bbm_providers.example.json` next to `make_book.py`, `~/.bbm/providers.json`,
and `bbm_providers.json` in the current directory. A project entry overrides
a global one of the same name, so a repo can pin the endpoint its scripts
expect, and either overrides the shipped example.

The example is read because the READMEs advertise the vendors in it and tell
the reader to copy the file; a reader who skips the copy used to be told the
name has no entry anywhere. Using it is announced, every time, so nobody
runs against an endpoint they never chose.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from rich import print

GLOBAL_CONFIG_PATH = Path.home() / ".bbm" / "providers.json"
LOCAL_CONFIG_FILENAME = "bbm_providers.json"
# Shipped next to make_book.py, one directory above this package.
EXAMPLE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "bbm_providers.example.json"
)
# The example's own marker for an entry the reader is meant to fill in. Such
# an entry is a template, not an endpoint: honouring it would send the run to
# a host that does not exist.
PLACEHOLDER_MARKER = "FILL-ME"

# `api_style` -> `--api_format`: what the host on the other end speaks, and
# so which route calls it. Every `--api_format` that names an endpoint is a
# style, plus `claude` as the older spelling of `anthropic`; `codex` is not,
# having no address or key for an entry to carry. A host with no style of
# its own — any of the many that serve the OpenAI shape — is `"openai"`
# with its address in `base_url`.
API_STYLES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "qwen": "qwen",
    "groq": "groq",
    "xai": "xai",
    "litellm": "litellm",
}

# Styles that name a classifier rather than a translation route. TypeSafe's
# System One models ("jev") answer typed questions and translate nothing, so
# such an entry can describe the classify endpoint but never be the run's own
# `--provider` (see `resolve_provider`).
CLASSIFIER_STYLES = {"jev": "jev"}

REQUIRED_FIELDS = {"api_style"}
OPTIONAL_FIELDS = {"base_url", "default_models", "env_key", "prices", "currency"}
# The two extra endpoints an entry may carry beside its own (packet F,
# 260923): the vision model for the steps that look at a page image, and the
# model for every classification step. Each is a model id, an optional
# address (else the entry's own) and an optional key variable.
ENDPOINT_FIELDS = {
    "img_model",
    "img_base_url",
    "img_env_key",
    "classify_model",
    "classify_base_url",
    "classify_env_key",
}
ALL_VALID_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS | ENDPOINT_FIELDS

# `prices`: {model id: {"input", "output", optional "cached_input"}}, each a
# price per million tokens in `currency` (default USD). With a price for
# every model the run uses, the progress bar shows what was spent instead
# of token counts. `cached_input` left out means cache reads cost the
# input price — the conservative reading, never an optimistic one.
PRICE_REQUIRED = {"input", "output"}
PRICE_FIELDS = PRICE_REQUIRED | {"cached_input"}


@dataclass
class ProviderRoute:
    """The endpoint flags a provider entry stands for."""

    api_format: str
    api_base: str
    models: list
    env_key: str
    prices: dict = None
    currency: str = "USD"
    # `--img-*` and `--classify-*` as the entry names them; "" when absent.
    img_model: str = ""
    img_base_url: str = ""
    img_env_key: str = ""
    classify_model: str = ""
    classify_base_url: str = ""
    classify_env_key: str = ""


def _load_json_file(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # Falling through to the other file would silently run the book
        # against a different endpoint than this one configures.
        raise ValueError(f"{path} could not be read: {e}")


def _merge_configs(*configs):
    """Every file's providers, each one winning over the ones before it."""
    merged = {}
    for config in configs:
        merged.update((config or {}).get("providers", {}))
    return {"providers": merged}


def _is_placeholder(provider):
    """An example entry still carrying its FILL-ME text, in any field."""
    return PLACEHOLDER_MARKER in json.dumps(provider)


def _load_example_config():
    """The shipped entries, minus the ones that are templates.

    A missing file is not an error: it is absent from a pip install, which
    only packages `book_maker`.
    """
    config = _load_json_file(EXAMPLE_CONFIG_PATH)
    if not config:
        return None
    return {
        "providers": {
            name: entry
            for name, entry in config.get("providers", {}).items()
            if not _is_placeholder(entry)
        }
    }


def _written_configs():
    """The two files the user writes, global first."""
    return (
        _load_json_file(GLOBAL_CONFIG_PATH),
        _load_json_file(os.path.join(os.getcwd(), LOCAL_CONFIG_FILENAME)),
    )


def load_provider_config():
    """Every provider entry in scope, each file winning over the ones before."""
    return _merge_configs(_load_example_config(), *_written_configs())


def written_provider_names():
    """The names defined in a file the user actually wrote."""
    return set(_merge_configs(*_written_configs())["providers"])


def validate_provider(name, provider):
    """Refuse an entry that cannot be honored, naming what is wrong with it."""
    if not isinstance(provider, dict):
        raise ValueError(f"provider {name!r} must be a JSON object")

    missing = REQUIRED_FIELDS - set(provider)
    if missing:
        raise ValueError(
            f"provider {name!r} is missing required fields: {sorted(missing)}"
        )

    unknown = set(provider) - ALL_VALID_FIELDS
    if unknown:
        raise ValueError(f"provider {name!r} has unknown fields: {sorted(unknown)}")

    api_style = provider["api_style"]
    if api_style not in API_STYLES and api_style not in CLASSIFIER_STYLES:
        # Almost always a vendor name for a host that speaks the OpenAI
        # shape, so the fix is worth printing rather than describing.
        raise ValueError(
            f"provider {name!r} has unsupported api_style {api_style!r}. "
            f"Supported: {', '.join(sorted({**API_STYLES, **CLASSIFIER_STYLES}))}. "
            f"A host that serves "
            f"the OpenAI shape at its own address is:\n"
            f'  "api_style": "openai",\n'
            f'  "base_url": "{provider.get("base_url") or "https://..."}"'
        )

    _validate_prices(name, provider)
    _validate_endpoint_fields(name, provider)

    models = provider.get("default_models")
    if models is None:
        return
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise ValueError(f"provider {name!r}: default_models must be a list of strings")
    if not models:
        raise ValueError(f"provider {name!r}: default_models must not be empty")
    if not all(m.strip() for m in models):
        # the CLI strips a blank name away and then falls back to its own
        # default model, so the entry would silently not be honored
        raise ValueError(
            f"provider {name!r}: default_models must not contain a blank name"
        )


def _validate_endpoint_fields(name, provider):
    """`img_*` / `classify_*`: each a non-blank string when present.

    A blank model would read as "not set" and quietly fall through to the
    next source in the chain, which is not what an entry that names the field
    asked for.
    """
    for field in sorted(ENDPOINT_FIELDS):
        if field not in provider:
            continue
        value = provider[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"provider {name!r}: {field} must be a non-blank string")


def _validate_prices(name, provider):
    currency = provider.get("currency")
    if currency is not None and (not isinstance(currency, str) or not currency.strip()):
        raise ValueError(f"provider {name!r}: currency must be a code such as USD")
    prices = provider.get("prices")
    if prices is None:
        return
    if not isinstance(prices, dict) or not prices:
        raise ValueError(
            f"provider {name!r}: prices must map model ids to "
            f'{{"input", "output", "cached_input"}} per million tokens'
        )
    for model, price in prices.items():
        if not isinstance(price, dict):
            raise ValueError(f"provider {name!r}: prices[{model!r}] must be an object")
        missing = PRICE_REQUIRED - set(price)
        if missing:
            raise ValueError(
                f"provider {name!r}: prices[{model!r}] is missing {sorted(missing)}"
            )
        unknown = set(price) - PRICE_FIELDS
        if unknown:
            raise ValueError(
                f"provider {name!r}: prices[{model!r}] has unknown fields "
                f"{sorted(unknown)}; known: {sorted(PRICE_FIELDS)}"
            )
        for field, value in price.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"provider {name!r}: prices[{model!r}].{field} must be a "
                    f"number of {provider.get('currency') or 'USD'} per million tokens"
                )
            if value < 0:
                raise ValueError(
                    f"provider {name!r}: prices[{model!r}].{field} must not be negative"
                )


def get_provider(name):
    """The validated entry for `name`, or a failure naming where it was sought.

    An entry the user never wrote — one taken from the shipped example — is
    announced rather than used quietly: it names an address and a key
    variable, and the reader who skipped the copy step never chose either.
    """
    providers = load_provider_config()["providers"]
    if name not in providers:
        known = ", ".join(sorted(providers)) or "none"
        raise ValueError(
            f"--provider {name} has no entry in {LOCAL_CONFIG_FILENAME} (this "
            f"directory), {GLOBAL_CONFIG_PATH}, or the shipped "
            f"{EXAMPLE_CONFIG_PATH.name}. Providers defined there: {known}."
        )
    if name not in written_provider_names():
        entry = providers[name]
        print(
            f"[bold yellow]Warning:[/bold yellow] --provider {name} is not in "
            f"{LOCAL_CONFIG_FILENAME} or {GLOBAL_CONFIG_PATH}; using the "
            f"shipped {EXAMPLE_CONFIG_PATH.name}, which sends this run to "
            f"{entry.get('base_url') or 'the format default address'} with "
            f"the key in {entry.get('env_key') or 'no variable'}. Copy the "
            f"file to {LOCAL_CONFIG_FILENAME} to pin these yourself."
        )
    provider = providers[name]
    validate_provider(name, provider)
    return provider


def resolve_provider(name):
    """`name` as endpoint settings: format, base, models to rotate, key variable."""
    provider = get_provider(name)
    if provider["api_style"] in CLASSIFIER_STYLES:
        raise ValueError(
            f"--provider {name} is a {provider['api_style']} entry: it "
            f"classifies and cannot translate. Name the classifier with "
            f"--classify-model {provider['api_style']} (or a translation "
            f"entry's classify_model) instead."
        )
    return ProviderRoute(
        api_format=API_STYLES[provider["api_style"]],
        api_base=provider.get("base_url") or "",
        models=list(provider.get("default_models") or []),
        env_key=provider.get("env_key") or "",
        prices=provider.get("prices") or None,
        currency=(provider.get("currency") or "USD").strip(),
        **{field: provider.get(field, "").strip() for field in ENDPOINT_FIELDS},
    )
