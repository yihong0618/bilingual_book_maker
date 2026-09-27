"""`make_book.py --to-epub`: the route, the copy, and the progress line.

Everything here runs in process and none of it starts a model or Pandoc:
what is under test is which code the CLI hands a PDF to, what it leaves
beside that PDF afterwards, and what an operator sees while the slow stage
is running. The stages themselves have their own tests
(`test_docling_adapter.py`, `test_bundle_pipeline.py`).
"""

import io
import sys
from pathlib import Path

import pytest

from book_maker import cli
from book_maker.pipeline import docling_parser, messages, to_epub
from book_maker.pipeline.errors import PipelineError
from book_maker.pipeline.pdf_figures import FIGURE_POLICY_DEFAULT
from book_maker.pipeline.progress import ProgressLine

TRANSLATION = ["--api_format", "google", "--language", "zh-hans"]


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


def stages(recorder, *, export=True, fail=None):
    """Fake extract/translate/export that only record being called."""

    def prepare_stage(
        bundle,
        source,
        *,
        pandoc,
        device=None,
        pages=None,
        ocr=False,
        ocr_lang=None,
        formula_images=True,
        progress=True,
        structure=None,
    ):
        recorder.append(("extract", device, ocr, progress, pages, ocr_lang))
        if structure is not None:
            recorder.append(("structure", structure.model, structure.rev))
        bundle.source.write_text("# Title\n\nProse.\n", encoding="utf-8")

    def translate_stage(bundle, options, *, pandoc):
        recorder.append(("translate", tuple(options)))
        bundle.bilingual_markdown.write_text("# Title\n", encoding="utf-8")

    def export_stage(bundle, *, pandoc):
        recorder.append(("export",))
        if fail:
            raise PipelineError(fail, stage="export")
        bundle.epub.write_bytes(b"PK\x03\x04 not really a zip")
        return bundle.epub

    return {
        "prepare_stage": prepare_stage,
        "translate_stage": translate_stage,
        "export_stage": export_stage,
    }


