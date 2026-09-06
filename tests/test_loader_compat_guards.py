"""Guards that need what only the loader knows.

Three of the audit's rows cannot be decided at the CLI: what a checkpoint
on disk was written by, what the codex route's context really is, and how
many requests a `--test` slice becomes. They live here, at the point where
the answer exists.
"""

import os
import pickle
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from ebooklib import epub

from book_maker.glossary import Glossary
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.plan import session_token_budget
from book_maker.session_context import DEFAULT_COMPACT_BUDGET

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
HERMETIC = Path(__file__).resolve().parent / "hermetic"


class Model:
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"
    model_name = "a-model"

    def __init__(self, key, language, **kwargs):
        self._fatal_error_detected = False
        self.language = language

    def translate(self, text):
        return f"<T>{text}</T>"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


class OtherModel(Model):
    model_name = "another-model"


class ListModel(Model):
    """A `--model_list` run: several models, one of them current."""

    _model_names = ("first-model", "second-model")
    _configured_model_names = ("first-model", "second-model")

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        self.model = self._model_names[0]

    @property
    def model_name(self):
        return self.model


class OtherListModel(ListModel):
    _model_names = ("first-model", "third-model")
    _configured_model_names = ("first-model", "third-model")


class CodexLike(Model):
    """A route whose context is one thread, asked for or not."""

    SUPPORTS_SESSION_CONTEXT = True
    SESSION_CONTEXT_ALWAYS_ON = True

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        self.context_compact_at = kwargs.get("context_compact_at")
        self.no_context_compact = kwargs.get("no_context_compact", False)


class GlossaryModel(Model):
    """A route that keeps the pinned/learned split the real ones keep."""

    SUPPORTS_GLOSSARY = True

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        self.pinned = kwargs.get("glossary") or Glossary()
        self.learned = Glossary()
        self.glossary = self.pinned


class WindowOnly(Model):
    """An ordinary route: a session only when one is asked for."""

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        self.context_compact_at = kwargs.get("context_compact_at")
        self.no_context_compact = kwargs.get("no_context_compact", False)


def _write_epub(path, paragraphs=("one", "two", "three")):
    book = epub.EpubBook()
    book.set_identifier("guards")
    book.set_title("Guards")
    book.set_language("en")
    item = epub.EpubHtml(title="Chapter", file_name="chapter.xhtml", lang="en")
    body = "".join(f"<p>{text}</p>" for text in paragraphs)
    item.content = f"<html><body>{body}</body></html>"
    book.add_item(item)
    book.toc = (item,)
    book.spine = [item]
    epub.write_epub(str(path), book)
    return path


def _loader(source, model=Model, key="", **kwargs):
    kwargs.setdefault("language", "zh-hans")
    return EPUBBookLoader(str(source), model, key=key, resume=False, **kwargs)


def _write_checkpoint(source, model=Model, key="", **kwargs):
    """A finished-looking checkpoint, written the way a real run writes one."""
    loader = _loader(source, model, key=key, **kwargs)
    loader._planned_job_ids = ["job-0", "job-1"]
    loader.p_to_save = ["<T>one</T>"]
    loader._save_progress()
    return loader.bin_path


# --------------------------------------------------- A5: the run fingerprint


