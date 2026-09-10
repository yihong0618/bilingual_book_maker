from book_maker.cli import get_book_type, main
from book_maker.loader.classify import PLAN_HANDOFF_EXIT_CODE


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"


import json
import os
import re

import pytest
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
# tests/hermetic/sitecustomize.py swaps the `google` translator for an
# offline one at interpreter startup. These are CLI *contract* tests — flag
# wiring, mode selection, what gets written — and routing them through a
# public translation endpoint made them fail on proxy errors and impossible
# to run offline. Live provider calls belong to tests/test_integration.py.
HERMETIC = Path(__file__).resolve().parent / "hermetic"


# Credentials the CLI falls back to. Left in place they would decide test
# outcomes from whatever the developer happens to have exported.
KEY_ENV_VARS = (
    "BBM_API_KEY",
    "OPENAI_API_KEY",
    "BBM_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BBM_CLAUDE_API_KEY",
    "BBM_CAIYUN_API_KEY",
    "BBM_DEEPL_API_KEY",
    "BBM_ORCAROUTER_API_KEY",
)


def _env():
    env = dict(os.environ)
    for name in KEY_ENV_VARS:
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env


def _cli(*args):
    return subprocess.run(
        [sys.executable, "make_book.py", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=_env(),
    )


def _run(tmp_path, *args):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--api_format", "google", *args)
    return proc, src.parent / (src.stem + "_plan.json")


def test_plan_classify_implies_plan_mode(tmp_path):
    # any classification choice is a choice to have a plan; no second flag
    # is needed to enter plan mode
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    # the handoff is not a finished translation, and says so
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
    assert plan.exists()
    assert "Paste the block below" in proc.stdout


def test_api_key_is_the_same_flag_as_key(tmp_path):
    # --api_key is a second spelling on the parser, not a legacy flag: the
    # run takes it and nothing is printed about it
    proc, _ = _run(tmp_path, "--api_key", "secret", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "deprecated" not in proc.stdout
    assert "--api_key" not in proc.stdout


def test_no_classify_flag_keeps_legacy_tag_mode(tmp_path):
    # the flag is opt-in: without it nothing about today's behavior changes
    proc, plan = _run(tmp_path, "--test", "--test_num", "1")
    assert proc.returncode == 0
    assert not plan.exists()


def test_explicit_none_is_the_same_as_no_flag(tmp_path):
    # 'none' denotes the default: ordinary --translate-tags selection, no plan
    proc, plan = _run(tmp_path, "--plan-classify", "none", "--test", "--test_num", "1")
    assert proc.returncode == 0
    assert not plan.exists()


def test_all_mode_translates_without_asking_or_writing_a_plan(tmp_path):
    # 'all' is the deliberate translate-everything entry: no questions, so
    # no plan file to answer them in, and no agent stop
    proc, plan = _run(tmp_path, "--plan-classify", "all", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "Paste the block below" not in proc.stdout


def test_all_mode_ignores_an_existing_plan(tmp_path):
    # half-loading an earlier run's skips would make "all" quietly mean
    # "all, except whatever something else decided"
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert plan.exists()
    proc, _ = _run(tmp_path, "--plan-classify", "all", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignores the existing plan" in " ".join(proc.stdout.split())


def test_most_is_the_old_name_of_all(tmp_path):
    # the mode translates the whole partition, and "all" is what that is.
    # Old command lines keep working and are corrected once, out loud.
    proc, plan = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--plan-classify most is now --plan-classify all" in " ".join(
        proc.stdout.split()
    )
    assert not plan.exists()


def test_the_retired_name_is_not_advertised():
    proc = _cli("--help")
    text = " ".join(proc.stdout.split())
    assert "--plan-classify {auto,none,all,model,agent}" in text
    # the choices list is the whole advertisement; "most" is parsed, not shown
    assert "'most'" not in text
    assert "agent,most" not in text


def test_explicit_tag_list_loses_to_the_classify_flag(tmp_path):
    proc, plan = _run(tmp_path, "--plan-classify", "agent", "--translate-tags", "div,p")
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
    assert plan.exists()
    # rich wraps long lines at terminal width, so compare wrap-insensitively
    assert "ignoring --translate-tags div,p" in " ".join(proc.stdout.split())


def test_translate_tags_auto_is_an_ordinary_tag(tmp_path):
    # review finding: the loader used to key plan mode off the literal tag
    # string, so `--translate-tags auto` was an undocumented backdoor into
    # plan mode. It is now just a tag name that matches nothing.
    proc, plan = _run(tmp_path, "--translate-tags", "auto", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "Translation plan" not in proc.stdout


def test_default_tags_are_overridden_quietly(tmp_path):
    # the untouched default "p" is not a selection worth a warning
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
    assert plan.exists()
    assert "ignoring --translate-tags" not in proc.stdout


def test_plan_dry_run_writes_a_fresh_plan(tmp_path):
    # regression: the dry-run path kept a reference to the removed
    # --plan-no-classify option and crashed right after writing the plan
    proc, plan = _run(tmp_path, "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert plan.exists()
    assert "plan written to" in proc.stdout


def test_a_corrupted_plan_fails_clean_not_with_a_traceback(tmp_path):
    # the plan JSON is the one file this workflow asks a person to hand-edit,
    # so its lint failure is the error a user is most likely to meet. All four
    # corruption classes already fail correctly (exit 1, before any API call,
    # accurate message) — this pins the *presentation*: the ledger's own
    # words, not an 18-line Python traceback.
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert plan.exists()
    data = json.loads(plan.read_text())
    data["signatures"][0]["action"] = "translate-everything"
    plan.write_text(json.dumps(data))
    proc, _ = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    flat = " ".join(proc.stdout.split())
    assert "invalid row" in flat
    assert "invalid action" in flat


def test_classify_flag_rejects_non_epub_books(tmp_path):
    # plan mode is epub-only, and agent mode promises to stop before
    # spending anything: silently translating a txt book instead would be
    # the opposite of what was asked
    src = tmp_path / "the_little_prince.txt"
    src.write_bytes((REPO / "test_books" / "the_little_prince.txt").read_bytes())
    proc = _cli(
        "--book_name", str(src), "--api_format", "google", "--plan-classify", "agent"
    )
    assert proc.returncode == 1
    assert "epub-only" in proc.stdout


def test_agent_mode_rejects_a_classifier_model(tmp_path):
    proc, _ = _run(
        tmp_path, "--plan-classify", "agent", "--plan-classify-model", "gpt-4o"
    )
    assert proc.returncode == 1
    assert "cannot be combined" in proc.stdout


def test_all_mode_rejects_a_classifier_model(tmp_path):
    # 'all' explicitly skips classification; naming a classifier alongside
    # it is a contradiction, not a preference to resolve silently
    proc, _ = _run(
        tmp_path, "--plan-classify", "all", "--plan-classify-model", "gpt-4o"
    )
    assert proc.returncode == 1
    assert "cannot be combined" in proc.stdout


def test_classify_model_flag_implies_model_mode(tmp_path):
    # naming a classifier is asking for model mode; it must not silently
    # sit in none mode doing nothing
    proc, plan = _run(
        tmp_path,
        "--plan-classify-model",
        "no-such-model",
        "--test",
        "--test_num",
        "1",
    )
    # google translates through one fixed engine with no model to ask, and a
    # classifier that cannot run must block rather than degrade into
    # translating undecided rows. Refused at the CLI now (audit row A10):
    # the run used to parse the whole book and write a plan file nothing had
    # decided before dying in the classifier.
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--plan-classify-model" in flat
    assert "no model to ask" in flat
    # and it must say what to do instead, not just what failed
    assert "--plan-classify agent" in flat
    # nothing was parsed or written on the way to the refusal
    assert not plan.exists()


def test_naming_a_model_for_a_fixed_engine_fails_loud(tmp_path):
    # the machine-translation formats run one fixed engine; honoring a model
    # is impossible, so refuse rather than ignore it
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--api_format", "google", "--model", "some-model"
    )
    assert proc.returncode == 1
    assert "--model" in proc.stdout
    assert "google" in proc.stdout


def test_naming_a_model_twice_fails_loud(tmp_path):
    # two answers to "which model is this run using" is one too many
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--key",
        "k",
        "--model",
        "a",
        "--model_list",
        "b",
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "once" in output


def test_the_openai_format_defaults_to_a_model(tmp_path):
    # a command with only a key used to die on "--model is required"; the
    # openai format has one obvious cheapest current model, so it just runs
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--key", "sk-test", "--test", "--test_num", "1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['gpt-5.6-luna']" in proc.stdout


def test_an_old_key_flag_alone_lands_on_the_default_model(tmp_path):
    # the old parser defaulted to chatgptapi, so `--openai_key sk-...` named
    # no model; it now gets the format's default rather than a retired preset
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--openai_key", "sk-test", "--test", "--test_num", "1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['gpt-5.6-luna']" in proc.stdout
    assert "gpt-3.5-turbo" not in proc.stdout


def test_the_anthropic_format_still_asks_for_a_model(tmp_path):
    # no id there is the obvious cheapest one, and guessing would bill a
    # whole book to a model nobody chose
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--key", "sk-test", "--api_format", "anthropic"
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "--model is required for the anthropic format" in " ".join(output.split())


def test_missing_key_names_where_it_looked(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "some-model")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "--key" in output
    assert "BBM_API_KEY" in output


def test_anthropic_format_is_inferred_from_the_endpoint(tmp_path):
    # --api_format is not required when the host already says which shape it
    # speaks; the key error proves which route was selected
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_base",
        "https://api.anthropic.com",
        "--model",
        "claude-haiku-4-5-20251001",
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "anthropic endpoint" in output
    assert "ANTHROPIC_API_KEY" in output


def test_a_route_specific_key_outranks_a_generic_one(monkeypatch):
    # an old --model groq command implies BBM_GROQ_API_KEY; picking
    # OPENAI_API_KEY instead would send one vendor's credential to another
    from book_maker.cli import resolve_api_key

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("BBM_GROQ_API_KEY", "groq-secret")

    key = resolve_api_key(
        "openai", "", "https://api.groq.com/openai/v1", ("BBM_GROQ_API_KEY",)
    )

    assert key == "groq-secret"


def test_a_local_endpoint_needs_no_key(tmp_path, monkeypatch):
    # ollama and friends authenticate nobody; requiring a key there was pure
    # ceremony (the old CLI had a dedicated --ollama_model flag for it)
    from book_maker.cli import resolve_api_key

    monkeypatch.delenv("BBM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BBM_OPENAI_API_KEY", raising=False)

    assert resolve_api_key("openai", "", "http://localhost:11434/v1") == "local"


def test_api_format_codex_needs_no_model(tmp_path):
    # The skill and the README spell this route `--api_format codex`, with no
    # --model: the sidecar picks its own default. If codex ever falls out of
    # MODEL_OPTIONAL_FORMATS the run dies at the model gate instead, before
    # anything codex-shaped is even tried.
    #
    # PATH is emptied so the sidecar cannot start: the run must get far
    # enough to look for the binary, and no further. A machine with codex
    # installed and signed in would otherwise spend the user's plan here.
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    env = _env()
    env["PATH"] = str(tmp_path / "no-binaries-here")
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(src),
            "--api_format",
            "codex",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=env,
    )
    out = proc.stdout + proc.stderr
    assert "--model is required" not in out
    # and the missing binary is the one line it was written as, not a
    # traceback: preflight exists to say this before any paid request
    assert "Install the Codex CLI" in out
    assert "Traceback" not in out
    assert proc.returncode == 1


def test_parallel_workers_is_refused_with_codex(tmp_path):
    # codex serializes every turn on one thread, and the parallel path then
    # reads a context attribute Codex does not have — an AttributeError that
    # only lands once the run is already under way. Refuse it up front
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--parallel-workers", "4")
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "codex" in flat
    # nothing may have been dispatched: no output book, no sidecar preflight
    assert not (tmp_path / f"{src.stem}_bilingual.epub").exists()
    assert "codex app-server" not in proc.stdout + proc.stderr


def test_parallel_workers_with_session_context_is_refused(tmp_path):
    # one history is the context; a worker cannot share it, and a fresh
    # history per chapter is window mode at session prices
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "session",
        "--parallel-workers",
        "4",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "--use_context session" in flat
    # refused before anything is dispatched
    assert not list(tmp_path.glob("*_bilingual.epub"))


def test_parallel_workers_still_runs_with_window_context(tmp_path):
    # only the two pairings are refused; the openai window route is untouched
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "--parallel-workers",
        "4",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--parallel-workers" not in proc.stdout


def test_one_worker_is_never_refused(tmp_path):
    # 1 worker is what every run already does, in either pairing
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "session",
        "--parallel-workers",
        "1",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # a dry run reaches no model at all, so the codex side of the boundary
    # can be checked without starting a sidecar
    proc = _cli(
        "--book_name",
        str(tmp_path / BOOK.name),
        "--model",
        "codex",
        "--parallel-workers",
        "1",
        "--plan-dry-run",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_quiet_flag_is_accepted(tmp_path):
    proc, plan = _run(tmp_path, "--plan-dry-run", "--quiet")
    assert proc.returncode == 0
    assert plan.exists()


def test_prompt_json_accepts_a_style_field():
    from book_maker.cli import parse_prompt_arg

    prompt = parse_prompt_arg(
        json.dumps({"user": "translate {text}", "style": "plain modern prose"})
    )
    assert prompt["style"] == "plain modern prose"


def test_prompt_json_still_rejects_unknown_keys():
    from book_maker.cli import parse_prompt_arg

    with pytest.raises(ValueError):
        parse_prompt_arg(json.dumps({"user": "{text}", "nonsense": "x"}))


def test_style_reaches_the_translator_kwargs():
    from book_maker.utils import prompt_config_to_kwargs

    kwargs = prompt_config_to_kwargs({"user": "{text}", "style": "terse"})
    assert kwargs["style_note"] == "terse"


def test_no_style_is_none():
    from book_maker.utils import prompt_config_to_kwargs

    assert prompt_config_to_kwargs({"user": "{text}"})["style_note"] is None


def _cli_in(cwd, *args, **extra_env):
    """The CLI run from `cwd`, so a project bbm_providers.json is in scope."""
    env = _env()
    env["PYTHONPATH"] = os.pathsep.join([str(HERMETIC), str(REPO), env["PYTHONPATH"]])
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(REPO / "make_book.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


def _provider_book(tmp_path, **entry):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    (tmp_path / "bbm_providers.json").write_text(
        json.dumps({"providers": {"p": entry}}), encoding="utf-8"
    )
    return src


def test_a_provider_supplies_the_models_and_its_key_variable(tmp_path):
    # the entry's env_key is consulted for the key, and its default_models
    # rotate in order — no --api_base, --key or --model on the command
    src = _provider_book(
        tmp_path,
        api_style="openai",
        base_url="https://api.deepseek.example/v1",
        default_models=["model-one", "model-two"],
        env_key="BBM_TEST_PROVIDER_KEY",
    )
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--provider",
        "p",
        "--test",
        "--test_num",
        "1",
        BBM_TEST_PROVIDER_KEY="sk-provider",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['model-one', 'model-two']" in proc.stdout


def test_a_provider_and_a_model_are_not_mutually_exclusive(tmp_path):
    # naming the gateway and the model at it is the ordinary case; the
    # command's own model wins over the entry's default_models
    src = _provider_book(
        tmp_path,
        api_style="openai",
        base_url="https://api.deepseek.example/v1",
        default_models=["model-one"],
    )
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--provider",
        "p",
        "--model",
        "my-model",
        "--key",
        "sk-test",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['my-model']" in proc.stdout


def test_an_unknown_provider_names_both_config_files(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli_in(tmp_path, "--book_name", str(src), "--provider", "ghost")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "ghost" in output
    assert "bbm_providers.json" in output
    assert ".bbm/providers.json" in output


def test_orcarouter_needs_no_endpoint_and_reads_its_own_key(tmp_path):
    # upstream's documented command, unchanged: the gateway's model id says
    # where the request goes, and BBM_ORCAROUTER_API_KEY carries the key
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--model",
        "orcarouter",
        "--test",
        "--test_num",
        "1",
        BBM_ORCAROUTER_API_KEY="sk-orca",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "animal_farm_bilingual.epub").exists()
    # a supported route, not a legacy alias: nothing is deprecated here
    assert "deprecated" not in proc.stdout


def _options(**kwargs):
    """The flags `resolve_endpoint` reads, defaulting to "not passed"."""
    from types import SimpleNamespace

    return SimpleNamespace(
        model=kwargs.pop("model", ""),
        model_list=kwargs.pop("model_list", ""),
        api_base=kwargs.pop("api_base", ""),
        api_format=kwargs.pop("api_format", ""),
        provider=kwargs.pop("provider", ""),
        **kwargs,
    )


@pytest.fixture
def provider_entry(tmp_path, monkeypatch):
    """Write a `p` provider entry and run from a directory that sees it."""

    def write(**entry):
        (tmp_path / "bbm_providers.json").write_text(
            json.dumps({"providers": {"p": entry}}), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

    return write


class TestVendorFormats:
    """The routes that name one vendor's endpoint.

    `gemini` and `qwen` speak protocols of their own; `groq`, `xai` and
    `litellm` are the OpenAI shape at another address. Either way the
    format alone has to be a complete route, or the flag saves nobody
    anything over typing the URL.
    """

    @pytest.mark.parametrize(
        "fmt,model",
        [("gemini", "gemini-flash-latest"), ("qwen", "qwen-mt-turbo")],
    )
    def test_a_native_format_names_the_model_its_alias_used_to_run(self, fmt, model):
        from book_maker.cli import resolve_endpoint

        options = _options(api_format=fmt)
        models, api_format, _ = resolve_endpoint(options)

        assert (models, api_format) == ([model], fmt)
        # the SDK knows the vendor's host; naming it here would be a second
        # answer to the same question
        assert options.api_base == ""

    @pytest.mark.parametrize(
        "fmt,base",
        [
            ("groq", "https://api.groq.com/openai/v1"),
            ("xai", "https://api.x.ai/v1"),
            ("litellm", "http://localhost:4000"),
        ],
    )
    def test_an_openai_shaped_format_fills_in_its_address(self, fmt, base):
        from book_maker.cli import resolve_endpoint

        options = _options(api_format=fmt)
        models, api_format, _ = resolve_endpoint(options)

        assert api_format == fmt
        assert options.api_base == base
        # no model: those catalogues turn over, and the CLI asks for one
        assert models == []

    def test_a_command_that_names_an_address_keeps_it(self):
        from book_maker.cli import resolve_endpoint

        options = _options(api_format="groq", api_base="https://gw.example/v1")
        resolve_endpoint(options)

        assert options.api_base == "https://gw.example/v1"

    @pytest.mark.parametrize(
        "fmt,variable",
        [
            ("gemini", "BBM_GOOGLE_GEMINI_KEY"),
            ("qwen", "BBM_QWEN_API_KEY"),
            ("groq", "BBM_GROQ_API_KEY"),
            ("xai", "BBM_XAI_API_KEY"),
        ],
    )
    def test_each_format_reads_its_own_key_variable(self, monkeypatch, fmt, variable):
        from book_maker.cli import resolve_api_key

        monkeypatch.delenv("BBM_API_KEY", raising=False)
        monkeypatch.setenv(variable, "vendor-secret")

        assert resolve_api_key(fmt, "", "") == "vendor-secret"

    @pytest.mark.parametrize("fmt", ["gemini", "qwen", "groq", "xai"])
    def test_a_missing_key_names_where_it_looked(self, monkeypatch, fmt):
        from book_maker.cli import FORMAT_ENV_KEYS, resolve_api_key

        for name in FORMAT_ENV_KEYS[fmt]:
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(SystemExit) as err:
            resolve_api_key(fmt, "", "")
        assert FORMAT_ENV_KEYS[fmt][-1] in str(err.value)

    def test_a_litellm_proxy_on_this_machine_needs_no_key(self, monkeypatch):
        # the default address is localhost, so the run authenticates nobody
        from book_maker.cli import resolve_api_key, resolve_endpoint

        monkeypatch.delenv("BBM_API_KEY", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        options = _options(api_format="litellm", model="gpt-4o")
        _, api_format, _ = resolve_endpoint(options)

        assert resolve_api_key(api_format, "", options.api_base) == "local"

    def test_a_remote_litellm_proxy_still_asks_for_one(self, monkeypatch):
        from book_maker.cli import resolve_api_key

        monkeypatch.delenv("BBM_API_KEY", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        monkeypatch.delenv("BBM_LITELLM_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            resolve_api_key("litellm", "", "https://proxy.example.com")

    def test_the_gemini_route_can_be_paced(self):
        # --interval is applied on this route and described as ignored
        # everywhere else; the CLI calls this method unconditionally there
        from book_maker.translator import FORMAT_DICT
        from book_maker.cli import build_parser

        assert hasattr(FORMAT_DICT["gemini"], "set_interval")
        assert build_parser().parse_args(["--book_name", "b.epub"]).interval == 0.01

    @pytest.mark.parametrize(
        "fmt,example",
        [
            ("groq", "llama-3.3-70b-versatile"),
            ("xai", "grok-4.3"),
            ("anthropic", "claude-sonnet-4-6"),
        ],
    )
    def test_asking_for_a_model_names_one_that_endpoint_serves(
        self, tmp_path, fmt, example
    ):
        # a Claude id offered as the example for the groq format sends the
        # reader to an id groq refuses
        src = tmp_path / BOOK.name
        src.write_bytes(BOOK.read_bytes())
        proc = _cli("--book_name", str(src), "--key", "sk-test", "--api_format", fmt)

        assert proc.returncode != 0
        output = " ".join((proc.stdout + proc.stderr).split())
        assert f"--model is required for the {fmt} format" in output
        assert example in output

    def test_a_format_that_moves_the_endpoint_drops_the_providers_key(
        self, provider_entry
    ):
        # a groq entry with no base_url takes its address from its format, so
        # --api_format xai moves the request to api.x.ai; leading with
        # GROQ_KEY there would hand xAI the Groq credential
        from book_maker.cli import resolve_endpoint

        provider_entry(api_style="groq", env_key="BBM_TEST_PROVIDER_KEY")
        options = _options(provider="p", api_format="xai", model="grok-4.3")
        _, api_format, env_keys = resolve_endpoint(options)

        assert api_format == "xai"
        assert options.api_base == "https://api.x.ai/v1"
        assert env_keys == ()

    def test_an_entry_that_writes_its_own_address_keeps_its_key(self, provider_entry):
        # the key belongs to the host, and the host here is still the entry's
        # whatever wire format the command asks for — `--api_format anthropic`
        # at a gateway entry is a real command
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://gw.example/v1",
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p", api_format="anthropic", model="claude-x")
        _, api_format, env_keys = resolve_endpoint(options)

        assert api_format == "anthropic"
        assert options.api_base == "https://gw.example/v1"
        assert env_keys == ("BBM_TEST_PROVIDER_KEY",)

    def test_a_provider_whose_format_is_kept_still_leads_with_its_key(
        self, provider_entry
    ):
        from book_maker.cli import resolve_endpoint

        provider_entry(api_style="groq", env_key="BBM_TEST_PROVIDER_KEY")
        options = _options(provider="p", api_format="groq", model="llama-3.3-70b")
        _, _, env_keys = resolve_endpoint(options)

        assert env_keys == ("BBM_TEST_PROVIDER_KEY",)

    def test_an_explicit_address_takes_the_key_off_the_entry(self, provider_entry):
        # the entry's key names the entry's host; --api_base moves the run
        # off it, so leading with that variable would send the credential to
        # whatever host was typed
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://gw.example/v1",
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p", api_base="https://other.example/v1")
        _, _, env_keys = resolve_endpoint(options)

        assert env_keys == ()

    def test_two_formats_with_no_address_are_two_hosts(self, provider_entry):
        # openai and anthropic have no address written down here, and
        # treating both as "no address" made them compare equal, so an
        # openai entry asked for the anthropic format kept its key and sent
        # it to api.anthropic.com
        from book_maker.cli import resolve_endpoint

        provider_entry(api_style="openai", env_key="BBM_TEST_PROVIDER_KEY")
        options = _options(provider="p", api_format="anthropic", model="claude-x")
        _, api_format, env_keys = resolve_endpoint(options)

        assert api_format == "anthropic"
        assert env_keys == ()

    def test_a_base_less_entry_on_its_own_format_keeps_its_key(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(api_style="openai", env_key="BBM_TEST_PROVIDER_KEY")
        options = _options(provider="p", model="gpt-5-mini")
        _, _, env_keys = resolve_endpoint(options)

        assert env_keys == ("BBM_TEST_PROVIDER_KEY",)

    def test_the_same_address_retyped_still_belongs_to_the_entry(self, provider_entry):
        # a trailing slash is not a different host
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://gw.example/v1",
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p", api_base="https://gw.example/v1/")
        _, _, env_keys = resolve_endpoint(options)

        assert env_keys == ("BBM_TEST_PROVIDER_KEY",)

    @pytest.mark.parametrize("fmt", ["groq", "xai", "litellm"])
    def test_extra_body_reaches_the_routes_built_on_the_openai_path(
        self, tmp_path, fmt
    ):
        # gating on the format name told a groq run its fields were dropped
        # when the request path it inherits sends them
        src = tmp_path / BOOK.name
        src.write_bytes(BOOK.read_bytes())
        proc = _cli(
            "--book_name",
            str(src),
            "--api_format",
            fmt,
            "--key",
            "sk-test",
            "--model",
            "m",
            "--extra_body",
            '{"a": 1}',
            "--test",
            "--test_num",
            "1",
        )
        output = proc.stdout + proc.stderr
        assert "--extra_body is ignored" not in output, output

    def test_every_format_that_defaults_a_model_can_be_asked_for_one(self):
        # a default model on a format whose class refuses --model would be a
        # command that cannot run
        from book_maker.cli import DEFAULT_MODELS
        from book_maker.translator import LLM_FORMATS

        assert set(DEFAULT_MODELS) <= set(LLM_FORMATS)


class TestProviderPrecedence:
    """`--provider` is shorthand for flags, so it fills gaps and settles nothing.

    A model name that selects a route (`codex`, `orcarouter`) says where the
    request goes; a provider entry only says where it would go otherwise.
    """

    def test_a_provider_alone_supplies_format_base_and_models(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one", "model-two"],
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p")
        models, api_format, env_keys = resolve_endpoint(options)

        assert models == ["model-one", "model-two"]
        assert api_format == "openai"
        assert options.api_base == "https://api.provider.example/v1"
        assert env_keys == ("BBM_TEST_PROVIDER_KEY",)

    def test_a_priced_entry_puts_a_price_table_on_the_options(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one"],
            prices={"model-one": {"input": 1, "output": 2}},
            currency="EUR",
        )
        options = _options(provider="p")
        resolve_endpoint(options)
        assert options.price_table.price_for("model-one") == {"input": 1, "output": 2}
        assert options.price_table.currency == "EUR"

    def test_an_unpriced_entry_leaves_the_meter_on_tokens(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(api_style="openai", base_url="https://api.provider.example/v1")
        options = _options(provider="p", model="m")
        resolve_endpoint(options)
        assert options.price_table is None

    def test_a_model_at_a_provider_keeps_the_providers_endpoint(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one"],
        )
        options = _options(provider="p", model="my-model")
        models, api_format, _ = resolve_endpoint(options)

        assert models == ["my-model"]
        assert api_format == "openai"
        assert options.api_base == "https://api.provider.example/v1"

    def test_model_codex_selects_the_sidecar_at_a_provider(self, provider_entry):
        # `codex` is not a model id at the provider's endpoint: it names the
        # sidecar, so the entry's api_style must not route it to the gateway
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one"],
        )
        from book_maker.legacy_cli import translate_legacy_argv

        legacy = translate_legacy_argv(["--provider", "p", "--model", "codex"])
        assert "--model codex is now --api_format codex" in legacy.notices
        assert "--model" not in legacy.argv  # the alias is gone, not a model id

        options = _options(provider="p", api_format="codex")
        models, api_format, _ = resolve_endpoint(options)

        assert api_format == "codex"
        # the sidecar resolves its own default; the entry's HTTP model is not
        # its to take, or the run would send `model-one` to codex
        assert "model-one" not in models

    def test_model_orcarouter_keeps_its_own_gateway_at_a_provider(self, provider_entry):
        # the OrcaRouter key would otherwise be sent to the provider's base;
        # the route's class supplies the address, so none is filled in here
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p", model="orcarouter")
        models, api_format, env_keys = resolve_endpoint(options)

        assert models == ["orcarouter"]
        assert not options.api_base
        assert api_format == "openai"
        assert env_keys == ("BBM_ORCAROUTER_API_KEY",)

    def test_an_explicit_api_base_still_reaches_the_orcarouter_class(self):
        from book_maker.cli import resolve_endpoint

        options = _options(model="OrcaRouter", api_base="https://mine/v1/")
        models, _, _ = resolve_endpoint(options)

        assert models == ["orcarouter"]
        assert options.api_base == "https://mine/v1"

    def test_an_explicit_api_format_still_outranks_a_route_selecting_model(self):
        from book_maker.cli import resolve_endpoint

        options = _options(model="codex", api_format="openai")
        _, api_format, _ = resolve_endpoint(options)

        assert api_format == "openai"


# --------------------------------------------------------------------------
# The flag audit's fixes (PR #553), on this fork's endpoint surface: the
# route is --api_format / --model <id verbatim>, so a check the branch wrote
# against --model gemini is written here against the format it names.
# --------------------------------------------------------------------------


def test_compact_budget_still_rejects_a_budget_too_small_to_use():
    import argparse

    from book_maker.cli import compact_budget

    with pytest.raises(argparse.ArgumentTypeError):
        compact_budget("499")


def test_a_promptdown_file_with_no_user_message_fails_clearly(tmp_path):
    # the repo's own prompt_md.prompt.md is written in promptdown's table
    # form, which the pinned promptdown does not parse. It used to fall
    # through to `prompt["user"]` and die with a KeyError traceback, after
    # the user had already been told the file loaded
    from book_maker.cli import parse_prompt_arg

    md = tmp_path / "style.prompt.md"
    md.write_text(
        "# Prompt\n\n## Conversation\n\n"
        "| Role | Content |\n|---|---|\n| User | Translate {text} |\n"
    )
    with pytest.raises(ValueError) as excinfo:
        parse_prompt_arg(str(md))
    message = str(excinfo.value)
    assert str(md) in message
    assert "**User:**" in message


def test_a_promptdown_file_in_block_form_still_loads(tmp_path):
    from book_maker.cli import parse_prompt_arg

    md = tmp_path / "style.prompt.md"
    md.write_text(
        "# Prompt\n\n## Developer Message\n\nBe faithful.\n\n"
        "## Conversation\n\n**User:**\nTranslate {text} into {language}\n"
    )
    prompt = parse_prompt_arg(str(md))
    assert "{text}" in prompt["user"]
    assert prompt["system"] == "Be faithful."


def test_an_empty_exclude_translate_tags_excludes_nothing(tmp_path):
    # the README documents --exclude-translate-tags "" as the way to
    # translate code and sup too; the empty string is falsy, so it used to
    # leave the sup,code default in place with no sign anything was ignored
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--exclude-translate-tags", ""
    )
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["exclude_tags"] == []


def test_a_style_and_a_colour_together_say_the_colour_is_lost(tmp_path):
    # --translation_style is the whole declaration block and replaces the
    # colour; it used to win in silence
    proc, _ = _run(
        tmp_path,
        "--test",
        "--test_num",
        "1",
        "--translation_color",
        "red",
        "--translation_style",
        "font-style: italic;",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    flat = " ".join(proc.stdout.split())
    assert "--translation_color" in flat
    assert "ignored" in flat


def test_a_colour_on_its_own_is_not_warned_about(tmp_path):
    proc, _ = _run(tmp_path, "--test", "--test_num", "1", "--translation_color", "red")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--translation_color" not in proc.stdout


def test_a_misspelled_exclude_filelist_name_fails_loud(tmp_path):
    # a typo here used to be silent: the chapter the user meant to skip was
    # translated and paid for. The only-list reached the coverage gate; the
    # exclude-list reached nothing at all
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--exclude_filelist", "titlepage.xhtm"
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--exclude_filelist" in flat
    assert "titlepage.xhtm" in flat
    assert not plan.exists()


def test_a_misspelled_filter_name_fails_the_dry_run_too(tmp_path):
    # Codex review 2 on #553: the dry run returned before the filter gate,
    # so it wrote a plan that did not honor the exclusion and exited 0
    proc, plan = _run(
        tmp_path, "--plan-dry-run", "--exclude_filelist", "titlepage.xhtm"
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--exclude_filelist" in flat and "titlepage.xhtm" in flat
    assert not plan.exists()


def test_a_misspelled_only_filelist_name_fails_loud(tmp_path):
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--only_filelist", "chpater1.xhtml"
    )
    assert proc.returncode == 1
    assert "--only_filelist" in " ".join(proc.stdout.split())
    assert not plan.exists()


def test_correctly_spelled_file_filters_still_plan(tmp_path):
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--exclude_filelist", "titlepage.xhtml"
    )
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE, proc.stdout + proc.stderr
    assert plan.exists()


def test_a_misspelled_exclude_name_fails_loud_in_tag_mode_too(tmp_path):
    # the gate lived inside the plan build, so a tag-mode run translated and
    # paid for the document the user meant to skip
    proc, _ = _run(
        tmp_path,
        "--plan-classify",
        "none",
        "--exclude_filelist",
        "titlepage.xhtm",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--exclude_filelist" in flat
    assert "titlepage.xhtm" in flat
    assert not (tmp_path / f"{BOOK.stem}_bilingual.epub").exists()


def test_the_file_filter_gate_runs_before_any_model_setup(tmp_path):
    # even in plan mode it ran after preflight, so a codex sidecar had
    # already booted and printed its login line before the typo was caught
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "codex",
        "--plan-classify",
        "agent",
        "--exclude_filelist",
        "titlepage.xhtm",
    )
    assert proc.returncode == 1
    assert "--exclude_filelist" in " ".join(proc.stdout.split())
    assert "Codex:" not in proc.stdout


def test_batch_is_refused_on_every_route_without_the_batch_api(tmp_path):
    # the loader calls batch_init / add_to_batch_translate_queue /
    # is_completed_batch on the translator, so a route that has none of them
    # used to accept the flag and die on AttributeError partway through
    from book_maker.translator import FORMAT_DICT

    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    for fmt, cls in FORMAT_DICT.items():
        if cls.SUPPORTS_BATCH_API:
            continue
        proc = _cli(
            "--book_name",
            str(src),
            "--api_format",
            fmt,
            "--key",
            "sk-test",
            "--model",
            "m",
            "--batch",
            "--test",
            "--test_num",
            "1",
        )
        output = " ".join((proc.stdout + proc.stderr).split())
        assert proc.returncode != 0, fmt
        assert f"the {fmt} format does not have" in output, output


def test_batch_is_refused_on_the_codex_format(tmp_path):
    # codex has no Batch API; the run used to die partway through with
    # AttributeError: batch_init, after plan quota had already been spent
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--batch")
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--batch" in flat
    assert "Codex:" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_batch_use_is_refused_on_the_codex_format(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--batch-use")
    assert proc.returncode == 1
    assert "--batch" in " ".join(proc.stdout.split())


def test_a_resumed_codex_run_says_continuity_restarts(tmp_path):
    # the thread is the only context this route has and it dies with the
    # process; <book>_handoff.md is written but never read back
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--resume")
    assert "new thread" in " ".join(proc.stdout.split())


def test_a_codex_run_without_resume_says_nothing_about_threads(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--batch")
    assert "new thread" not in proc.stdout


def test_session_context_is_refused_on_a_format_that_has_none(tmp_path):
    # a machine-translation format keeps no history at all; --use_context
    # session was accepted and silently meant window
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "deepl",
        "--use_context",
        "session",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--use_context session" in flat
    assert "deepl" in flat


def test_bare_window_context_is_not_refused_anywhere(tmp_path):
    # window mode is what those formats do have; only session is refused
    proc, _ = _run(tmp_path, "--use_context", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--use_context session" not in proc.stdout


def test_session_context_is_accepted_on_the_formats_that_implement_it():
    from book_maker.translator import FORMAT_DICT

    assert FORMAT_DICT["openai"].SUPPORTS_SESSION_CONTEXT
    assert FORMAT_DICT["anthropic"].SUPPORTS_SESSION_CONTEXT
    assert FORMAT_DICT["codex"].SUPPORTS_SESSION_CONTEXT
    # (google is replaced by the hermetic stub, which declares both)
    assert not FORMAT_DICT["deepl"].SUPPORTS_SESSION_CONTEXT
    assert not FORMAT_DICT["caiyun"].SUPPORTS_SESSION_CONTEXT


def test_parallel_workers_with_context_is_refused_where_it_breaks(tmp_path):
    # a format that keeps no re-sendable window has nothing to clone, and
    # the run died reading a context attribute it never set — after the
    # chapters had already been dispatched
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "deepl",
        "--use_context",
        "--parallel-workers",
        "2",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "--use_context" in flat


def test_parallel_workers_without_context_is_left_alone(tmp_path):
    # only the pairing is refused; parallel on its own is untouched
    proc, _ = _run(tmp_path, "--parallel-workers", "2", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_formats_that_carry_chapter_context_are_not_refused():
    from book_maker.translator import FORMAT_DICT

    assert FORMAT_DICT["openai"].SUPPORTS_PARALLEL_CONTEXT
    assert FORMAT_DICT["anthropic"].SUPPORTS_PARALLEL_CONTEXT
    assert not FORMAT_DICT["deepl"].SUPPORTS_PARALLEL_CONTEXT
    assert not FORMAT_DICT["caiyun"].SUPPORTS_PARALLEL_CONTEXT


def test_the_old_mode_name_still_works_and_says_it_moved(tmp_path):
    # scripts written before the rename keep running
    proc, plan = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "--plan-classify most is now --plan-classify all" in " ".join(
        proc.stdout.split()
    )


def test_the_old_mode_name_is_not_advertised():
    proc = _cli("--help")
    assert "{auto,none,all,model,agent}" in " ".join(proc.stdout.split())
    assert "'most'" not in proc.stdout


def test_a_zero_compact_budget_is_refused_like_any_other_too_small_one():
    # `0` used to mean "size it from the model's own context window". OpenAI's
    # own endpoint never reported one, so the answer was a notice and a
    # fallback on two routes and a dead run on the third; the flag now takes
    # a number and nothing else.
    import argparse

    from book_maker.cli import compact_budget

    with pytest.raises(argparse.ArgumentTypeError):
        compact_budget("0")


def test_a_named_compact_budget_is_never_refused(tmp_path):
    proc, _ = _run(tmp_path, "--context-compact-at", "3000", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_agent_handoff_makes_no_model_verification_call(tmp_path, monkeypatch):
    """`--plan-classify agent` writes a plan and stops. It must pay nothing.

    Model verification used to happen eagerly in `set_model_list`, at CLI
    setup, so a run that never translated a word still bought a startup
    round trip. Any use of the client at all fails this test loudly.
    """
    from book_maker.translator import FORMAT_DICT, chatgptapi_translator

    # tests/hermetic swaps the openai route for an offline stand-in, which is
    # right for every other CLI contract test and useless here: the route
    # check being tested lives in the real translator.
    monkeypatch.setitem(FORMAT_DICT, "openai", chatgptapi_translator.ChatGPTAPI)

    class NetworkUsed(BaseException):
        """A BaseException on purpose: the route check gives a model the
        benefit of the doubt on any `Exception`, so an ordinary one raised
        here would be swallowed and this test would pass without proving
        anything."""

    class NoNetwork:
        def __getattr__(self, name):
            raise NetworkUsed(f"the run used the network: openai_client.{name}")

    monkeypatch.setattr(chatgptapi_translator, "OpenAI", lambda *a, **k: NoNetwork())
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_book.py",
            "--book_name",
            str(src),
            "--api_format",
            "openai",
            "--model",
            "a-model-nothing-serves",
            "--key",
            "not-a-real-key",
            "--plan-classify",
            "agent",
        ],
    )

    with pytest.raises(SystemExit) as stopped:
        main()

    assert stopped.value.code == PLAN_HANDOFF_EXIT_CODE
    assert (tmp_path / f"{src.stem}_plan.json").exists()


def test_the_dry_run_builds_no_translator_at_all(tmp_path, monkeypatch):
    """`--plan-dry-run` writes a plan from the file. Nothing is asked of an
    endpoint, so nothing may be paid for — not even a client."""
    from book_maker.translator import chatgptapi_translator

    def refuse(*args, **kwargs):
        raise BaseException("a dry run built an API client")

    monkeypatch.setattr(chatgptapi_translator, "OpenAI", refuse)
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    monkeypatch.setattr(
        sys,
        "argv",
        ["make_book.py", "--book_name", str(src), "--plan-dry-run"],
    )

    main()

    assert (tmp_path / f"{src.stem}_plan.json").exists()


class TestRequestExtrasFlags:
    """`--extra_body` / `--extra_headers`: refused early, or carried."""

    def _cli_extras(self, tmp_path, *args):
        src = tmp_path / BOOK.name
        src.write_bytes(BOOK.read_bytes())
        return _cli(
            "--book_name",
            str(src),
            "--key",
            "sk-test",
            "--api_format",
            "openai",
            "--model",
            "m",
            "--test",
            "--test_num",
            "1",
            *args,
        )

    def test_invalid_json_names_the_flag_it_came_from(self, tmp_path):
        # with both flags taking JSON, "invalid JSON" alone leaves the reader
        # checking the wrong one
        proc = self._cli_extras(tmp_path, "--extra_headers", "{nope}")
        output = " ".join((proc.stdout + proc.stderr).split())

        assert proc.returncode != 0
        assert "invalid JSON in --extra_headers" in output

    def test_a_json_array_is_refused_before_a_paid_request(self, tmp_path):
        # the SDK would accept it and the endpoint would reject it, one
        # billed request later
        proc = self._cli_extras(tmp_path, "--extra_body", '["a"]')
        output = " ".join((proc.stdout + proc.stderr).split())

        assert proc.returncode != 0
        assert "--extra_body must be a JSON object, not list" in output

    def test_a_non_string_header_value_is_refused_here(self, tmp_path):
        # httpx raises on one deep inside the first request otherwise
        proc = self._cli_extras(tmp_path, "--extra_headers", '{"X-N": 1}')
        output = " ".join((proc.stdout + proc.stderr).split())

        assert proc.returncode != 0
        assert "--extra_headers values must all be strings" in output

    def test_a_route_that_builds_no_request_says_so_and_runs_on(self, tmp_path):
        # google translates through a fixed engine: there is no request body
        # for these to join, and dropping them silently is what this replaces
        src = tmp_path / BOOK.name
        src.write_bytes(BOOK.read_bytes())
        proc = _cli(
            "--book_name",
            str(src),
            "--api_format",
            "google",
            "--extra_body",
            '{"a": 1}',
            "--extra_headers",
            '{"b": "c"}',
            "--test",
            "--test_num",
            "1",
        )
        output = " ".join((proc.stdout + proc.stderr).split())

        assert (
            "--extra_body and --extra_headers are ignored by the google route" in output
        )

    def test_a_header_value_is_never_printed(self, tmp_path):
        # a header is where a credential goes; echoing the value would put it
        # in every log and CI artifact the run touches
        proc = self._cli_extras(
            tmp_path, "--extra_headers", '{"Authorization": "Bearer sk-SECRET"}'
        )
        output = proc.stdout + proc.stderr

        assert "sk-SECRET" not in output
        assert "Authorization" in output  # the name still says what was sent

    def test_a_body_field_repeating_the_key_is_masked_in_the_echo(self, tmp_path):
        # a gateway that wants the key in the body gets it repeated there;
        # the body echo goes through redact() like every other sink
        src = tmp_path / BOOK.name
        src.write_bytes(BOOK.read_bytes())
        proc = _cli(
            "--book_name",
            str(src),
            "--key",
            "sk-live-0123456789",
            "--api_format",
            "openai",
            "--model",
            "m",
            "--test",
            "--test_num",
            "1",
            "--extra_body",
            '{"api_key": "sk-live-0123456789", "top_p": 0.9}',
        )
        output = proc.stdout + proc.stderr

        assert "sk-live-0123456789" not in output
        assert "api_key" in output and "top_p" in output

    def test_both_flags_are_echoed_so_the_run_records_what_it_sent(self, tmp_path):
        proc = self._cli_extras(
            tmp_path,
            "--extra_body",
            '{"enable_thinking": false}',
            "--extra_headers",
            '{"X-Title": "bbm"}',
        )
        output = " ".join((proc.stdout + proc.stderr).split())

        assert "enable_thinking" in output
        assert "X-Title" in output


# --------------------------------------------- the whole path, read back


def test_the_written_epub_carries_each_translation_beside_its_source(tmp_path):
    """The one check the CLI tests were missing: not the exit code, not the
    plan, but the book. Run the real command against the offline stand-in,
    then open what it wrote and find each translation as the paragraph
    right after its original, with the original's class, in a content
    document and not only in the nav."""
    import zipfile

    from bs4 import BeautifulSoup

    proc, _ = _run(tmp_path, "--test", "--test_num", "2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = tmp_path / "animal_farm_bilingual.epub"
    assert output.exists(), proc.stdout + proc.stderr

    with zipfile.ZipFile(output) as archive:
        documents = {
            name: archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith((".html", ".xhtml")) and "nav" not in name
        }

    def words(node):
        return " ".join(node.get_text(" ").split())

    placed = 0
    for name, text in documents.items():
        for paragraph in BeautifulSoup(text, "html.parser").find_all("p"):
            translation = words(paragraph)
            if not translation.startswith("[offline]"):
                continue
            source = paragraph.find_previous_sibling("p")
            assert source is not None, f"{name}: a translation with nothing before it"
            assert words(source) == translation[len("[offline]") :], name
            assert source.get("class") == paragraph.get("class"), name
            placed += 1
    assert placed == 2, f"{placed} translations placed, 2 requested"


# --------------------------------------------------------------------------
# --max-batch-units, and the session-mode grouping default
# --------------------------------------------------------------------------


def test_a_batch_of_zero_units_is_refused():
    # a request has to carry something; `--accumulated_num 1` is the flag
    # that turns grouping off, and this one must not become a second spelling
    import argparse

    from book_maker.cli import batch_unit_cap

    with pytest.raises(argparse.ArgumentTypeError):
        batch_unit_cap("0")


def test_a_zero_batch_units_run_stops_at_the_parser(tmp_path):
    proc, _ = _run(tmp_path, "--max-batch-units", "0", "--plan-dry-run")
    # argparse's own refusal: exit 2, one line, nothing translated
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "--max-batch-units" in proc.stderr
    assert not list(tmp_path.glob("*_plan.json"))
    assert "Traceback" not in proc.stdout + proc.stderr


def test_a_negative_batch_units_is_refused_the_same_way(tmp_path):
    proc, _ = _run(tmp_path, "--max-batch-units", "-4", "--plan-dry-run")
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_batch_units_is_recorded_in_the_plan(tmp_path):
    proc, plan = _run(tmp_path, "--max-batch-units", "4", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["batch_units"] == 4


# ------------------------------------- --batch_units, the old spelling


def test_the_old_spelling_still_reaches_the_same_dest():
    from book_maker.cli import parse_args

    old = parse_args(["--book_name", "b.epub", "--batch_units", "4"])
    new = parse_args(["--book_name", "b.epub", "--max-batch-units", "4"])
    assert old.batch_units == new.batch_units == 4
    # only the old one records that it was the spelling typed
    assert old.batch_units_deprecated_flag == "--batch_units"
    assert new.batch_units_deprecated_flag is None


def test_the_old_spelling_plans_exactly_as_the_new_one_does(tmp_path):
    proc, plan = _run(tmp_path, "--batch_units", "4", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["batch_units"] == 4


def test_the_old_spelling_earns_one_notice_naming_the_new_one(tmp_path):
    proc, _ = _run(tmp_path, "--batch_units", "4", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    flat = " ".join(proc.stdout.split())
    assert flat.count("--batch_units is now --max-batch-units") == 1


def test_the_new_spelling_earns_no_notice(tmp_path):
    proc, _ = _run(tmp_path, "--max-batch-units", "4", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "deprecated" not in proc.stdout


def test_help_advertises_only_the_new_spelling():
    proc = _cli("--help")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--max-batch-units" in proc.stdout
    assert "--batch_units" not in proc.stdout


def test_batch_units_defaults_to_the_measured_cap(tmp_path):
    # half the level the 260905 fault-emergence sweep measured faults at
    from book_maker.loader.plan import GENERAL_GROUP_MAX_UNITS

    proc, plan = _run(tmp_path, "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["batch_units"] == GENERAL_GROUP_MAX_UNITS


def test_an_untyped_accumulated_num_reaches_the_parser_as_none():
    # the explicitness is the whole mechanism: plan mode defaults the budget
    # by context mode only when the flag was not typed, and `1` has to stay
    # distinguishable from silence
    from book_maker.cli import parse_args

    assert parse_args(["--book_name", "b.epub"]).accumulated_num is None
    assert (
        parse_args(["--book_name", "b.epub", "--accumulated_num", "1"]).accumulated_num
        == 1
    )


def test_a_session_dry_run_previews_the_default_budget(tmp_path):
    # --plan-dry-run must group the way the run will: session mode defaults
    # the token budget, so the preview's plan carries it too
    from book_maker.loader.plan import session_token_budget

    proc, plan = _run(tmp_path, "--plan-dry-run", "--use_context", "session")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # a dry run has no translator to measure a prompt overhead against, so
    # the preview carries the floor
    assert json.loads(plan.read_text())["token_budget"] == session_token_budget(None)


def test_a_plain_dry_run_previews_the_derived_budget_and_names_both_routes(tmp_path):
    # 260906: the derived default is no longer session-only, so a dry run
    # without --use_context session previews one too. It cannot know the
    # endpoint's schema verdict — there is no endpoint — so it groups at the
    # schema-verified derivation and says both numbers out loud.
    from book_maker.loader.plan import derived_token_budget

    proc, plan = _run(tmp_path, "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["token_budget"] == derived_token_budget(
        None, "schema"
    )
    # rich wraps the line to the terminal width
    out = " ".join(proc.stdout.split())
    assert "plan grouping: budget 1200 tokens per request" in out
    assert "800 below strict decoding" in out


def test_a_google_plan_run_derives_and_narrates_the_substrict_budget(tmp_path):
    # the parity half: --api_format google holds no schema at all, so the
    # real run takes the sub-strict derivation — and the number it lands on
    # is one of the two the dry run above printed. Nothing was typed, so the
    # budget in the run's own batches line is the derived one.
    from book_maker.loader.plan import derived_token_budget

    proc, _ = _run(tmp_path, "--plan-classify", "all", "--test", "--test_num", "2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    budget = derived_token_budget(None, "substrict")
    out = " ".join(proc.stdout.split())
    assert (
        f"plan grouping: budget {budget} tokens per request "
        f"(endpoint below strict decoding" in out
    )
    # the partition stays endpoint-independent, so the plan's own line
    # carries the schema-verified budget; the halving happens per request
    assert f"/ {derived_token_budget(None, 'schema')} tokens per request)" in out
    # and it actually grouped: far fewer requests than units
    units, requests = re.search(
        r"batches: (\d+) unit\(s\) in (\d+) request\(s\)", out
    ).groups()
    assert int(requests) < int(units)


def test_a_typed_budget_narrates_nothing(tmp_path):
    # the operator's own number needs no explaining; the line is only for
    # the derived default
    proc, _ = _run(
        tmp_path,
        "--plan-classify",
        "all",
        "--accumulated_num",
        "900",
        "--test",
        "--test_num",
        "2",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "plan grouping: budget" not in " ".join(proc.stdout.split())


def test_an_explicit_one_keeps_the_session_dry_run_ungrouped(tmp_path):
    proc, plan = _run(
        tmp_path,
        "--plan-dry-run",
        "--use_context",
        "session",
        "--accumulated_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # 0, the off switch: None would preview the short-run grouping the run
    # won't do (the zero-budget behavior itself is pinned at the
    # assign_batches level: a 0 budget groups nothing at all)
    assert json.loads(plan.read_text())["token_budget"] == 0


# --------------------------------------------------------------------------
# --poetry-group-size: deprecated, still honoured
# --------------------------------------------------------------------------


def test_poetry_group_size_still_shapes_the_plan(tmp_path):
    # deprecated is not removed: the flag reaches the plan exactly as before
    proc, plan = _run(tmp_path, "--poetry-group-size", "5", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["poetry_group_size"] == 5


def test_poetry_group_size_warns_and_points_at_the_units_cap(tmp_path):
    proc, _ = _run(tmp_path, "--poetry-group-size", "5", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    flat = " ".join(proc.stdout.split())
    assert flat.count("--poetry-group-size:") == 1
    assert "general grouping" in flat
    assert "--max-batch-units" in flat


def test_an_untyped_poetry_group_size_says_nothing(tmp_path):
    proc, plan = _run(tmp_path, "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--poetry-group-size" not in proc.stdout
    # and the default is unchanged
    assert json.loads(plan.read_text())["poetry_group_size"] == 8


# --------------------------------------------------------------------------
# the compaction budget is pinned, and the preview says the same thing
# --------------------------------------------------------------------------


def _compact_line(text):
    """The compaction sentence, however rich wrapped it.

    `rich.print` wraps at the 80 columns it assumes for a pipe, so the
    sentence can arrive split across lines. What is being pinned is the
    wording, not the wrapping.
    """
    import re

    flat = " ".join(text.split())
    found = re.search(r"session: compacting at [^)]*\)", flat)
    return found.group(0) if found else None


def test_the_session_dry_run_previews_the_pinned_budget(tmp_path):
    from book_maker.session_context import DEFAULT_COMPACT_BUDGET

    proc, _ = _run(tmp_path, "--plan-dry-run", "--use_context", "session")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _compact_line(proc.stdout) == (
        f"session: compacting at {DEFAULT_COMPACT_BUDGET} estimated tokens "
        f"(the default; --context-compact-at overrides)"
    )


def test_the_session_dry_run_previews_an_explicit_budget(tmp_path):
    proc, _ = _run(
        tmp_path,
        "--plan-dry-run",
        "--use_context",
        "session",
        "--context-compact-at",
        "2000",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _compact_line(proc.stdout) == (
        "session: compacting at 2000 estimated tokens (--context-compact-at)"
    )


def test_a_windowed_dry_run_previews_no_compaction_at_all(tmp_path):
    proc, _ = _run(tmp_path, "--plan-dry-run", "--use_context")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _compact_line(proc.stdout) is None


def test_the_preview_and_the_run_print_the_same_line(tmp_path):
    # the preview's whole job is to say what the run will do; these two
    # lines come from one function so they cannot drift
    from book_maker.session_context import DEFAULT_COMPACT_BUDGET

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    preview, _ = _run(tmp_path / "a", "--plan-dry-run", "--use_context", "session")
    real, _ = _run(
        tmp_path / "b",
        "--use_context",
        "session",
        "--test",
        "--test_num",
        "1",
    )
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert real.returncode == 0, real.stdout + real.stderr
    assert _compact_line(preview.stdout) == _compact_line(real.stdout)
    assert str(DEFAULT_COMPACT_BUDGET) in _compact_line(real.stdout)


def test_the_run_narrates_an_explicit_budget_as_the_flag(tmp_path):
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "session",
        "--context-compact-at",
        "2000",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _compact_line(proc.stdout) == (
        "session: compacting at 2000 estimated tokens (--context-compact-at)"
    )


def test_a_run_with_no_session_narrates_no_compaction(tmp_path):
    proc, _ = _run(tmp_path, "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _compact_line(proc.stdout) is None


def test_no_code_path_reaches_for_the_deleted_derivation():
    import book_maker.session_context as session_context
    from book_maker.loader import epub_loader

    assert not hasattr(session_context, "derived_compact_budget")
    assert not hasattr(session_context, "DERIVED_COMPACT_FLOOR")
    assert not hasattr(session_context, "DERIVED_COMPACT_CEILING")
    assert not hasattr(epub_loader, "derived_compact_budget")
    assert not hasattr(epub_loader.EPUBBookLoader, "_derive_session_compact_budget")


# --------------------------------------------------------------------------
# a finished run says where the book landed
# --------------------------------------------------------------------------


def test_a_finished_epub_run_ends_with_the_absolute_path(tmp_path):
    proc, _ = _run(tmp_path, "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = tmp_path / "animal_farm_bilingual.epub"
    assert output.exists()
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines[-1].strip() == f"Bilingual book saved: {output.resolve()}"


def test_the_saved_line_is_printed_exactly_once(tmp_path):
    proc, _ = _run(tmp_path, "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("Bilingual book saved:") == 1


def test_a_long_path_arrives_on_one_line(tmp_path):
    # rich wraps at the 80 columns it assumes for a pipe; a path broken in
    # half is exactly what this line exists to prevent, so it is not rich's
    deep = tmp_path / ("d" * 40) / ("e" * 40)
    deep.mkdir(parents=True)
    proc, _ = _run(deep, "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = deep / "animal_farm_bilingual.epub"
    assert len(str(output)) > 80
    assert f"Bilingual book saved: {output.resolve()}\n" in proc.stdout


def test_a_failed_run_says_nothing_about_a_saved_book(tmp_path):
    # no book at that path at all: the run cannot get as far as writing one
    proc = _cli(
        "--book_name",
        str(tmp_path / "not_a_book.epub"),
        "--api_format",
        "google",
    )
    assert proc.returncode != 0
    assert "Bilingual book saved:" not in proc.stdout


def test_a_txt_run_names_the_txt_it_wrote(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("Hello there.\n\nSecond paragraph.\n", encoding="utf-8")
    proc = _cli("--book_name", str(source), "--api_format", "google")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = tmp_path / "book_bilingual.txt"
    assert output.exists(), proc.stdout
    assert f"Bilingual book saved: {output.resolve()}" in proc.stdout
    assert proc.stdout.count("Bilingual book saved:") == 1


def test_a_srt_run_names_the_srt_it_wrote(tmp_path):
    source = tmp_path / "subs.srt"
    source.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello there.\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nAnd again.\n",
        encoding="utf-8",
    )
    proc = _cli("--book_name", str(source), "--api_format", "google")
    assert proc.returncode == 0, proc.stdout + proc.stderr

    output = tmp_path / "subs_bilingual.srt"
    assert output.exists(), proc.stdout
    assert f"Bilingual book saved: {output.resolve()}" in proc.stdout
