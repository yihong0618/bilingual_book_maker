"""`--parallel-workers` on the reading edition, through the real CLI.

The PDF route (`make_book.py --book_name X.pdf --to-epub`) translates the
extracted Markdown with `ReadingEditionMarkdownLoader`, which is where the
pairing, the heading identifiers and the completion record are decided. The
Markdown loader translates batches (and, with `--use_context`, whole
sections) on worker threads, so everything that layout depends on has to
survive answers arriving out of order.

What is pinned here: the rendered file is byte-for-byte the serial one, the
headings keep Pandoc's identifiers with no duplicates, the credit and the
completion record are written exactly once, the EPUB's navigation still
lists the source headings and nothing else -- and the work really did run
concurrently, so a loader that quietly fell back to one worker fails these
tests instead of passing them.

Real Pandoc and real EPUB packaging; only the model is fixed.
"""

import json
import re
import sys
import threading
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_fixture,
)

from book_maker.pipeline import to_epub  # noqa: E402
from book_maker.pipeline.bundle import Bundle, sha256_file  # noqa: E402
from book_maker.pipeline.epub_export import export_epub  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.importer import import_markdown  # noqa: E402
from book_maker.pipeline.preflight import parse_markdown  # noqa: E402
from book_maker.pipeline.translate import translate_bundle  # noqa: E402

WORKERS = 3

# Three heading-delimited sections of comparable size, so `--use_context`
# partitions them one per worker and the plain path has several batches to
# hand out. The numbered prose is what proves the order of the result.
BOOK = """# Alpha

Alpha paragraph one, long enough to be its own translatable block of prose.

Alpha paragraph two, long enough to be its own translatable block of prose.

## Beta

Beta paragraph three, long enough to be its own translatable block of prose.

Beta paragraph four, long enough to be its own translatable block of prose.

# Gamma

Gamma paragraph five, long enough to be its own translatable block of prose.

Gamma paragraph six, long enough to be its own translatable block of prose.
"""

HEADINGS = ["Alpha", "Beta", "Gamma"]

REPEATED_HEADINGS = "# Notes\n\nFirst.\n\n# Notes\n\nSecond.\n\n# Notes\n\nThird.\n"

LANGUAGE = ["--language", "zh-hans"]


class ConcurrentFake(FakeTranslator):
    """The deterministic translator, plus a high-water mark of live calls.

    A request parks until a second one joins it, or until the timeout says
    no second one is coming. A serial run therefore still finishes -- it
    just records a high-water mark of one, and the assertion names that.
    """

    instances = []
    fail_after = None

    MEET_TIMEOUT = 2.0

    _lock = threading.Lock()
    _live = 0
    max_live = 0
    _met = threading.Event()

    @classmethod
    def reset(cls, meet_timeout=2.0):
        """`meet_timeout=0` for the one-worker baseline, which has nobody to
        meet and no reason to wait for the answer."""
        cls._live = 0
        cls.max_live = 0
        cls._met = threading.Event()
        cls.MEET_TIMEOUT = meet_timeout

    def translate_list(self, texts):
        cls = type(self)
        with cls._lock:
            cls._live += 1
            cls.max_live = max(cls.max_live, cls._live)
            if cls._live >= 2:
                cls._met.set()
        try:
            cls._met.wait(cls.MEET_TIMEOUT)
            return [self.translate(text) for text in texts]
        finally:
            with cls._lock:
                cls._live -= 1


class ContextFake(ConcurrentFake):
    """Same, for a route whose window a worker's clone can carry."""

    instances = []
    fail_after = None
    SUPPORTS_PARALLEL_CONTEXT = True


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def plain_format(monkeypatch):
    ConcurrentFake.reset()
    return register_fake_format(monkeypatch, "parallelfake", ConcurrentFake)


@pytest.fixture
def context_format(monkeypatch):
    ContextFake.reset()
    return register_fake_format(monkeypatch, "parallelctxfake", ContextFake)


def prepared(tmp_path, pandoc, name, text=BOOK):
    """A bundle holding `text` as its extracted source, ready to translate."""
    book = write_fixture(tmp_path / f"src-{name}", text)
    bundle = Bundle(tmp_path / f"bundle-{name}").create()
    import_markdown(bundle, book, pandoc=pandoc)
    return bundle


