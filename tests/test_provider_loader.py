"""`--provider NAME`: a named endpoint standing in for the endpoint flags.

Two files define providers — `bbm_providers.json` in the current directory
and `~/.bbm/providers.json` — and the project one wins on a shared name. What
an entry supplies is only what the command left out: every flag actually
typed outranks it.
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from book_maker import provider_loader
from book_maker.cli import apply_provider
from book_maker.provider_loader import (
    API_STYLES,
    _merge_configs,
    get_provider,
    load_provider_config,
    resolve_provider,
    validate_provider,
)

DEEPSEEK = {
    "api_style": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "default_models": ["deepseek-chat"],
    "env_key": "BBM_DEEPSEEK_API_KEY",
}


@pytest.fixture
def configs(tmp_path, monkeypatch):
    """All three config files, redirected into tmp_path and initially absent.

    The shipped example is redirected too: left pointing at the real one,
    every test here would inherit its eleven vendor entries and stop being
    about the files it writes.
    """
    global_file = tmp_path / "home" / ".bbm" / "providers.json"
    global_file.parent.mkdir(parents=True)
    monkeypatch.setattr(provider_loader, "GLOBAL_CONFIG_PATH", global_file)
    example_file = tmp_path / "shipped" / "bbm_providers.example.json"
    example_file.parent.mkdir(parents=True)
    monkeypatch.setattr(provider_loader, "EXAMPLE_CONFIG_PATH", example_file)
    local_dir = tmp_path / "project"
    local_dir.mkdir()
    monkeypatch.chdir(local_dir)
    return SimpleNamespace(
        global_file=global_file,
        local_file=local_dir / "bbm_providers.json",
        example_file=example_file,
    )


def _write(path, providers):
    path.write_text(json.dumps({"providers": providers}), encoding="utf-8")


def _options(**kwargs):
    """The parsed options apply_provider() reads and fills in."""
    defaults = dict(provider="", api_format=None, api_base=None, model=None)
    defaults.update(kwargs)
    defaults.setdefault("model_list", "")
    return SimpleNamespace(**defaults)


class TestValidation:
    def test_a_full_entry_is_valid(self):
        validate_provider("deepseek", DEEPSEEK)

    def test_api_style_is_the_only_required_field(self):
        validate_provider("minimal", {"api_style": "openai"})

    def test_a_missing_api_style_is_named(self):
        with pytest.raises(ValueError, match="missing required fields"):
            validate_provider("bad", {"base_url": "https://example.com"})

    def test_an_unsupported_api_style_is_named(self):
        with pytest.raises(ValueError, match="unsupported api_style"):
            validate_provider("bad", {"api_style": "nonexistent"})

    def test_a_typo_field_is_not_silently_ignored(self):
        with pytest.raises(ValueError, match="unknown fields"):
            validate_provider("bad", {"api_style": "openai", "typo": "x"})

    @pytest.mark.parametrize("models", ["gpt-4", [123], []])
    def test_default_models_must_be_a_non_empty_list_of_strings(self, models):
        with pytest.raises(ValueError, match="default_models"):
            validate_provider("bad", {"api_style": "openai", "default_models": models})

    @pytest.mark.parametrize("models", [[" "], ["a", ""], ["", "b"]])
    def test_a_blank_model_name_is_refused(self, models):
        # the CLI strips these away and then falls back to its own default,
        # so a book would be billed to a model the entry never named
        with pytest.raises(ValueError, match="default_models"):
            validate_provider("bad", {"api_style": "openai", "default_models": models})

    def test_an_entry_that_is_not_an_object_is_refused(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            validate_provider("bad", "not a dict")


class TestConfigFiles:
    def test_no_files_at_all_is_not_an_error(self, configs):
        assert load_provider_config() == {"providers": {}}

    def test_the_global_file_is_read(self, configs):
        _write(configs.global_file, {"deepseek": DEEPSEEK})
        assert "deepseek" in load_provider_config()["providers"]

    def test_the_project_file_is_read(self, configs):
        _write(configs.local_file, {"siliconflow": {"api_style": "openai"}})
        assert "siliconflow" in load_provider_config()["providers"]

    def test_the_project_file_overrides_the_global_one(self, configs):
        _write(
            configs.global_file,
            {"deepseek": {"api_style": "openai", "base_url": "https://old.example/v1"}},
        )
        _write(configs.local_file, {"deepseek": DEEPSEEK})
        assert get_provider("deepseek") == DEEPSEEK

    def test_entries_from_both_files_are_available(self, configs):
        _write(configs.global_file, {"a": DEEPSEEK})
        _write(configs.local_file, {"b": {"api_style": "openai"}})
        assert set(load_provider_config()["providers"]) == {"a", "b"}

    def test_a_malformed_file_fails_loud(self, configs):
        # silently falling through to the other file could run the book
        # against a different endpoint than the one configured here
        configs.local_file.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="bbm_providers.json"):
            load_provider_config()

    def test_merge_keeps_the_local_value(self):
        merged = _merge_configs(
            {"providers": {"p": {"api_style": "openai", "base_url": "https://old"}}},
            {"providers": {"p": DEEPSEEK}},
        )
        assert merged["providers"]["p"] == DEEPSEEK


class TestUnknownProvider:
    def test_the_error_names_both_files(self, configs):
        _write(configs.local_file, {"deepseek": DEEPSEEK})
        with pytest.raises(ValueError) as err:
            get_provider("ghost")
        message = str(err.value)
        assert "ghost" in message
        assert "bbm_providers.json" in message
        assert ".bbm/providers.json" in message
        # and says what it did find, so a typo is obvious
        assert "deepseek" in message


class TestApiStyles:
    @pytest.mark.parametrize(
        "style,api_format",
        [
            ("openai", "openai"),
            ("anthropic", "anthropic"),
            ("claude", "anthropic"),
            ("gemini", "gemini"),
            ("qwen", "qwen"),
            ("groq", "groq"),
            ("xai", "xai"),
            ("litellm", "litellm"),
        ],
    )
    def test_a_style_is_a_wire_format_and_nothing_else(
        self, configs, style, api_format
    ):
        _write(configs.local_file, {"p": {"api_style": style}})
        route = resolve_provider("p")
        # no base: the CLI falls back to the format's own address
        assert (route.api_format, route.api_base) == (api_format, "")

    def test_every_style_names_a_format_the_cli_accepts(self):
        # a style that resolves to a format --api_format would reject leaves
        # the entry unusable, and the failure would arrive at argparse
        from book_maker.translator import FORMAT_DICT

        assert set(API_STYLES.values()) <= set(FORMAT_DICT)

    def test_an_unknown_style_names_the_ones_that_exist(self, configs):
        _write(configs.local_file, {"p": {"api_style": "grpc"}})
        with pytest.raises(ValueError, match="openai") as err:
            resolve_provider("p")
        message = str(err.value)
        for style in API_STYLES:
            assert style in message

    def test_an_unknown_style_prints_the_openai_entry_to_write_instead(self, configs):
        # almost always a vendor name for a host that speaks the OpenAI
        # shape, and the entry keeps the address it already names
        _write(
            configs.local_file,
            {"p": {"api_style": "fireworks", "base_url": "https://gw.example/v1"}},
        )
        with pytest.raises(ValueError) as err:
            resolve_provider("p")
        message = str(err.value)
        assert '"api_style": "openai"' in message
        assert '"base_url": "https://gw.example/v1"' in message


class TestApplyingToACommand:
    def test_a_provider_fills_in_the_endpoint(self, configs):
        _write(configs.local_file, {"deepseek": DEEPSEEK})
        options = _options(provider="deepseek")

        env_keys = apply_provider(options)

        assert options.api_format == "openai"
        assert options.api_base == "https://api.deepseek.com/v1"
        assert options.model == "deepseek-chat"
        assert env_keys == ("BBM_DEEPSEEK_API_KEY",)

    def test_several_default_models_rotate_in_order(self, configs):
        _write(
            configs.local_file,
            {"p": {"api_style": "openai", "default_models": ["a", "b", "c"]}},
        )
        options = _options(provider="p")

        apply_provider(options)

        assert options.model_list == "a,b,c"
        assert options.model is None

    def test_everything_passed_explicitly_wins(self, configs):
        _write(configs.local_file, {"deepseek": DEEPSEEK})
        options = _options(
            provider="deepseek",
            api_format="anthropic",
            api_base="https://gw.example/v1",
            model="my-model",
        )

        apply_provider(options)

        assert options.api_format == "anthropic"
        assert options.api_base == "https://gw.example/v1"
        assert options.model == "my-model"
        assert options.model_list == ""

    def test_a_model_list_also_outranks_the_default_models(self, configs):
        _write(configs.local_file, {"deepseek": DEEPSEEK})
        options = _options(provider="deepseek", model_list="a,b")

        apply_provider(options)

        assert options.model_list == "a,b"
        assert options.model is None

    def test_no_provider_changes_nothing(self, configs):
        options = _options()
        assert apply_provider(options) == ()
        assert options.api_base is None

    def test_an_unknown_provider_stops_the_run(self, configs):
        with pytest.raises(SystemExit, match="ghost"):
            apply_provider(_options(provider="ghost"))


# --------------------------------------------------------------------------
# prices: what a model charges per million tokens, so the bar can show spent
# --------------------------------------------------------------------------


def _entry(**extra):
    return {"api_style": "openai", "base_url": "https://x.example/v1", **extra}


def test_prices_and_currency_ride_on_the_route(tmp_path, monkeypatch):
    import json
    from book_maker.provider_loader import resolve_provider

    prices = {"luna": {"input": 0.2, "output": 1.2, "cached_input": 0.02}}
    (tmp_path / "bbm_providers.json").write_text(
        json.dumps({"providers": {"p": _entry(prices=prices, currency="usd")}})
    )
    monkeypatch.chdir(tmp_path)
    route = resolve_provider("p")
    assert route.prices == prices and route.currency == "usd"

    (tmp_path / "bbm_providers.json").write_text(
        json.dumps({"providers": {"p": _entry()}})
    )
    route = resolve_provider("p")
    assert route.prices is None and route.currency == "USD"


@pytest.mark.parametrize(
    "prices, complaint",
    [
        ({}, "must map model ids"),
        ({"luna": {"input": 0.2}}, "missing ['output']"),
        ({"luna": {"input": 0.2, "output": 1.2, "cache": 0.1}}, "unknown fields"),
        ({"luna": {"input": "0.2", "output": 1.2}}, "must be a number"),
        ({"luna": {"input": -1, "output": 1.2}}, "must not be negative"),
        ({"luna": 0.2}, "must be an object"),
    ],
)
def test_a_price_that_cannot_be_applied_is_refused(prices, complaint):
    from book_maker.provider_loader import validate_provider

    with pytest.raises(ValueError, match=re.escape(complaint)):
        validate_provider("p", _entry(prices=prices))


def test_a_blank_currency_is_refused():
    from book_maker.provider_loader import validate_provider

    with pytest.raises(ValueError, match="currency"):
        validate_provider(
            "p", _entry(prices={"m": {"input": 1, "output": 2}}, currency=" ")
        )


class TestShippedExample:
    """`bbm_providers.example.json` is the lowest layer.

    The READMEs advertise the vendors in it and tell the reader to copy the
    file; a reader who skips the copy used to be told the name exists
    nowhere. It is read, and every use of it is announced.
    """

    def test_an_example_entry_is_found_when_no_file_was_written(self, configs):
        _write(configs.example_file, {"deepseek": DEEPSEEK})

        assert get_provider("deepseek") == DEEPSEEK

    def test_using_the_example_names_the_address_and_the_key(self, configs, capsys):
        _write(configs.example_file, {"deepseek": DEEPSEEK})
        get_provider("deepseek")
        out = " ".join(capsys.readouterr().out.split())

        # the two things the run would otherwise take without being asked
        assert "https://api.deepseek.com/v1" in out
        assert "BBM_DEEPSEEK_API_KEY" in out
        assert "bbm_providers.example.json" in out

    def test_a_written_entry_wins_and_is_not_announced(self, configs, capsys):
        _write(configs.example_file, {"deepseek": DEEPSEEK})
        _write(
            configs.local_file,
            {"deepseek": {**DEEPSEEK, "base_url": "https://mine/v1"}},
        )

        assert get_provider("deepseek")["base_url"] == "https://mine/v1"
        assert "example" not in capsys.readouterr().out

    def test_a_global_entry_also_wins_over_the_example(self, configs, capsys):
        _write(configs.example_file, {"deepseek": DEEPSEEK})
        _write(
            configs.global_file,
            {"deepseek": {**DEEPSEEK, "base_url": "https://glob/v1"}},
        )

        assert get_provider("deepseek")["base_url"] == "https://glob/v1"
        assert "example" not in capsys.readouterr().out

    def test_a_template_entry_is_not_an_endpoint(self, configs):
        # honouring FILL-ME would send the run to a host that does not exist
        _write(
            configs.example_file,
            {
                "gateway": {
                    "api_style": "openai",
                    "base_url": "https://FILL-ME.example.com/v1",
                    "default_models": ["FILL-ME-model-id"],
                }
            },
        )

        with pytest.raises(ValueError, match="no entry"):
            get_provider("gateway")

    def test_a_missing_example_is_not_an_error(self, configs):
        # absent from a pip install, which packages only book_maker
        assert load_provider_config()["providers"] == {}

    def test_the_shipped_file_is_readable_and_valid(self, monkeypatch):
        # the real one, read from the checkout rather than from wherever
        # book_maker was imported: under `pip install .`, as CI runs, that
        # is site-packages, and the example is not packaged. Every
        # non-template entry it offers has to survive validate_provider, or
        # the fallback hands out a failure
        shipped = Path(__file__).resolve().parent.parent / "bbm_providers.example.json"
        monkeypatch.setattr(provider_loader, "EXAMPLE_CONFIG_PATH", shipped)
        config = provider_loader._load_example_config()

        assert config, "the shipped example should be present in a checkout"
        for name, entry in config["providers"].items():
            validate_provider(name, entry)

    def test_the_shipped_atlascloud_route_is_openai_compatible(self, monkeypatch):
        shipped = Path(__file__).resolve().parent.parent / "bbm_providers.example.json"
        monkeypatch.setattr(provider_loader, "EXAMPLE_CONFIG_PATH", shipped)

        route = resolve_provider("atlascloud")

        assert route.api_format == "openai"
        assert route.api_base == "https://api.atlascloud.ai/v1"
        assert route.models == ["openai/gpt-5.6-luna"]
        assert route.env_key == "ATLASCLOUD_API_KEY"
