"""`--glossary` and `--glossary-auto` on the Markdown reading edition.

The PDF route (`make_book.py --book_name x.pdf --to-epub`) extracts the PDF
to Markdown and then translates *that* Markdown through this same CLI, with
a `MarkdownBookLoader` subclass substituted. Three things have to hold for a
pinned term to survive the trip, and each fails silently:

1. the route forwards the two flags instead of eating them with its own;
2. the loader the route substitutes still hands the parsed glossary and the
   auto switch to the translator it builds -- the bundle subclass changes
   where files go, not what the model is asked;
3. the bundle's identity -- what decides whether a finished translation is
   reused or bought again -- follows the glossary's *contents*, and not the
   spelling of the flag that named the file.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import pandoc_or_skip, write_fixture  # noqa: E402

from book_maker import cli  # noqa: E402
from book_maker.glossary import Glossary  # noqa: E402
from book_maker.pipeline import to_epub  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.importer import import_markdown  # noqa: E402
from book_maker.pipeline.reading_edition import (  # noqa: E402
    reading_edition_loader_class,
)
from book_maker.pipeline.translate import (  # noqa: E402
    translate_bundle,
    translation_fingerprint,
)

PINS = "harness → 执行框架\nauto-research loop → 自动研究循环\n"
HARNESS = "执行框架"
LOOP = "自动研究循环"

SOURCE = """# The harness

The harness drives an auto-research loop.

