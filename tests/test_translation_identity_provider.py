"""With `--provider`, the translation identity is the endpoint the run calls.

PIN (lead + astra consult 260928, packet B, scratchpad astra/answer.md §2;
dated record in docs/ by the lead): `option_identity` used to hash the three
provider files by path and contents -- the shipped example,
`~/.bbm/providers.json` and `./bbm_providers.json` with the current
directory in the path string. So an edit to an unrelated entry, a
reformatting of the file, or the same rerun from another directory changed
the fingerprint: a finished bundle was translated and paid for again, an
interrupted one refused to resume with SETTINGS_CHANGED. The identity now
holds a digest of what the entry makes the run send: the format, the address
where the route uses one, the models after `--model`/`--model_list`, and the
key variable names that would be read (never their values). Resolved on a
copy of the options, through the CLI's own `resolve_endpoint`, so the sparse
identity is not polluted.
"""

import argparse
import json

import pytest

from book_maker import provider_loader
from book_maker.pipeline.errors import PipelineError
from book_maker.pipeline.translate import option_identity, parse_bbm_options

ENTRY = {
    "api_style": "openai",
    "base_url": "https://api.example.test/v1",
    "default_models": ["model-a", "model-b"],
    "env_key": "BBM_EXAMPLE_API_KEY",
}
OTHER = {
    "api_style": "openai",
    "base_url": "https://other.example.test/v1",
    "default_models": ["other-model"],
    "env_key": "BBM_OTHER_API_KEY",
}