class TestResumeRunFingerprint:
    """A slot's position is bound by its job id; its *contents* were bound by
    nothing, so `--resume --language es` used to splice two languages into
    one book and report success."""

    def test_a_different_language_is_refused(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(source, language="english")

        resumed = EPUBBookLoader(
            str(source), Model, key="", resume=True, language="spanish"
        )
        with pytest.raises(SystemExit) as stopped:
            resumed._check_resume_run_fingerprint()

        assert stopped.value.code == 1
        out = " ".join(capsys.readouterr().out.split())
        assert "different language, prompt or model" in out
        assert "Delete" in out

    def test_the_same_language_resumes(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(source, language="english")

        resumed = EPUBBookLoader(
            str(source), Model, key="", resume=True, language="english"
        )
        resumed._check_resume_run_fingerprint()

        assert "different language" not in capsys.readouterr().out

    def test_a_different_prompt_is_refused(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(source, prompt_config={"user": "Translate {text}"})

        resumed = EPUBBookLoader(
            str(source),
            Model,
            key="",
            resume=True,
            language="zh-hans",
            prompt_config={"user": "Render {text} in a stiff register"},
        )
        with pytest.raises(SystemExit):
            resumed._check_resume_run_fingerprint()

    def test_a_different_model_is_refused(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(source, model=Model)

        resumed = EPUBBookLoader(
            str(source), OtherModel, key="", resume=True, language="zh-hans"
        )
        with pytest.raises(SystemExit):
            resumed._check_resume_run_fingerprint()

    def test_a_different_glossary_is_refused(self, tmp_path):
        # a pin is a substitution the run must make, so resuming under
        # another one splices two vocabularies into one book
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(
            source, model=GlossaryModel, glossary=Glossary.parse("Winston → 温斯顿\n")
        )

        resumed = EPUBBookLoader(
            str(source),
            GlossaryModel,
            key="",
            resume=True,
            language="zh-hans",
            glossary=Glossary.parse("Winston → 溫斯頓\n"),
        )
        with pytest.raises(SystemExit):
            resumed._check_resume_run_fingerprint()

    def test_the_same_glossary_resumes(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        pins = "Winston → 温斯顿\n"
        _write_checkpoint(source, model=GlossaryModel, glossary=Glossary.parse(pins))

        resumed = EPUBBookLoader(
            str(source),
            GlossaryModel,
            key="",
            resume=True,
            language="zh-hans",
            glossary=Glossary.parse(pins),
        )
        resumed._check_resume_run_fingerprint()
        assert "different language" not in capsys.readouterr().out

    def test_what_the_run_learned_does_not_move_the_fingerprint(self, tmp_path):
        # the derived half changes every window by design; folding it in
        # would make every resume look like a different run
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, model=GlossaryModel)
        before = loader._run_fingerprint()
        loader._run_fingerprint_value = None
        loader.translate_model.learned = Glossary.parse("Boxer → 拳击手\n")
        loader.translate_model.glossary = loader.translate_model.learned
        assert loader._run_fingerprint() == before

    def test_a_pre_fingerprint_checkpoint_warns_and_continues(self, tmp_path, capsys):
        # refusing these would strand every run interrupted before today
        source = _write_epub(tmp_path / "book.epub")
        path = _write_checkpoint(source)
        with open(path, "rb") as handle:
            payload = pickle.load(handle)
        payload.pop("run_fingerprint")
        with open(path, "wb") as handle:
            pickle.dump(payload, handle)

        resumed = EPUBBookLoader(
            str(source), Model, key="", resume=True, language="anything-else"
        )
        resumed._check_resume_run_fingerprint()

        out = " ".join(capsys.readouterr().out.split())
        assert "written before runs recorded their language" in out

    def test_a_fresh_run_is_never_asked(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source)
        loader._check_resume_run_fingerprint()
        assert capsys.readouterr().out == ""

    def test_the_check_runs_before_anything_is_translated(self, tmp_path, capsys):
        # not after the plan is built and half the book re-derived: the
        # refusal has to be the first thing the resumed run does
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(source, language="english")

        class Explodes(Model):
            def translate(self, text):
                raise AssertionError("the resumed run translated something")

            def translate_list(self, texts):
                raise AssertionError("the resumed run translated something")

        resumed = EPUBBookLoader(
            str(source), Explodes, key="", resume=True, language="spanish"
        )
        with pytest.raises(SystemExit):
            resumed.make_bilingual_book()

    def test_the_fingerprint_is_only_of_what_changes_the_words(self, tmp_path):
        # a flag that does not change a translation must not invalidate a
        # checkpoint: resuming is the whole point of having one
        source = _write_epub(tmp_path / "book.epub")
        first = _loader(source)
        second = _loader(source)
        second.is_test = True
        second.test_num = 2
        second.accumulated_num = 1600
        assert first._run_fingerprint() == second._run_fingerprint()


class TestTheFingerprintIsOfTheRunAsResolved:
    """The command is not the run. A prompt settles out of the flag, the
    environment and the route's default; a `--model_list` run has no single
    "current" model. Hashing what was typed missed both."""

    def test_a_model_list_is_the_list_not_whichever_model_is_current(self, tmp_path):
        # rotation means the model in hand at save time is luck; the same
        # command must not accept or reject its own checkpoint by it
        source = _write_epub(tmp_path / "book.epub")
        first = _loader(source, ListModel)
        second = _loader(source, ListModel)
        second.translate_model.model = "second-model"

        assert first._run_fingerprint() == second._run_fingerprint()

    def test_a_model_list_run_resumes_whatever_it_rotated_to(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        writer = _loader(source, ListModel)
        writer.translate_model.model = "second-model"  # rotated by save time
        writer._planned_job_ids = ["job-0", "job-1"]
        writer.p_to_save = ["<T>one</T>"]
        writer._save_progress()

        resumed = EPUBBookLoader(
            str(source), ListModel, key="", resume=True, language="zh-hans"
        )
        resumed._check_resume_run_fingerprint()

        assert "different language" not in capsys.readouterr().out

    def test_a_different_model_list_is_still_refused(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        _write_checkpoint(source, ListModel)

        resumed = EPUBBookLoader(
            str(source), OtherListModel, key="", resume=True, language="zh-hans"
        )
        with pytest.raises(SystemExit):
            resumed._check_resume_run_fingerprint()

    def test_a_changed_env_system_message_is_refused(self, tmp_path, monkeypatch):
        # $OPENAI_API_SYS_MSG never reaches --prompt, so the old fingerprint
        # hashed the same bytes for two runs under different instructions
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        source = _write_epub(tmp_path / "book.epub")
        monkeypatch.setenv("OPENAI_API_SYS_MSG", "Translate in a stiff register.")
        _write_checkpoint(source, ChatGPTAPI, key="k")

        monkeypatch.setenv("OPENAI_API_SYS_MSG", "Translate breezily.")
        resumed = EPUBBookLoader(
            str(source), ChatGPTAPI, key="k", resume=True, language="zh-hans"
        )
        with pytest.raises(SystemExit) as stopped:
            resumed._check_resume_run_fingerprint()
        assert stopped.value.code == 1

    def test_the_same_env_system_message_resumes(self, tmp_path, monkeypatch, capsys):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        source = _write_epub(tmp_path / "book.epub")
        monkeypatch.setenv("OPENAI_API_SYS_MSG", "Translate in a stiff register.")
        _write_checkpoint(source, ChatGPTAPI, key="k")

        resumed = EPUBBookLoader(
            str(source), ChatGPTAPI, key="k", resume=True, language="zh-hans"
        )
        resumed._check_resume_run_fingerprint()

        assert "different language" not in capsys.readouterr().out

    def test_a_changed_source_language_note_moves_the_fingerprint(self, tmp_path):
        # --source_lang is appended to the system message for the whole
        # run, so it changes the instructions the slots were written under
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        source = _write_epub(tmp_path / "book.epub")
        writer = _loader(source, ChatGPTAPI, key="k")
        writer.translate_model.source_language = "English"
        before = writer._run_fingerprint()

        other = _loader(source, ChatGPTAPI, key="k")
        other.translate_model.source_language = "French"
        assert other._run_fingerprint() != before

    def test_the_snapshot_does_not_move_when_the_endpoint_narrows_a_list(
        self, tmp_path
    ):
        # `_ensure_models_routable` drops models the endpoint refuses, mid
        # run: the fingerprint is taken once at run start so a checkpoint
        # written after that still matches the run that wrote it
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, ListModel)
        loader._check_resume_run_fingerprint()
        before = loader._run_fingerprint()

        loader.translate_model._model_names = ("second-model",)
        assert loader._run_fingerprint() == before

    def test_a_narrowing_before_the_snapshot_does_not_move_it_either(self, tmp_path):
        # the CLI's plan probe runs `_ensure_models_routable` before the
        # loader ever takes its snapshot, so the configured list has to be
        # recorded at configuration time, not read from the routable list
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, ListModel)
        loader.translate_model._model_names = ("second-model",)  # probe narrowed

        untouched = _loader(source, ListModel)
        assert loader._run_fingerprint() == untouched._run_fingerprint()

    def test_a_changed_style_note_moves_the_fingerprint(self, tmp_path):
        # a fixed --prompt style rides in every request, so a style-only
        # change writes a different book under the same user/system pair
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI

        source = _write_epub(tmp_path / "book.epub")
        plain = _loader(source, ChatGPTAPI, key="k")
        plain.translate_model.style_note = "wooden, literal"
        before = plain._run_fingerprint()

        restyled = _loader(source, ChatGPTAPI, key="k")
        restyled.translate_model.style_note = "breezy"
        assert restyled._run_fingerprint() != before


# ------------------------------------------- B1: codex counts as a session


class TestCodexIsASession:
    """The codex thread IS the history. Without this the flagless codex run
    left grouping off and paid per paragraph, on a route billed by request."""

    def test_the_codex_route_is_a_session_without_the_flag(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None)
        assert loader._session_run is True

    def test_an_ordinary_route_is_not(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, WindowOnly, context_mode=None)
        assert loader._session_run is False

    def test_the_grouping_budget_is_derived_on_the_codex_route(self, tmp_path):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None)
        loader.plan_mode = True
        loader.translate_tags = "auto"

        assert loader._plan_token_budget == session_token_budget(None)

    def test_the_compact_budget_is_the_pinned_default_and_is_narrated(
        self, tmp_path, capsys
    ):
        # nothing is derived any more: the codex route narrates the same
        # pinned number every other session run gets
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None)
        loader.plan_mode = True
        loader.translate_tags = "auto"

        loader._narrate_session_compact_budget()

        # untouched — the translator falls back to the default itself
        assert loader.translate_model.context_compact_at is None
        out = " ".join(capsys.readouterr().out.split())
        assert (
            out == f"session: compacting at {DEFAULT_COMPACT_BUDGET} estimated "
            f"tokens (the default; --context-compact-at overrides)"
        )

    def test_it_is_said_once_per_run(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None)

        loader._narrate_session_compact_budget()
        loader._narrate_session_compact_budget()

        assert capsys.readouterr().out.count("session: compacting at") == 1

    def test_an_ordinary_route_narrates_nothing(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, WindowOnly, context_mode=None)
        loader.plan_mode = True
        loader.translate_tags = "auto"

        assert loader._plan_token_budget is None
        loader._narrate_session_compact_budget()

        assert loader.translate_model.context_compact_at is None
        assert capsys.readouterr().out == ""

    def test_an_explicit_budget_wins_and_is_narrated_as_the_flag(
        self, tmp_path, capsys
    ):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None, context_compact_at=2000)
        loader.plan_mode = True
        loader.translate_tags = "auto"

        loader._narrate_session_compact_budget()

        assert loader.translate_model.context_compact_at == 2000
        out = " ".join(capsys.readouterr().out.split())
        assert (
            out == "session: compacting at 2000 estimated tokens "
            "(--context-compact-at)"
        )

    def test_no_context_compact_narrates_nothing(self, tmp_path, capsys):
        # the run never compacts, so there is no window to announce
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None)
        loader.translate_model.no_context_compact = True

        loader._narrate_session_compact_budget()

        assert capsys.readouterr().out == ""

    def test_accumulated_num_one_still_turns_grouping_off(self, tmp_path):
        # the documented off switch has to keep working on this route too
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, CodexLike, context_mode=None)
        loader.plan_mode = True
        loader.accumulated_num_given = True
        assert loader._plan_token_budget == 0


