"""The migration layer for commands written against the old CLI.

Old invocations keep working: every removed flag is rewritten into the
endpoint surface before argparse sees it, and each rewrite prints what it
became so the user can update their command. Nothing is guessed silently —
a legacy flag with no honest equivalent fails loudly instead.
"""

import pytest

from book_maker.legacy_cli import translate_legacy_argv


def rewrite(*argv):
    """The rewritten argv, ignoring notices."""
    return translate_legacy_argv(list(argv)).argv


def notices(*argv):
    return " ".join(translate_legacy_argv(list(argv)).notices)


def flags(*argv):
    """Rewritten argv as {flag: value}, for cases where order is irrelevant."""
    out = translate_legacy_argv(list(argv)).argv
    return dict(zip(out[::2], out[1::2]))


class TestUntouched:
    def test_a_modern_command_is_passed_through_unchanged(self):
        argv = ["--book_name", "b.epub", "--key", "k", "--model_list", "gpt-5-mini"]
        assert rewrite(*argv) == argv
        assert notices(*argv) == ""

    def test_flags_that_merely_start_the_same_are_left_alone(self):
        argv = ["--model_list", "gpt-5-mini"]
        assert rewrite(*argv) == argv


class TestOpenAIPresets:
    @pytest.mark.parametrize(
        "alias,model",
        [
            ("gpt4omini", "gpt-4o-mini"),
            ("gpt4o", "gpt-4o"),
            ("o3mini", "o3-mini"),
        ],
    )
    def test_each_preset_becomes_its_first_model(self, alias, model):
        # the preset's own head model, not a newer one: translating must not
        # quietly move a run onto a model the user never asked for
        assert rewrite("--model", alias) == ["--model", model]

    @pytest.mark.parametrize("alias", ["openai", "chatgptapi"])
    def test_a_route_alias_carries_no_model(self, alias):
        # both named the OpenAI route, which is the default; the format's own
        # default model must survive, not a retired preset
        assert rewrite("--model", alias) == []
        assert "default" in notices("--model", alias)

    def test_the_openai_alias_keeps_the_users_model_list(self):
        assert rewrite("--model", "openai", "--model_list", "yi-34b") == [
            "--model_list",
            "yi-34b",
        ]

    @pytest.mark.parametrize("alias", ["gpt4", "gpt5mini", "o1", "o1mini", "o1preview"])
    def test_a_retired_alias_travels_as_a_model_id(self, alias):
        # it names a model the endpoint no longer serves; the model check
        # says which one, instead of this layer rewriting it to another
        # retired id and saying nothing
        assert rewrite("--model", alias) == ["--model", alias]
        assert notices("--model", alias) == ""

    def test_a_real_model_id_passes_through_untouched(self):
        # --model now names an actual model; only the old aliases translate
        assert rewrite("--model", "gpt-5.6-luna") == ["--model", "gpt-5.6-luna"]
        assert notices("--model", "gpt-5.6-luna") == ""

    def test_a_vendor_prefixed_id_passes_through(self):
        # gateways address models as "vendor/model"
        assert rewrite("--model", "openai/gpt-5.6-luna") == [
            "--model",
            "openai/gpt-5.6-luna",
        ]

    def test_an_explicit_model_list_wins_over_the_preset_default(self):
        assert rewrite("--model", "gpt4o", "--model_list", "gpt-4.1") == [
            "--model_list",
            "gpt-4.1",
        ]

    def test_the_equals_form_is_understood(self):
        assert rewrite("--model=gpt4o") == ["--model", "gpt-4o"]

    def test_the_short_form_is_understood(self):
        assert rewrite("-m", "gpt4o") == ["--model", "gpt-4o"]


