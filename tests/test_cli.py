from book_maker.cli import get_book_type, main


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"


import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
# tests/hermetic/sitecustomize.py swaps the `google` translator for an
# offline one at interpreter startup. These are CLI *contract* tests — flag
# wiring, mode selection, what gets written — and routing them through a
# public translation endpoint made them fail on proxy errors and impossible
# to run offline. Live provider calls belong to tests/test_integration.py.
HERMETIC = Path(__file__).resolve().parent / "hermetic"


def _env():
    env = dict(os.environ)
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
    proc = _cli("--book_name", str(src), "--model", "google", *args)
    return proc, src.parent / (src.stem + "_plan.json")


def test_plan_classify_implies_plan_mode(tmp_path):
    # any classification choice is a choice to have a plan; no second flag
    # is needed to enter plan mode
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == 0
    assert plan.exists()
    assert "Paste the block below" in proc.stdout


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


def test_most_mode_translates_without_asking_or_writing_a_plan(tmp_path):
    # 'most' is the deliberate translate-everything entry: no questions, so
    # no plan file to answer them in, and no agent stop
    proc, plan = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "Paste the block below" not in proc.stdout


def test_most_mode_ignores_an_existing_plan(tmp_path):
    # half-loading an earlier run's skips would make "most" quietly mean
    # "most, except whatever something else decided"
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert plan.exists()
    proc, _ = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignores the existing plan" in " ".join(proc.stdout.split())


def test_explicit_tag_list_loses_to_the_classify_flag(tmp_path):
    proc, plan = _run(tmp_path, "--plan-classify", "agent", "--translate-tags", "div,p")
    assert proc.returncode == 0
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
    assert proc.returncode == 0
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
        "--book_name", str(src), "--model", "google", "--plan-classify", "agent"
    )
    assert proc.returncode == 1
    assert "epub-only" in proc.stdout


def test_agent_mode_rejects_a_classifier_model(tmp_path):
    proc, _ = _run(
        tmp_path, "--plan-classify", "agent", "--plan-classify-model", "gpt-4o"
    )
    assert proc.returncode == 1
    assert "cannot be combined" in proc.stdout


def test_most_mode_rejects_a_classifier_model(tmp_path):
    # 'most' explicitly skips classification; naming a classifier alongside
    # it is a contradiction, not a preference to resolve silently
    proc, _ = _run(
        tmp_path, "--plan-classify", "most", "--plan-classify-model", "gpt-4o"
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
    # google translator has no structured_json, and a classifier that cannot
    # run must block rather than degrade into translating undecided rows
    assert proc.returncode == 1
    assert "no structured-output support" in " ".join(proc.stdout.split())
    # and it must say what to do instead, not just what failed
    assert "--plan-classify agent" in " ".join(proc.stdout.split())


def test_model_list_with_a_preset_model_fails_loud(tmp_path):
    # --model chatgptapi runs a hardcoded GPT-3.5 discovery and ignores
    # --model_list entirely; silently dropping the user's explicit model
    # choice cost a live run — refuse the combination instead
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "chatgptapi",
        "--openai_key",
        "sk-test",
        "--model_list",
        "some-model",
    )
    assert proc.returncode == 1
    assert "--model_list" in proc.stdout
    assert "openai" in proc.stdout


def test_quiet_flag_is_accepted(tmp_path):
    proc, plan = _run(tmp_path, "--plan-dry-run", "--quiet")
    assert proc.returncode == 0
    assert plan.exists()


def test_kobo_mode_does_not_require_book_name(tmp_path, monkeypatch):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    fake_obok = ModuleType("book_maker.obok")
    fake_obok.cli_main = lambda device_path: str(src)
    monkeypatch.setitem(sys.modules, "book_maker.obok", fake_obok)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_book.py",
            "--book_from",
            "kobo",
            "--device_path",
            "/mounted/kobo",
            "--plan-dry-run",
        ],
    )

    main()

    assert (tmp_path / f"{src.stem}_plan.json").exists()


def test_groq_model_list_does_not_use_openai_validation(monkeypatch):
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI
    from book_maker.translator.groq_translator import GroqClient

    def fail_if_called(*args, **kwargs):
        raise AssertionError("OpenAI model validation must not run for Groq")

    monkeypatch.setattr(ChatGPTAPI, "_validate_custom_models", fail_if_called)
    client = object.__new__(GroqClient)
    client.set_model_list(["llama-3.3-70b-versatile"])

    assert client.model == "llama-3.3-70b-versatile"
    assert next(client.model_list) == "llama-3.3-70b-versatile"