@pytest.fixture
def configs(tmp_path, monkeypatch):
    """The three provider files, redirected into tmp_path, initially absent."""
    global_file = tmp_path / "home" / ".bbm" / "providers.json"
    global_file.parent.mkdir(parents=True)
    monkeypatch.setattr(provider_loader, "GLOBAL_CONFIG_PATH", global_file)
    example_file = tmp_path / "shipped" / "bbm_providers.example.json"
    example_file.parent.mkdir(parents=True)
    monkeypatch.setattr(provider_loader, "EXAMPLE_CONFIG_PATH", example_file)
    local_dir = tmp_path / "project"
    local_dir.mkdir()
    monkeypatch.chdir(local_dir)
    for name in ("BBM_EXAMPLE_API_KEY", "BBM_RENAMED_API_KEY", "BBM_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    class Files:
        local = local_dir / "bbm_providers.json"
        home = global_file

        @staticmethod
        def write(path, providers, indent=None):
            path.write_text(
                json.dumps({"providers": providers}, indent=indent), encoding="utf-8"
            )

    return Files


def _identity(*flags):
    return option_identity(
        parse_bbm_options(["--provider", "mine", "--language", "ja", *flags])
    )


def _with(**changes):
    return {**ENTRY, **changes}


def test_editing_another_entry_leaves_the_identity_alone(configs):
    configs.write(configs.local, {"mine": ENTRY, "other": OTHER})
    before = _identity()
    configs.write(
        configs.local, {"mine": ENTRY, "other": {**OTHER, "default_models": ["x"]}}
    )
    assert _identity() == before


def test_reformatting_the_file_leaves_the_identity_alone(configs):
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity()
    configs.write(configs.local, {"mine": ENTRY}, indent=4)
    assert _identity() == before


def test_the_selected_entry_models_change_the_identity(configs):
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity()
    configs.write(configs.local, {"mine": _with(default_models=["model-b"])})
    assert _identity() != before


def test_the_model_order_changes_the_identity(configs):
    # the rotation starts with the first one
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity()
    configs.write(configs.local, {"mine": _with(default_models=["model-b", "model-a"])})
    assert _identity() != before


def test_the_selected_entry_address_changes_the_identity(configs):
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity()
    configs.write(configs.local, {"mine": _with(base_url="https://moved.test/v1")})
    assert _identity() != before


def test_the_selected_entry_format_changes_the_identity(configs):
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity()
    configs.write(configs.local, {"mine": _with(api_style="anthropic")})
    assert _identity() != before


def test_prices_and_sidecar_fields_do_not_change_the_identity(configs):
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity()
    configs.write(
        configs.local,
        {
            "mine": _with(
                img_model="vision-1",
                img_base_url="https://vision.test/v1",
                classify_model="jev",
                prices={"model-a": {"input": 1, "output": 2}},
                currency="EUR",
            )
        },
    )
    assert _identity() == before


def test_the_current_directory_is_not_part_of_the_identity(
    configs, tmp_path, monkeypatch
):
    # the entry lives in the home file; neither directory has a local one
    configs.write(configs.home, {"mine": ENTRY})
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    here = _identity()
    monkeypatch.chdir(second)
    assert _identity() == here


def test_moving_the_entry_between_files_is_the_same_endpoint(configs):
    configs.write(configs.home, {"mine": ENTRY})
    before = _identity()
    configs.home.unlink()
    configs.write(configs.local, {"mine": ENTRY})
    assert _identity() == before


def test_the_key_variable_name_counts_and_its_value_does_not(configs, monkeypatch):
    configs.write(configs.local, {"mine": ENTRY})
    monkeypatch.setenv("BBM_EXAMPLE_API_KEY", "sk-first-secret-value")
    before = _identity()
    monkeypatch.setenv("BBM_EXAMPLE_API_KEY", "sk-second-secret-value")
    after = _identity()
    assert after == before
    assert "secret-value" not in json.dumps(after)
    configs.write(configs.local, {"mine": _with(env_key="BBM_RENAMED_API_KEY")})
    assert _identity() != before


def test_an_overridden_default_model_is_not_in_the_identity(configs):
    # --model outranks the entry's list, which the run then never sends
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity("--model", "chosen")
    configs.write(configs.local, {"mine": _with(default_models=["unused"])})
    assert _identity("--model", "chosen") == before
    # and without --model the same edit is a different run
    configs.write(configs.local, {"mine": ENTRY})
    plain = _identity()
    configs.write(configs.local, {"mine": _with(default_models=["unused"])})
    assert _identity() != plain


def test_an_explicit_key_makes_the_key_variable_irrelevant(configs):
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity("--key", "sk-typed")
    configs.write(configs.local, {"mine": _with(env_key="BBM_RENAMED_API_KEY")})
    assert _identity("--key", "sk-typed") == before


def test_another_address_does_not_read_the_entry_key_variable(configs, capsys):
    # --api_base moves the run off the entry's host, and the entry's key
    # does not follow it there (apply_provider's endpoint binding)
    configs.write(configs.local, {"mine": ENTRY})
    flags = ("--api_base", "https://elsewhere.test/v1")
    before = _identity(*flags)
    configs.write(configs.local, {"mine": _with(env_key="BBM_RENAMED_API_KEY")})
    assert _identity(*flags) == before
    # the run says this itself; the fingerprint does not say it again
    assert capsys.readouterr().out == ""


def test_the_codex_route_ignores_the_entry_address_and_key(configs):
    # the sidecar takes neither the entry's base nor its key, and not its
    # models either: a model comes only from --model there
    configs.write(configs.local, {"mine": ENTRY})
    before = _identity("--api_format", "codex")
    configs.write(
        configs.local,
        {
            "mine": _with(
                base_url="https://moved.test/v1",
                env_key="BBM_RENAMED_API_KEY",
                default_models=["unused"],
            )
        },
    )
    assert _identity("--api_format", "codex") == before


def test_resolution_does_not_leak_into_the_options(configs):
    configs.write(configs.local, {"mine": ENTRY})
    options = parse_bbm_options(["--provider", "mine", "--language", "ja"])
    snapshot = dict(vars(options))
    first = option_identity(options)
    assert vars(options) == snapshot
    assert not hasattr(options, "provider_route")
    assert option_identity(options) == first
    # a copy of the parsed options is the same run
    assert option_identity(argparse.Namespace(**snapshot)) == first
    names = {name for name, _ in first}
    assert "provider_route" not in names and "price_table" not in names
    assert "api_base" not in names and "model_list" not in names


def test_an_unknown_provider_is_refused_as_the_run_would(configs):
    configs.write(configs.local, {"other": OTHER})
    with pytest.raises(PipelineError, match="--provider mine has no entry"):
        _identity()


def test_without_a_provider_nothing_is_resolved(configs):
    options = parse_bbm_options(["--model", "gpt-5.6-luna", "--language", "ja"])
    names = {name for name, _ in option_identity(options)}
    assert names == {"model", "language"}