class TestVendorRoutes:
    def test_the_bare_claude_alias_becomes_its_old_default_model(self):
        assert rewrite("--model", "claude") == [
            "--model",
            "claude-haiku-4-5-20251001",
        ]

    def test_an_exact_claude_id_passes_through(self):
        # the anthropic format is inferred from the id at route time, so the
        # legacy layer has nothing to add here
        assert rewrite("--model", "claude-opus-4-6") == ["--model", "claude-opus-4-6"]

    @pytest.mark.parametrize(
        "alias,fmt,model",
        [
            ("gemini", "gemini", "gemini-flash-latest"),
            ("geminipro", "gemini", "gemini-pro-latest"),
            ("xai", "xai", "grok-4.3"),
            ("qwen", "qwen", "qwen-mt-turbo"),
            ("qwen-mt-plus", "qwen", "qwen-mt-plus"),
        ],
    )
    def test_a_route_alias_becomes_its_format_and_its_model(self, alias, fmt, model):
        # the format carries the endpoint's address, so only the model the
        # alias used to default to has to be spelled out
        assert rewrite("--model", alias) == ["--api_format", fmt, "--model", model]

    def test_groq_names_a_format_and_no_model(self):
        # every id in groq's old preset list is retired; the model list the
        # command already carried is what runs
        assert rewrite("--model", "groq", "--model_list", "llama3-8b-8192") == [
            "--api_format",
            "groq",
            "--model_list",
            "llama3-8b-8192",
        ]

    def test_an_explicit_api_base_is_never_overridden(self):
        # a gateway serving gemini ids is the whole reason someone passes both
        assert flags("--model", "gemini", "--api_base", "https://gw/v1") == {
            "--api_base": "https://gw/v1",
            "--api_format": "gemini",
            "--model": "gemini-flash-latest",
        }

    @pytest.mark.parametrize(
        "alias,key_flag",
        [
            ("gemini", "--gemini_key"),
            ("geminipro", "--gemini_key"),
            ("qwen", "--qwen_key"),
            ("groq", "--groq_key"),
            ("xai", "--xai_key"),
        ],
    )
    def test_an_alias_with_its_key_flag_and_another_format_is_refused(
        self, alias, key_flag
    ):
        # the vendor key flag proves the old route was meant; honouring
        # --api_format openai would send that key to api.openai.com, and
        # honouring the alias would ignore what was typed
        with pytest.raises(SystemExit) as err:
            translate_legacy_argv(
                ["--api_format", "openai", "--model", alias, key_flag, "sk-vendor"]
            )
        message = str(err.value)
        assert alias in message and "openai" in message

    def test_an_alias_word_with_no_vendor_key_is_a_model_id(self):
        # a LiteLLM config may name a backend `groq`; the format and the
        # address were both given explicitly, and no vendor key says
        # otherwise, so there is nothing to rewrite
        assert flags(
            "--api_format",
            "openai",
            "--api_base",
            "http://localhost:4000",
            "--model",
            "groq",
            "--key",
            "local",
        ) == {
            "--model": "groq",
            "--api_format": "openai",
            "--api_base": "http://localhost:4000",
            "--key": "local",
        }
        assert (
            notices(
                "--api_format",
                "openai",
                "--api_base",
                "http://localhost:4000",
                "--model",
                "groq",
                "--key",
                "local",
            )
            == ""
        )

    def test_an_alias_beside_its_own_format_only_supplies_the_model(self):
        assert flags("--api_format", "gemini", "--model", "gemini") == {
            "--model": "gemini-flash-latest",
            "--api_format": "gemini",
        }

    def test_a_qwen_model_id_beside_an_explicit_format_is_not_rewritten(self):
        # `qwen-mt-plus` is a real id on the qwen route, so a command that
        # already names the format is a modern one with nothing to apologise
        # for
        assert flags("--api_format", "qwen", "--model", "qwen-mt-plus") == {
            "--api_format": "qwen",
            "--model": "qwen-mt-plus",
        }
        assert notices("--api_format", "qwen", "--model", "qwen-mt-plus") == ""
        # and the same id at a gateway on the openai format, which is a real
        # command too: DashScope's compatibility base serves it
        assert flags("--api_format", "openai", "--model", "qwen-mt-plus") == {
            "--model": "qwen-mt-plus",
            "--api_format": "openai",
        }

    @pytest.mark.parametrize(
        "alias,fmt",
        [
            ("google", "google"),
            ("caiyun", "caiyun"),
            ("deepl", "deepl"),
            ("deeplfree", "deeplfree"),
            ("tencentransmart", "tencent"),
        ],
    )
    def test_machine_translation_aliases_become_formats(self, alias, fmt):
        assert rewrite("--model", alias) == ["--api_format", fmt]


class TestKeys:
    @pytest.mark.parametrize(
        "flag",
        [
            "--openai_key",
            "--claude_key",
            "--gemini_key",
            "--groq_key",
            "--xai_key",
            "--qwen_key",
            "--caiyun_key",
            "--deepl_key",
        ],
    )
    def test_every_key_flag_becomes_key(self, flag):
        assert flags(flag, "secret")["--key"] == "secret"

    def test_api_key_is_not_rewritten(self):
        # --api_key is a second spelling of --key on the parser, so the shim
        # passes it through and nothing is printed about it
        result = translate_legacy_argv(["--api_key", "secret"])
        assert result.argv == ["--api_key", "secret"]
        assert result.notices == []

    def test_the_legacy_key_variable_is_still_consulted(self):
        # --model gemini now routes through the openai format, whose variables
        # are not the one an old user exported
        result = translate_legacy_argv(["--model", "gemini"])
        assert "BBM_GOOGLE_GEMINI_KEY" in result.env_keys

    def test_a_modern_command_adds_no_extra_variables(self):
        assert translate_legacy_argv(["--model_list", "gpt-5-mini"]).env_keys == ()