# ------------------------------------------- B7: what a --test slice covers


class TestTestSliceRequestCount:
    """`--test_num` counts units. On a grouped run the default `--test` can
    be one request, which exercises no rollover, compaction or misalignment
    at all — and says nothing about it."""

    def _plans(self, batch_indexes):
        return [
            SimpleNamespace(
                jobs=[
                    SimpleNamespace(document_index=0, batch_index=index)
                    for index in batch_indexes
                ]
            )
        ]

    def test_a_grouped_slice_says_how_few_requests_it_is(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, is_test=True, test_num=8)

        loader._report_test_slice_requests(self._plans([0] * 8), 8)

        out = " ".join(capsys.readouterr().out.split())
        assert "8 unit(s) in 1 request(s)" in out
        assert "--test_num counts units, not requests" in out

    def test_an_ungrouped_slice_says_nothing(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, is_test=True, test_num=8)

        loader._report_test_slice_requests(self._plans(range(8)), 8)

        assert capsys.readouterr().out == ""

    def test_a_full_run_says_nothing(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source)

        loader._report_test_slice_requests(self._plans([0] * 8), 8)

        assert capsys.readouterr().out == ""

    def test_an_empty_slice_says_nothing(self, tmp_path, capsys):
        source = _write_epub(tmp_path / "book.epub")
        loader = _loader(source, is_test=True, test_num=8)

        loader._report_test_slice_requests([], 0)

        assert capsys.readouterr().out == ""


# ------------------------------- A3: classification is not truncated by --test


def _env():
    env = dict(os.environ)
    for name in ("BBM_API_KEY", "OPENAI_API_KEY", "BBM_OPENAI_API_KEY"):
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


def test_a_test_run_is_told_classification_covers_the_whole_book(tmp_path):
    # --test truncates the units translated, never the partition: the
    # classifier is paid for over the whole book by a run asked to be small
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "openai",
        "--key",
        "sk-test",
        "--plan-classify",
        "model",
        "--test",
        "--test_num",
        "1",
    )
    flat = " ".join((proc.stdout + proc.stderr).split())
    assert proc.returncode == 0, flat
    assert "plan classification covers the whole book" in flat
    assert "cached and reused by the full run" in flat


def test_a_full_run_is_not_told_that(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "openai",
        "--key",
        "sk-test",
        "--plan-classify",
        "model",
    )
    flat = " ".join((proc.stdout + proc.stderr).split())
    assert proc.returncode == 0, flat
    assert "plan classification covers the whole book" not in flat