@pytest.fixture
def no_pandoc_lookup(monkeypatch):
    """Pandoc resolves to a name; nothing here ever runs it."""
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------
class TestRouting:
    def test_a_pdf_with_the_flag_runs_the_pipeline_instead_of_the_loader(
        self, pdf, monkeypatch
    ):
        seen = {}

        def fake(
            path,
            argv,
            *,
            device,
            pdf_ocr,
            ocr_lang,
            pages,
            formula_images,
            ocr_replace_layer,
            ocr_engine,
            img_model,
            img_base_url,
            img_key,
            quiet,
            figure_policy,
        ):
            seen.update(
                ocr_replace_layer=ocr_replace_layer,
                ocr_engine=ocr_engine,
                path=Path(path),
                argv=list(argv),
                device=device,
                pdf_ocr=pdf_ocr,
                ocr_lang=ocr_lang,
                pages=pages,
                formula_images=formula_images,
                img_model=img_model,
                img_base_url=img_base_url,
                img_key=img_key,
                quiet=quiet,
            )
            assert figure_policy == FIGURE_POLICY_DEFAULT

        monkeypatch.setattr(to_epub, "pdf_to_epub", fake)
        monkeypatch.setitem(
            cli.BOOK_LOADER_DICT,
            "pdf",
            lambda *a, **k: pytest.fail("the legacy PDF loader was built"),
        )

        cli.main(["--book_name", str(pdf), "--to-epub", *TRANSLATION])

        assert seen["path"] == pdf
        # Nothing chosen: the adapter's own detection decides the device.
        assert seen["device"] is None
        # OCR is opt-in; a born-digital PDF is read without it.
        assert seen["pdf_ocr"] is False
        assert seen["ocr_lang"] is None
        assert seen["pages"] is None
        # an embedded layer is kept unless replacing it is asked for
        assert seen["ocr_replace_layer"] is False
        # docling takes whichever OCR engine is installed unless one is named
        assert seen["ocr_engine"] == "auto"
        assert seen["img_model"] is None
        assert seen["quiet"] is False
        # every other option is the translation's, and is handed on as typed
        assert seen["argv"] == ["--book_name", str(pdf), "--to-epub", *TRANSLATION]

    def test_the_inner_run_gets_the_command_line_as_typed(self, pdf, monkeypatch):
        # PIN (lead, 260920, Codex review): the legacy rewrite is the inner
        # run's to do -- it also names the env variable an old alias implies
        # its key lives in, and only the run that resolves the endpoint reads
        # that. So the divert passes the raw argv, old flags included.
        seen = {}
        monkeypatch.setattr(
            to_epub,
            "pdf_to_epub",
            lambda path, argv, **kwargs: seen.update(argv=list(argv)),
        )
        typed = [
            "--book_name",
            str(pdf),
            "--to-epub",
            "--model",
            "gemini",
            "--language",
            "ja",
        ]
        cli.main(typed)
        assert seen["argv"] == typed

    def test_the_device_and_quiet_reach_the_pipeline(self, pdf, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(
            [
                "--book_name",
                str(pdf),
                "--to-epub",
                "--device",
                "cpu",
                "--quiet",
                *TRANSLATION,
            ]
        )
        assert seen == {
            "device": "cpu",
            "pdf_ocr": False,
            "ocr_lang": None,
            "pages": None,
            # Display formulas are kept as pictures unless asked otherwise:
            # without them the equations are missing from the book.
            "formula_images": True,
            "ocr_replace_layer": False,
            "ocr_engine": "auto",
            # The region-role pass is off unless its model is named.
            "img_model": None,
            "img_base_url": None,
            "img_key": None,
            "figure_policy": FIGURE_POLICY_DEFAULT,
            "quiet": True,
        }

    def test_an_unknown_device_is_refused_before_the_pipeline_is_reached(
        self, pdf, monkeypatch
    ):
        """The choices are argparse's, so a typo costs nothing."""
        monkeypatch.setattr(
            to_epub,
            "pdf_to_epub",
            lambda *a, **k: pytest.fail("an unknown device reached the pipeline"),
        )
        with pytest.raises(SystemExit) as exited:
            cli.main(
                ["--book_name", str(pdf), "--to-epub", "--device", "tpu", *TRANSLATION]
            )
        assert exited.value.code == 2

    def test_pages_reaches_the_pipeline(self, pdf, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(["--book_name", str(pdf), "--to-epub", "--pages", "6-7", *TRANSLATION])
        assert seen == {
            "device": None,
            "pdf_ocr": False,
            "ocr_lang": None,
            "pages": "6-7",
            "formula_images": True,
            "ocr_replace_layer": False,
            "ocr_engine": "auto",
            # The region-role pass is off unless its model is named.
            "img_model": None,
            "img_base_url": None,
            "img_key": None,
            "figure_policy": FIGURE_POLICY_DEFAULT,
            "quiet": False,
        }

    def test_ocr_lang_reaches_the_pipeline_as_typed(self, pdf, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(
            [
                "--book_name",
                str(pdf),
                "--to-epub",
                "--pdf-ocr",
                "--ocr-lang",
                "ch_sim,en",
                *TRANSLATION,
            ]
        )
        assert seen == {
            "device": None,
            "pdf_ocr": True,
            "ocr_lang": "ch_sim,en",
            "pages": None,
            "formula_images": True,
            "ocr_replace_layer": False,
            "ocr_engine": "auto",
            # The region-role pass is off unless its model is named.
            "img_model": None,
            "img_base_url": None,
            "img_key": None,
            "figure_policy": FIGURE_POLICY_DEFAULT,
            "quiet": False,
        }

    def test_pdf_ocr_reaches_the_pipeline(self, pdf, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(["--book_name", str(pdf), "--to-epub", "--pdf-ocr", *TRANSLATION])
        assert seen == {
            "device": None,
            "pdf_ocr": True,
            "ocr_lang": None,
            "pages": None,
            "formula_images": True,
            "ocr_replace_layer": False,
            "ocr_engine": "auto",
            # The region-role pass is off unless its model is named.
            "img_model": None,
            "img_base_url": None,
            "img_key": None,
            "figure_policy": FIGURE_POLICY_DEFAULT,
            "quiet": False,
        }

    @pytest.mark.parametrize(
        "retired,expected,names",
        [
            ("--with-ocr", {"pdf_ocr": True}, ("--with-ocr", "--pdf-ocr")),
            ("--no-gpu", {"device": "cpu"}, ("--no-gpu", "--device cpu")),
        ],
    )
    def test_a_retired_spelling_still_runs_and_names_its_replacement(
        self, pdf, monkeypatch, capsys, retired, expected, names
    ):
        """A person with the old flag in a script deserves a sentence.

        Both spellings keep working for one release; what must not happen
        is an argparse error, or silence about the name that replaced them.
        """
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(["--book_name", str(pdf), "--to-epub", retired, *TRANSLATION])
        assert seen == {
            "device": None,
            "pdf_ocr": False,
            "ocr_lang": None,
            "pages": None,
            "formula_images": True,
            "ocr_replace_layer": False,
            "ocr_engine": "auto",
            # The region-role pass is off unless its model is named.
            "img_model": None,
            "img_base_url": None,
            "img_key": None,
            "figure_policy": FIGURE_POLICY_DEFAULT,
            "quiet": False,
            **expected,
        }
        out = " ".join(capsys.readouterr().out.split())
        assert "deprecated" in out
        for name in names:
            assert name in out

    def test_the_current_device_flag_wins_over_the_retired_one(self, pdf, monkeypatch):
        """The operator who wrote the current flag meant it."""
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(
            [
                "--book_name",
                str(pdf),
                "--to-epub",
                "--no-gpu",
                "--device",
                "cuda",
                *TRANSLATION,
            ]
        )
        assert seen["device"] == "cuda"

    def test_a_pdf_without_the_flag_still_takes_the_legacy_route(
        self, pdf, monkeypatch
    ):
        monkeypatch.setattr(
            to_epub,
            "pdf_to_epub",
            lambda *a, **k: pytest.fail("--to-epub was not asked for"),
        )

        class ReachedTheLegacyLoader(Exception):
            pass

        def loader(*args, **kwargs):
            raise ReachedTheLegacyLoader

        monkeypatch.setitem(cli.BOOK_LOADER_DICT, "pdf", loader)
        with pytest.raises(ReachedTheLegacyLoader):
            cli.main(["--book_name", str(pdf), *TRANSLATION])

    def test_a_pipeline_failure_is_one_line_and_exit_1(self, pdf, monkeypatch, capsys):
        def fail(*args, **kwargs):
            raise PipelineError("the backend said no", stage="extract")

        monkeypatch.setattr(to_epub, "pdf_to_epub", fail)
        with pytest.raises(SystemExit) as exited:
            cli.main(["--book_name", str(pdf), "--to-epub", *TRANSLATION])
        assert exited.value.code == 1
        out = " ".join(capsys.readouterr().out.split())
        assert "extract failed: the backend said no" in out
        assert "Traceback" not in out

    def test_a_missing_pandoc_names_the_path_not_a_flag_that_does_not_exist(
        self, pdf, monkeypatch, capsys
    ):
        # The main CLI has no --pandoc option, so the shared refusal that
        # offers one would send its operator looking for nothing.
        def missing(explicit=None):
            raise PipelineError(messages.PANDOC_REQUIRED)

        monkeypatch.setattr(to_epub, "find_pandoc", missing)
        with pytest.raises(SystemExit) as exited:
            cli.main(["--book_name", str(pdf), "--to-epub", *TRANSLATION])
        assert exited.value.code == 1
        out = capsys.readouterr().out
        assert messages.PANDOC_ON_PATH in " ".join(out.split())
        assert "--pandoc PATH" not in out


# --------------------------------------------------------------------------
# What is left on disk
# --------------------------------------------------------------------------
class TestTheStages:
    def test_they_run_in_order_and_the_epub_lands_beside_the_pdf(
        self, pdf, no_pandoc_lookup, capsys
    ):
        order = []
        result = to_epub.pdf_to_epub(pdf, TRANSLATION, **stages(order))

        assert [step[0] for step in order] == ["extract", "translate", "export"]
        assert result == pdf.parent / "book_bilingual.epub"
        assert result.is_file()
        # the bundle stays, for editing and for the next run to resume from
        bundle = pdf.parent / "book_book"
        assert (bundle / "source.md").is_file()
        assert (bundle / "book_bilingual.epub").is_file()
        out = capsys.readouterr().out
        assert str(bundle) in out
        assert str(result) in out

    def test_the_translation_stage_gets_the_options_the_route_does_not_own(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        to_epub.pdf_to_epub(
            pdf,
            ["--book_name", str(pdf), "--to-epub", "--device", "cpu", *TRANSLATION],
            **stages(order),
        )
        translated = dict(enumerate(order))[1]
        assert list(translated[1]) == TRANSLATION

    def test_the_device_reaches_the_extract_stage_and_the_default_detects(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        to_epub.pdf_to_epub(pdf, TRANSLATION, **stages(order))
        assert order[0] == ("extract", "auto", False, True, None, None)

        order = []
        to_epub.pdf_to_epub(pdf, TRANSLATION, device="cpu", quiet=True, **stages(order))
        assert order[0] == ("extract", "cpu", False, False, None, None)

    def test_pdf_ocr_reaches_the_extract_stage(self, pdf, no_pandoc_lookup):
        order = []
        to_epub.pdf_to_epub(pdf, TRANSLATION, pdf_ocr=True, **stages(order))
        assert order[0] == ("extract", "auto", True, True, None, None)

    def test_ocr_lang_reaches_the_extract_stage_as_typed(self, pdf, no_pandoc_lookup):
        # The codes are the engine's to check; the route hands them on as
        # typed and the stage splits them.
        order = []
        to_epub.pdf_to_epub(
            pdf, TRANSLATION, pdf_ocr=True, ocr_lang="ch_sim,en", **stages(order)
        )
        assert order[0] == ("extract", "auto", True, True, None, "ch_sim,en")

    def test_an_empty_ocr_lang_is_refused_before_anything_is_extracted(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        with pytest.raises(PipelineError) as refused:
            to_epub.pdf_to_epub(
                pdf, TRANSLATION, pdf_ocr=True, ocr_lang=" , ", **stages(order)
            )
        assert refused.value.detail == messages.OCR_LANG_EMPTY
        assert order == []

    def test_a_page_selection_gets_its_own_bundle_and_book(
        self, pdf, no_pandoc_lookup, capsys
    ):
        # PIN (owner ask 260921, docs/260921-feat-PDF_PAGES_FLAG.md): a
        # chapter run must not overwrite the whole-book run beside it, and
        # a rerun with the same selection resumes its own bundle.
        order = []
        result = to_epub.pdf_to_epub(pdf, TRANSLATION, pages="6-7", **stages(order))
        assert order[0] == ("extract", "auto", False, True, "6-7", None)
        assert result == pdf.parent / "book_pages-6-7_bilingual.epub"
        assert result.is_file()
        bundle = pdf.parent / "book_pages-6-7_book"
        assert (bundle / "source.md").is_file()
        assert not (pdf.parent / "book_book").exists()
        assert not (pdf.parent / "book_bilingual.epub").exists()
        assert str(bundle) in capsys.readouterr().out

    def test_a_selection_with_spaces_and_commas_still_names_a_file(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        result = to_epub.pdf_to_epub(
            pdf, TRANSLATION, pages="1, 3,5-7", **stages(order)
        )
        assert result == pdf.parent / "book_pages-1,3,5-7_bilingual.epub"
        assert order[0][4] == "1, 3,5-7"

    def test_a_selection_that_does_not_parse_is_refused_before_extraction(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        with pytest.raises(PipelineError) as refused:
            to_epub.pdf_to_epub(pdf, TRANSLATION, pages="7-6", **stages(order))
        assert "ends before it starts" in refused.value.detail
        assert order == []
        assert not (pdf.parent / "book_pages-7-6_book").exists()

    def test_a_failed_export_copies_nothing(self, pdf, no_pandoc_lookup):
        order = []
        with pytest.raises(PipelineError) as refused:
            to_epub.pdf_to_epub(
                pdf, TRANSLATION, **stages(order, fail="navigation is invalid")
            )
        assert "navigation is invalid" in refused.value.detail
        # the name a reader opens must never hold a half-built book
        assert not (pdf.parent / "book_bilingual.epub").exists()

    def test_a_previous_book_survives_a_copy_that_dies(
        self, pdf, no_pandoc_lookup, monkeypatch
    ):
        # PIN (lead, 260920, Codex review): the name a reader opens is
        # replaced atomically; a copy that dies halfway leaves the old book
        # in place and no partial file beside it.
        destination = pdf.parent / "book_bilingual.epub"
        destination.write_bytes(b"the previous good book")

        def dies(src, dst):
            Path(dst).write_bytes(b"half")
            raise OSError("disk full")

        monkeypatch.setattr(to_epub.shutil, "copyfile", dies)
        with pytest.raises(PipelineError) as refused:
            to_epub.pdf_to_epub(pdf, TRANSLATION, **stages([]))
        assert "disk full" in refused.value.detail
        assert destination.read_bytes() == b"the previous good book"
        assert not list(pdf.parent.glob("*.part"))

    def test_a_finished_copy_leaves_no_partial_file(self, pdf, no_pandoc_lookup):
        to_epub.pdf_to_epub(pdf, TRANSLATION, **stages([]))
        assert not list(pdf.parent.glob("*.part"))

    # PIN (lead 260925, Codex review 01a0dc74,
    # docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md): the partial file
    # behind the copy is this invocation's own, so concurrent runs writing
    # the same book neither share nor delete each other's.
    def test_each_copy_goes_through_its_own_partial_file(
        self, pdf, no_pandoc_lookup, monkeypatch
    ):
        used = []
        real = to_epub.shutil.copyfile

        def records(src, dst):
            used.append(Path(dst))
            return real(src, dst)

        monkeypatch.setattr(to_epub.shutil, "copyfile", records)
        to_epub.pdf_to_epub(pdf, TRANSLATION, **stages([]))
        # the fake translate stage leaves no record; a rerun must not see an edit
        to_epub.bundle_path(pdf).joinpath("book_bilingual.md").unlink()
        to_epub.pdf_to_epub(pdf, TRANSLATION, **stages([]))
        assert len(used) == 2 and used[0] != used[1]
        assert all(path.parent == pdf.parent for path in used)
        # the book a reader opens keeps the bundle EPUB's permissions, not
        # the owner-only ones of a temporary file
        book = pdf.parent / "book_bilingual.epub"
        built = to_epub.bundle_path(pdf) / "book_bilingual.epub"
        assert book.stat().st_mode & 0o777 == built.stat().st_mode & 0o777
        assert not list(pdf.parent.glob("*.part"))

    def test_a_copy_that_dies_removes_only_its_own_partial_file(
        self, pdf, no_pandoc_lookup, monkeypatch
    ):
        # another export's partial file, under the name every run once shared
        theirs = pdf.parent / "book_bilingual.epub.part"
        theirs.write_bytes(b"another run's half-written book")
        ours = []

        def dies(src, dst):
            ours.append(Path(dst))
            Path(dst).write_bytes(b"half")
            raise OSError("disk full")

        monkeypatch.setattr(to_epub.shutil, "copyfile", dies)
        with pytest.raises(PipelineError):
            to_epub.pdf_to_epub(pdf, TRANSLATION, **stages([]))
        assert ours and ours[0] != theirs and not ours[0].exists()
        assert theirs.read_bytes() == b"another run's half-written book"

    def test_the_translation_options_are_parsed_before_anything_is_extracted(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        with pytest.raises(PipelineError):
            to_epub.pdf_to_epub(pdf, ["--not-an-option"], **stages(order))
        assert order == []


@pytest.mark.parametrize(
    "argv,kept",
    [
        (["--to-epub", "--model", "m"], ["--model", "m"]),
        (["--pdf-ocr", "--key", "k"], ["--key", "k"]),
        # an extraction setting: it must not reach the translation run, where
        # it would trip compat row C30 and enter the translation fingerprint
        (["--no-formula-images", "--key", "k"], ["--key", "k"]),
        (["--device", "cpu", "--key", "k"], ["--key", "k"]),
        (["--device=cpu", "--key", "k"], ["--key", "k"]),
        # The retired spellings are still stripped: they still parse, so
        # they would still reach the translation CLI, which never heard of
        # them.
        (["--no-gpu", "--key", "k"], ["--key", "k"]),
        (["--with-ocr", "--key", "k"], ["--key", "k"]),
        (["--pages", "6-7", "--key", "k"], ["--key", "k"]),
        (["--pages=6-7", "--key", "k"], ["--key", "k"]),
        (["--ocr-lang", "ch_sim,en", "--key", "k"], ["--key", "k"]),
        (["--ocr-lang=ja", "--key", "k"], ["--key", "k"]),
        # route-owned: the inner run would warn (C34) and fingerprint it
        (["--ocr-replace-layer", "--key", "k"], ["--key", "k"]),
        # route-owned: the engine is the extraction's (row C36 would warn)
        (["--ocr-engine", "ocrmac", "--key", "k"], ["--key", "k"]),
        (["--ocr-engine=easyocr", "--key", "k"], ["--key", "k"]),
        # route-owned: the image endpoint is the extraction's, and must
        # not reach the translation run (row C31 would warn) or its
        # fingerprint
        (["--img-model", "gpt-5.6-luna", "--key", "k"], ["--key", "k"]),
        (["--img-model=gpt-5.6-luna", "--key", "k"], ["--key", "k"]),
        (["--img-base-url", "http://h/v1", "--key", "k"], ["--key", "k"]),
        (["--img-key", "vk", "--key", "k"], ["--key", "k"]),
        (["--book_name", "b.pdf", "--test"], ["--test"]),
        (["--book_name=b.pdf", "--test"], ["--test"]),
        # a value that happens to look like a flag this route owns is still
        # the previous option's value
        (["--language", "zh-hans", "--to-epub"], ["--language", "zh-hans"]),
    ],
)
def test_only_the_route_s_own_options_are_stripped(argv, kept):
    assert to_epub.translation_argv(argv) == kept


# --------------------------------------------------------------------------
# The progress line
# --------------------------------------------------------------------------
class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TestProgressLine:
    def line(self, tty, **kwargs):
        stream, clock = Stream(tty), Clock()
        return (
            ProgressLine(
                "Extracting PDF: 3 pages, OCR on cpu",
                stream=stream,
                clock=clock,
                **kwargs,
            ),
            stream,
            clock,
        )

    def test_a_terminal_gets_one_line_rewritten_in_place(self):
        line, stream, clock = self.line(True)
        line.start()
        assert stream.getvalue() == "\rExtracting PDF: 3 pages, OCR on cpu, 0s"

        clock.now = 35.4
        line.note("Processing document book.pdf")
        line.tick()
        written = stream.getvalue()
        assert written.count("\n") == 0
        assert written.endswith(
            "\rExtracting PDF: 3 pages, OCR on cpu, 35s - Processing document book.pdf"
        )

    def test_a_shorter_line_covers_the_longer_one_it_replaces(self):
        line, stream, clock = self.line(True)
        line.start()
        detail = "a very long thing the backend said about loading its models"
        line.note(detail)
        clock.now = 5
        line.tick()
        long_line = stream.getvalue().rsplit("\r", 1)[-1]
        line.detail = None
        clock.now = 10
        line.tick()
        short = "Extracting PDF: 3 pages, OCR on cpu, 10s"
        # the padding is what stops half of the previous line surviving
        assert stream.getvalue().endswith(short + " " * (len(long_line) - len(short)))

    def test_a_log_gets_a_whole_line_every_ten_seconds(self):
        line, stream, clock = self.line(False)
        line.start()
        clock.now = 3
        line.tick()  # too soon: a log is kept forever
        clock.now = 10
        line.note("Finished converting document in 22.22 sec")
        line.tick()
        lines = stream.getvalue().splitlines()
        assert lines == [
            "Extracting PDF: 3 pages, OCR on cpu, 0s",
            "Extracting PDF: 3 pages, OCR on cpu, 10s - Finished converting "
            "document in 22.22 sec",
        ]
        assert "\r" not in stream.getvalue()

    def test_finishing_clears_the_terminal_line_and_leaves_the_record(self):
        line, stream, clock = self.line(True)
        line.start()
        clock.now = 29
        line.finish("PDF extracted: 3 pages, OCR on cpu, 29s.")
        written = stream.getvalue()
        assert written.endswith("PDF extracted: 3 pages, OCR on cpu, 29s.\n")
        assert "\r" + " " * len("Extracting PDF: 3 pages, OCR on cpu, 0s") in written

    def test_a_quiet_run_says_nothing_at_all(self):
        line, stream, clock = self.line(True, enabled=False)
        line.start()
        clock.now = 60
        line.note("Processing document book.pdf")
        line.tick()
        line.finish("PDF extracted: 3 pages, OCR on cpu, 60s.")
        assert stream.getvalue() == ""

    def test_the_detail_is_bounded(self):
        line, stream, clock = self.line(False)
        line.start()
        line.note("x" * 500)
        assert len(line.detail) == 80


class TestWhatTheParserSaysReachesTheLine:
    """The parser writes to stdout; the adapter turns that into progress.

    `_Sink` stands in for `sys.stdout` during a conversion, so nothing the
    parser logs scrolls past the operator and the last thing it said
    becomes the progress line's detail -- and, if the conversion dies, the
    reason in the failure message.
    """

    def test_the_converter_s_own_output_reaches_the_line(self):
        said = []
        sink = docling_parser._Sink(said.append)
        # whole lines arrive as whole lines
        sink.write("Processing document book.pdf\n")
        # ... but a partial write must not be shown as half a sentence
        sink.write("Finished converting document")
        assert said == ["Processing document book.pdf"]
        sink.write(" in 22.22 sec\n")
        assert said[-1] == "Finished converting document in 22.22 sec"

    def test_two_lines_in_one_write_are_two_notes(self):
        said = []
        sink = docling_parser._Sink(said.append)
        sink.write("first\nsecond\n")
        assert said == ["first", "second"]

    def test_what_is_left_unterminated_is_flushed_not_lost(self):
        said = []
        sink = docling_parser._Sink(said.append)
        sink.write("a line nobody ended")
        assert said == []
        sink.flush()
        assert said == ["a line nobody ended"]

    def test_it_is_never_mistaken_for_a_terminal(self):
        # A parser that asks would draw its own progress bar into a stream
        # that is being read line by line.
        assert docling_parser._Sink(lambda text: None).isatty() is False


# --------------------------------------------------------------------------
# --ocr-replace-layer (packet G; owner ruling 260923, section 6 of
# docs/260923-docs-OWNER_RULINGS_OCR_PROMPT_LAYER_WIKI.md)
# --------------------------------------------------------------------------
class TestOcrReplaceLayer:
    def _route(self, pdf, monkeypatch, *flags):
        seen = {}

        def fake(path, argv, **kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(to_epub, "pdf_to_epub", fake)
        cli.main(["--book_name", str(pdf), "--to-epub", *flags, *TRANSLATION])
        return seen

    def test_without_ocr_the_route_refuses_before_anything_is_read(
        self, pdf, monkeypatch, capsys
    ):
        monkeypatch.setattr(
            to_epub,
            "pdf_to_epub",
            lambda *a, **k: pytest.fail("the route ran without OCR to replace with"),
        )
        with pytest.raises(SystemExit) as stopped:
            cli.main(
                ["--book_name", str(pdf), "--to-epub", "--ocr-replace-layer"]
                + TRANSLATION
            )
        assert stopped.value.code == 1
        out = " ".join(capsys.readouterr().out.split())
        assert messages.OCR_REPLACE_NEEDS_OCR in out

    def test_with_ocr_the_route_is_handed_the_flag(self, pdf, monkeypatch):
        seen = self._route(pdf, monkeypatch, "--pdf-ocr", "--ocr-replace-layer")
        assert seen["ocr_replace_layer"] is True
        assert seen["pdf_ocr"] is True

    def test_the_retired_ocr_spelling_satisfies_it(self, pdf, monkeypatch):
        seen = self._route(pdf, monkeypatch, "--with-ocr", "--ocr-replace-layer")
        assert seen["ocr_replace_layer"] is True

    def test_an_image_model_does_not_imply_it(self, pdf, monkeypatch):
        # PIN (owner 260923): neither --pdf-ocr nor an image model is
        # permission to overwrite an existing layer
        seen = self._route(pdf, monkeypatch, "--pdf-ocr", "--img-model", "m")
        assert seen["ocr_replace_layer"] is False

    def test_the_route_hands_it_to_the_extraction(
        self, pdf, monkeypatch, no_pandoc_lookup
    ):
        handed = []

        def prepare_stage(bundle, source, **kwargs):
            handed.append(kwargs.get("ocr_replace_layer", False))
            bundle.source.write_text("# Title\n\nProse.\n", encoding="utf-8")

        recorder = []
        fakes = stages(recorder)
        fakes["prepare_stage"] = prepare_stage
        for flag in (True, False):
            to_epub.pdf_to_epub(
                pdf,
                ["--book_name", str(pdf), "--to-epub", *TRANSLATION],
                pdf_ocr=True,
                ocr_replace_layer=flag,
                **fakes,
            )
        assert handed == [True, False]