class TestEndpointFlags:
    def test_ollama_becomes_a_local_endpoint(self):
        assert flags("--ollama_model", "llama3") == {
            "--api_base": "http://localhost:11434/v1",
            "--model": "llama3",
            "--key": "ollama",  # ollama authenticates nobody
        }

    def test_custom_api_carries_its_url_to_api_base(self):
        assert rewrite("--custom_api", "https://host/t") == [
            "--api_format",
            "customapi",
            "--api_base",
            "https://host/t",
        ]

    def test_azure_deployment_becomes_the_model(self):
        assert flags(
            "--api_base", "https://x.openai.azure.com", "--deployment_id", "dep"
        ) == {
            "--api_base": "https://x.openai.azure.com/openai/v1",
            "--model": "dep",
        }

    def test_interval_is_left_alone(self):
        # a real flag again, and the gemini route it paces is back with it
        assert rewrite("--interval", "0.5", "--model_list", "m") == [
            "--interval",
            "0.5",
            "--model_list",
            "m",
        ]
        assert notices("--interval", "0.5", "--model_list", "m") == ""


class TestFaithfulness:
    """An old command must keep doing what it did, or say why it cannot."""

    def test_a_bare_key_command_still_names_no_model(self):
        # `bbook_maker --book_name b.epub --openai_key sk-...` named no model:
        # the old parser defaulted to chatgptapi. The openai format now has
        # its own default, so this layer supplies nothing — injecting the
        # retired preset here would override that default with gpt-3.5-turbo.
        assert flags("--openai_key", "sk") == {"--key": "sk"}
        assert "model" not in notices("--openai_key", "sk")

    def test_no_default_is_invented_for_a_machine_translation_route(self):
        assert rewrite("--model", "google") == ["--api_format", "google"]

    def test_no_default_is_invented_when_a_fixed_format_was_named(self):
        # `--api_format deepl --deepl_key k` asks for one key flag to be
        # rewritten; inventing an OpenAI model turns that into an error,
        # because a fixed engine refuses --model outright
        assert rewrite("--api_format", "deepl", "--deepl_key", "k") == [
            "--key",
            "k",
            "--api_format",
            "deepl",
        ]

    def test_the_key_matching_the_route_wins(self):
        # sending the OpenAI key to Groq would disclose a credential to a
        # third party; the old CLI picked the route's own key flag
        assert (
            flags(
                "--model",
                "groq",
                "--openai_key",
                "OPEN",
                "--groq_key",
                "GROQ",
                "--model_list",
                "llama3",
            )["--key"]
            == "GROQ"
        )

    def test_an_unrelated_key_flag_is_still_honored_alone(self):
        assert (
            flags("--model", "groq", "--openai_key", "OPEN", "--model_list", "m")[
                "--key"
            ]
            == "OPEN"
        )

    def test_custom_api_falls_back_to_its_environment_variable(self, monkeypatch):
        monkeypatch.setenv("BBM_CUSTOM_API", "https://env-host/t")

        assert flags("--model", "customapi") == {
            "--api_format": "customapi",
            "--api_base": "https://env-host/t",
        }

    def test_ollama_stays_keyless_on_a_remote_host(self):
        # the old CLI passed a placeholder key for every ollama route, so a
        # LAN server needed no credential
        assert flags(
            "--ollama_model", "llama3", "--api_base", "http://192.168.1.9:11434/v1"
        ) == {
            "--key": "ollama",
            "--api_base": "http://192.168.1.9:11434/v1",
            "--model": "llama3",
        }

    def test_azure_reaches_its_openai_compatible_path(self):
        # a bare resource root has no /chat/completions; Azure serves the
        # OpenAI shape under /openai/v1
        assert flags(
            "--api_base", "https://res.openai.azure.com", "--deployment_id", "dep"
        ) == {
            "--api_base": "https://res.openai.azure.com/openai/v1",
            "--model": "dep",
        }

    def test_an_azure_base_already_pointing_at_v1_is_left_alone(self):
        assert (
            flags(
                "--api_base",
                "https://res.openai.azure.com/openai/v1",
                "--deployment_id",
                "dep",
            )["--api_base"]
            == "https://res.openai.azure.com/openai/v1"
        )