Nothing pinned is named here.
"""


class GlossaryProbe:
    """A translator that answers, and records the block it would have sent.

    The pinned block is assembled by the translator from the `Glossary` the
    loader handed it, so recording it per request is what proves the pins
    reached the model rather than merely being parsed by the CLI.
    """

    SUPPORTS_REQUEST_EXTRAS = False
    SUPPORTS_BATCH_API = False
    SUPPORTS_SESSION_CONTEXT = True
    SUPPORTS_STRUCTURED_OUTPUT = False
    SUPPORTS_GLOSSARY = True

    instances = []

    def __init__(self, key, language, api_base=None, **kwargs):
        self.language = language
        self.model_name = "glossary-probe"
        self.kwargs = kwargs
        self.glossary = kwargs.get("glossary")
        self.sent = []
        type(self).instances.append(self)

    def translate(self, text):
        block = self.glossary.prompt_block(text) if self.glossary else ""
        self.sent.append((text, block))
        return "译:" + text.strip().lstrip("#").strip()

    def translate_list(self, texts):
        return [self.translate(text) for text in texts]

    def set_interval(self, interval):
        pass


@pytest.fixture
def probe_format(monkeypatch):
    from book_maker.translator import FORMAT_DICT

    monkeypatch.setitem(FORMAT_DICT, "glossaryprobe", GlossaryProbe)
    monkeypatch.setitem(cli.FORMAT_DICT, "glossaryprobe", GlossaryProbe)
    GlossaryProbe.instances = []
    return "glossaryprobe"


@pytest.fixture
def glossary_file(tmp_path):
    path = tmp_path / "pins.txt"
    path.write_text(PINS, encoding="utf-8")
    return path


def bundle_with_source(tmp_path, text=SOURCE):
    bundle = Bundle(tmp_path / "bundle").create()
    bundle.source.write_text(text, encoding="utf-8")
    return bundle


def run_reading_edition(bundle, argv):
    loader = reading_edition_loader_class(
        bundle, language_tag="zh-Hans", heading_ids=("the-harness",), pandoc=None
    )
    cli.main(["--book_name", str(bundle.source), *argv], markdown_loader_class=loader)
    return bundle


# --------------------------------------------------------------------------
# What the route forwards
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv,kept",
    [
        (
            ["--to-epub", "--glossary", "pins.txt"],
            ["--glossary", "pins.txt"],
        ),
        (
            ["--book_name", "b.pdf", "--terminology", "pins.txt"],
            ["--terminology", "pins.txt"],
        ),
        (
            ["--no-gpu", "--glossary-auto", "on", "--use_context", "session"],
            ["--glossary-auto", "on", "--use_context", "session"],
        ),
    ],
)
def test_the_route_forwards_the_glossary_flags_to_the_inner_run(argv, kept):
    assert to_epub.translation_argv(argv) == kept


def test_the_translate_stage_is_handed_both_flags(tmp_path, monkeypatch):
    """`--to-epub` end to end, with the three stages recorded, no PDF parser."""
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    pins = tmp_path / "pins.txt"
    pins.write_text(PINS, encoding="utf-8")
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    handed = []

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
        bundle.source.write_text(SOURCE, encoding="utf-8")

    def translate_stage(bundle, options, *, pandoc):
        handed.append(list(options))
        bundle.bilingual_markdown.write_text("# The harness\n", encoding="utf-8")

    def export_stage(bundle, *, pandoc):
        bundle.epub.write_bytes(b"PK\x03\x04")
        return bundle.epub

    to_epub.pdf_to_epub(
        pdf,
        [
            "--book_name",
            str(pdf),
            "--to-epub",
            "--no-gpu",
            "--api_format",
            "google",
            "--language",
            "zh-hans",
            "--glossary",
            str(pins),
            "--glossary-auto",
            "on",
            "--use_context",
            "session",
        ],
        device="cpu",
        prepare_stage=prepare_stage,
        translate_stage=translate_stage,
        export_stage=export_stage,
    )
    assert handed == [
        [
            "--api_format",
            "google",
            "--language",
            "zh-hans",
            "--glossary",
            str(pins),
            "--glossary-auto",
            "on",
            "--use_context",
            "session",
        ]
    ]


# --------------------------------------------------------------------------
# What the reading edition's translator is given
# --------------------------------------------------------------------------
class TestThePinsReachTheModel:
    def test_the_reading_edition_translator_is_built_with_the_glossary(
        self, tmp_path, probe_format, glossary_file
    ):
        bundle = run_reading_edition(
            bundle_with_source(tmp_path),
            [
                "--api_format",
                probe_format,
                "--language",
                "zh-hans",
                "--glossary",
                str(glossary_file),
            ],
        )
        translator = GlossaryProbe.instances[0]
        assert translator.kwargs["glossary"] == Glossary.parse(PINS)
        # the bilingual file is still the reading edition's own layout
        assert "{#the-harness}" in bundle.bilingual_markdown.read_text(encoding="utf-8")

    def test_the_block_rides_with_the_units_that_name_a_term(
        self, tmp_path, probe_format, glossary_file
    ):
        run_reading_edition(
            bundle_with_source(tmp_path),
            [
                "--api_format",
                probe_format,
                "--language",
                "zh-hans",
                "--glossary",
                str(glossary_file),
            ],
        )
        sent = dict(GlossaryProbe.instances[0].sent)
        heading = sent["# The harness"]
        both = sent["The harness drives an auto-research loop."]
        neither = sent["Nothing pinned is named here."]
        assert HARNESS in heading and LOOP not in heading
        assert HARNESS in both and LOOP in both
        assert neither == ""

    def test_glossary_auto_on_reaches_the_translator_of_a_session_run(
        self, tmp_path, probe_format, glossary_file
    ):
        run_reading_edition(
            bundle_with_source(tmp_path),
            [
                "--api_format",
                probe_format,
                "--language",
                "zh-hans",
                "--glossary",
                str(glossary_file),
                "--glossary-auto",
                "on",
                "--use_context",
                "session",
            ],
        )
        translator = GlossaryProbe.instances[0]
        assert translator.kwargs["glossary_auto"] is True
        # the handoff the learned renderings ride in belongs to the bundle,
        # beside the Markdown the run actually translated
        assert Path(translator.kwargs["handoff_path"]).parent == (tmp_path / "bundle")

    def test_a_run_without_the_flags_gets_neither(
        self, tmp_path, probe_format, glossary_file
    ):
        run_reading_edition(
            bundle_with_source(tmp_path),
            ["--api_format", probe_format, "--language", "zh-hans"],
        )
        translator = GlossaryProbe.instances[0]
        assert not translator.kwargs["glossary"]
        # `--glossary-auto` is off unless asked for, and the flag's absence
        # reaches the translator as that answer rather than as "not typed".
        assert translator.kwargs["glossary_auto"] is False
        assert [block for _, block in translator.sent] == ["", "", ""]


# --------------------------------------------------------------------------
# What the bundle's identity is made of
# --------------------------------------------------------------------------
class TestTheBundleIdentityFollowsTheFileNotTheFlag:
    # PIN (lead, 260920): `--glossary` and `--terminology` are one flag under
    # two names (tests/test_glossary_wiring.py::TestTheTwoSpellings). Before
    # this, the typed spelling reached the bundle fingerprint through the
    # namespace, so the same run under the other word was a different
    # translation: a finished bundle was translated again, and an
    # interrupted one was refused with "settings changed".
    def test_the_two_spellings_are_the_same_translation(self, tmp_path):
        bundle = bundle_with_source(tmp_path)
        pins = tmp_path / "pins.txt"
        pins.write_text(PINS, encoding="utf-8")
        base = ["--api_format", "google", "--language", "zh-hans"]
        assert translation_fingerprint(
            bundle, base + ["--glossary", str(pins)]
        ) == translation_fingerprint(bundle, base + ["--terminology", str(pins)])

    def test_editing_the_glossary_is_a_different_translation(self, tmp_path):
        bundle = bundle_with_source(tmp_path)
        pins = tmp_path / "pins.txt"
        pins.write_text(PINS, encoding="utf-8")
        base = ["--api_format", "google", "--language", "zh-hans"]
        before = translation_fingerprint(bundle, base + ["--glossary", str(pins)])
        pins.write_text(PINS.replace(HARNESS, "框架"), encoding="utf-8")
        assert translation_fingerprint(bundle, base + ["--glossary", str(pins)]) != (
            before
        )

    def test_a_finished_bundle_is_not_bought_again_under_the_other_word(
        self, tmp_path, probe_format, glossary_file
    ):
        """The reuse path itself, over a real bundle and a real Pandoc."""
        pandoc = pandoc_or_skip()
        book = write_fixture(tmp_path / "src", SOURCE)
        bundle = Bundle(tmp_path / "bundle").create()
        import_markdown(bundle, book, pandoc=pandoc)
        options = ["--api_format", probe_format, "--language", "zh-hans"]

        translate_bundle(
            bundle, options + ["--glossary", str(glossary_file)], pandoc=pandoc
        )
        first = len(GlossaryProbe.instances)
        assert first == 1

        translate_bundle(
            bundle, options + ["--terminology", str(glossary_file)], pandoc=pandoc
        )
        # No second translator was built at all: the finished translation was
        # recognized as this run's and reused.
        assert len(GlossaryProbe.instances) == first

        # ... while a glossary whose *contents* changed is a new translation.
        glossary_file.write_text(PINS.replace(HARNESS, "框架"), encoding="utf-8")
        translate_bundle(
            bundle, options + ["--glossary", str(glossary_file)], pandoc=pandoc
        )
        assert len(GlossaryProbe.instances) == first + 1