def options(fmt, *extra):
    return ["--api_format", fmt, *LANGUAGE, *extra]


def heading_ids(pandoc, text):
    return [
        block["c"][1][0]
        for block in parse_markdown(pandoc, text)["blocks"]
        if block["t"] == "Header"
    ]


def nav_targets(epub):
    """The identifiers the EPUB's table of contents points at, in order."""
    with zipfile.ZipFile(epub) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
    toc = re.search(r'<nav[^>]*epub:type="toc".*?</nav>', nav, re.S).group(0)
    return [
        href.split("#", 1)[1]
        for href in re.findall(r'href="([^"]+)"', toc)
        if "#" in href
    ]


def record_of(bundle):
    return json.loads(
        bundle.work_file("translate.result.json").read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------
# The plain parallel path: one worker per pending batch
# --------------------------------------------------------------------------
def test_parallel_workers_render_the_same_book_as_one_worker(
    tmp_path, pandoc, plain_format
):
    ConcurrentFake.reset(meet_timeout=0)
    serial = prepared(tmp_path, pandoc, "serial")
    translate_bundle(serial, options(plain_format), pandoc=pandoc)
    expected = serial.bilingual_markdown.read_text(encoding="utf-8")

    ConcurrentFake.reset()
    parallel = prepared(tmp_path, pandoc, "parallel")
    translate_bundle(
        parallel,
        options(plain_format, "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    produced = parallel.bilingual_markdown.read_text(encoding="utf-8")

    assert ConcurrentFake.max_live >= 2, "the batches were translated serially"
    # Byte-for-byte: pairing, order, identifiers and the credit all at once.
    assert produced == expected


def test_parallel_pairs_stay_next_to_their_own_source_in_order(
    tmp_path, pandoc, plain_format
):
    bundle = prepared(tmp_path, pandoc, "order")
    translate_bundle(
        bundle,
        options(plain_format, "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")

    assert ConcurrentFake.max_live >= 2, "the batches were translated serially"
    # Every source block, then its own translation, in reading order.
    assert re.findall(r"(?:Alpha|Beta|Gamma) paragraph (\w+)", text) == [
        "one",
        "one",
        "two",
        "two",
        "three",
        "three",
        "four",
        "four",
        "five",
        "five",
        "six",
        "six",
    ]
    for word in ("one", "two", "three", "four", "five", "six"):
        source = re.search(rf"^\w+ paragraph {word}.*$", text, re.M)
        translation = text.index(f"译:", source.end())
        # Nothing between a source block and its translation but the fence.
        assert (
            text[source.end() : translation].strip()
            == '::: {.bbm-translation lang="zh-hans"}'
        )


def test_parallel_headings_keep_pandocs_identifiers_and_the_nav(
    tmp_path, pandoc, plain_format
):
    bundle = prepared(tmp_path, pandoc, "nav")
    translate_bundle(
        bundle,
        options(plain_format, "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    ids = heading_ids(pandoc, text)

    assert ConcurrentFake.max_live >= 2, "the batches were translated serially"
    assert ids == ["alpha", "beta", "gamma"]
    assert len(set(ids)) == len(ids)
    # A translated heading is prose, never a second entry in the table of
    # contents: three source headings, three navigation targets.
    export_epub(bundle, pandoc=pandoc)
    assert nav_targets(bundle.epub) == ids
    assert len(HEADINGS) == len(ids)


def test_parallel_repeated_headings_still_get_distinct_identifiers(
    tmp_path, pandoc, plain_format
):
    bundle = prepared(tmp_path, pandoc, "repeat", REPEATED_HEADINGS)
    translate_bundle(
        bundle,
        options(plain_format, "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")

    assert heading_ids(pandoc, text) == ["notes", "notes-1", "notes-2"]
    export_epub(bundle, pandoc=pandoc)
    assert nav_targets(bundle.epub) == ["notes", "notes-1", "notes-2"]


def test_parallel_writes_one_completion_record_and_one_credit(
    tmp_path, pandoc, plain_format
):
    bundle = prepared(tmp_path, pandoc, "record")
    translate_bundle(
        bundle,
        options(plain_format, "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    record = record_of(bundle)

    assert ConcurrentFake.max_live >= 2, "the batches were translated serially"
    assert record["completed"] is True
    assert record["untranslated_batches"] == 0
    # Nine source blocks: three headings and six paragraphs, each counted
    # once however many workers carried them.
    assert record["pairs"] == 9
    assert record["output_sha256"] == sha256_file(bundle.bilingual_markdown)
    # The stamp belongs to the finished book, not to a worker.
    assert text.count("bbm-translation-credit") == 1


# --------------------------------------------------------------------------
# `--use_context`: whole sections in parallel, on cloned windows
# --------------------------------------------------------------------------
def test_parallel_sections_with_context_render_the_serial_book(
    tmp_path, pandoc, context_format
):
    ContextFake.reset(meet_timeout=0)
    serial = prepared(tmp_path, pandoc, "ctx-serial")
    translate_bundle(serial, options(context_format, "--use_context"), pandoc=pandoc)
    expected = serial.bilingual_markdown.read_text(encoding="utf-8")

    ContextFake.reset()
    parallel = prepared(tmp_path, pandoc, "ctx-parallel")
    translate_bundle(
        parallel,
        options(context_format, "--use_context", "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    produced = parallel.bilingual_markdown.read_text(encoding="utf-8")

    assert ContextFake.max_live >= 2, "the sections were translated serially"
    assert produced == expected


def test_parallel_sections_with_context_keep_identifiers_and_the_record(
    tmp_path, pandoc, context_format
):
    bundle = prepared(tmp_path, pandoc, "ctx-nav")
    translate_bundle(
        bundle,
        options(context_format, "--use_context", "--parallel-workers", str(WORKERS)),
        pandoc=pandoc,
    )
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    record = record_of(bundle)

    assert ContextFake.max_live >= 2, "the sections were translated serially"
    assert heading_ids(pandoc, text) == ["alpha", "beta", "gamma"]
    assert record["pairs"] == 9
    assert record["untranslated_batches"] == 0
    assert text.count("bbm-translation-credit") == 1
    export_epub(bundle, pandoc=pandoc)
    assert nav_targets(bundle.epub) == ["alpha", "beta", "gamma"]


# --------------------------------------------------------------------------
# Forwarding, and the one pair the route must refuse before it costs anything
# --------------------------------------------------------------------------
def test_the_pdf_route_forwards_the_worker_count_to_the_inner_run():
    typed = [
        "--book_name",
        "paper.pdf",
        "--to-epub",
        "--no-gpu",
        "--parallel-workers",
        "3",
        *LANGUAGE,
    ]
    assert to_epub.translation_argv(typed) == [
        "--parallel-workers",
        "3",
        *LANGUAGE,
    ]


def test_session_and_workers_are_refused_before_the_pdf_is_extracted(
    tmp_path, monkeypatch
):
    # PIN (lead, 260920): README's own --to-epub advice is "--use_context
    # session for a paper", and the CLI refuses that together with
    # --parallel-workers. The inner run only reaches its own refusal after
    # the extraction has been paid for, and the harness can then only report
    # "stopped before finishing (exit 1)". Decided: the harness refuses the
    # pair in check_options, in the CLI's own words, before any stage runs.
    # Source: book_maker/cli.py PARALLEL_SESSION_REFUSAL.
    from book_maker.cli import PARALLEL_SESSION_REFUSAL

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")

    def prepare_stage(
        bundle, source, *, pandoc, device=None, formula_images=True, progress=True
    ):
        pytest.fail("the PDF was extracted before the options were refused")

    with pytest.raises(PipelineError) as err:
        to_epub.pdf_to_epub(
            pdf,
            [
                "--book_name",
                str(pdf),
                "--to-epub",
                "--api_format",
                "google",
                *LANGUAGE,
                "--use_context",
                "session",
                "--parallel-workers",
                "3",
            ],
            prepare_stage=prepare_stage,
        )

    assert err.value.detail == PARALLEL_SESSION_REFUSAL
    assert not to_epub.bundle_path(pdf).exists()


def test_workers_without_a_session_are_not_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    from book_maker.pipeline.translate import check_options

    kept = check_options(
        ["--api_format", "google", *LANGUAGE, "--parallel-workers", "3"]
    )
    assert "--parallel-workers" in kept