class TestNotices:
    def test_each_rewrite_states_its_replacement(self):
        text = notices("--model", "gpt4o", "--openai_key", "sk")
        assert "--model gpt4o is now --model gpt-4o" in text
        assert "--openai_key" in text and "--key" in text

    def test_a_secret_is_never_echoed(self):
        assert "sk-secret" not in notices("--openai_key", "sk-secret")

    def test_a_notice_never_claims_a_flag_that_was_overridden(self):
        # reporting the default base while the run used the user's gateway
        # describes a run that did not happen
        text = notices("--ollama_model", "llama3", "--api_base", "https://gw/v1")
        assert "localhost" not in text
        assert "--api_base" in text  # says the user's own was kept

    def test_a_fully_superseded_translation_says_so(self):
        text = notices("--model", "gpt4o", "--model_list", "gpt-4.1")
        assert "--model_list" in text

    def test_a_legacy_flag_without_a_value_explains_itself(self):
        # argparse would say "unrecognized arguments: --model", which reads
        # as "no such flag" when the real problem is a missing value
        with pytest.raises(SystemExit, match="needs a value"):
            translate_legacy_argv(["--model"])

    def test_an_unrecognized_value_is_a_model_id_not_an_error(self):
        # --model names an actual model now; only the old aliases translate,
        # and an id this fork has never heard of is the normal case
        assert rewrite("--model", "gpt3") == ["--model", "gpt3"]


class TestOrcaRouter:
    """`--model orcarouter` is a live route; only its key flag is old."""

    def test_the_old_key_flag_becomes_key(self):
        assert flags("--model", "orcarouter", "--orcarouter_key", "K") == {
            "--key": "K",
            "--model": "orcarouter",
        }
        assert "--orcarouter_key" in notices(
            "--model", "orcarouter", "--orcarouter_key", "K"
        )

    def test_the_model_is_passed_through_untranslated(self):
        # a rewrite here would print a deprecation notice for a supported
        # shortcut; the CLI resolves the route itself
        assert rewrite("--model", "orcarouter/anthropic/claude-sonnet-4-6") == [
            "--model",
            "orcarouter/anthropic/claude-sonnet-4-6",
        ]

    def test_the_key_is_never_echoed(self):
        assert "K-secret" not in notices(
            "--model", "orcarouter", "--orcarouter_key", "K-secret"
        )


class TestApiRoute:
    """`--model apiroute` is a live route; only its key flag is old."""

    def test_the_old_key_flag_becomes_key(self):
        assert flags("--model", "apiroute", "--apiroute_key", "K") == {
            "--key": "K",
            "--model": "apiroute",
        }
        assert "--apiroute_key" in notices(
            "--model", "apiroute", "--apiroute_key", "K"
        )

    def test_the_model_is_passed_through_untranslated(self):
        assert rewrite("--model", "apiroute/claude-3-7-sonnet-20250219") == [
            "--model",
            "apiroute/claude-3-7-sonnet-20250219",
        ]

    def test_the_key_is_never_echoed(self):
        assert "K-secret" not in notices(
            "--model", "apiroute", "--apiroute_key", "K-secret"
        )


class TestCodex:
    """`--model codex` named the sidecar; `--api_format codex` does now."""

    def test_the_route_name_becomes_the_format(self):
        assert rewrite("--model", "codex") == ["--api_format", "codex"]
        assert "--model codex is now --api_format codex" in notices("--model", "codex")

    def test_the_name_is_matched_however_it_was_capitalised(self):
        assert rewrite("-m", "Codex") == ["--api_format", "codex"]
        assert rewrite("--model", "CODEX") == ["--api_format", "codex"]

    def test_a_named_model_still_rides_on_the_explicit_format(self):
        assert rewrite("--api_format", "codex", "--model", "gpt-5.4-mini") == [
            "--model",
            "gpt-5.4-mini",
            "--api_format",
            "codex",
        ]
        assert notices("--api_format", "codex", "--model", "gpt-5.4-mini") == ""

    def test_an_explicit_other_format_keeps_codex_as_a_model_id(self):
        # codex has no key flag, so the old route can never be proven meant
        # against a different explicit format: the id is handed through, and
        # nothing crashes on the missing key flag
        assert rewrite("--api_format", "openai", "--model", "codex") == [
            "--model",
            "codex",
            "--api_format",
            "openai",
        ]
        assert notices("--api_format", "openai", "--model", "codex") == ""
