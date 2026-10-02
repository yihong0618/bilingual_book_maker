"""The PDF/Markdown -> bilingual Markdown + EPUB bundle, end to end.

Real temporary bundles and the real Pandoc: the point of these tests is
that the artifacts are the ones a reader would open, so nothing about the
Markdown parsing or the EPUB packaging is simulated. Only the model is
fixed, because a translation has to be deterministic to be asserted.
"""

import importlib.util
import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    PNG,
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_fixture,
)

from book_maker.pipeline.bundle import (
    TRANSLATE_STATE,
    Bundle,
    sha256_file,
)  # noqa: E402
from book_maker.pipeline.epub_export import (  # noqa: E402
    FRONT_MATTER_MAX_CHARS,
    export_epub,
)
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.importer import import_markdown  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    BILINGUAL_EDITED_EXPORT,
    PANDOC_REQUIRED,
    SETTINGS_CHANGED,
)
from book_maker.pipeline.preflight import parse_markdown  # noqa: E402
from book_maker.pipeline.translate import check_options, translate_bundle  # noqa: E402

HARNESS = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"


def load_harness():
    spec = importlib.util.spec_from_file_location("pdf_to_book", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def fake_format(monkeypatch):
    return register_fake_format(monkeypatch)


OPTIONS = ["--api_format", "faketest", "--language", "zh-hans"]


def prepared(tmp_path, pandoc, text=None):
    from pipeline_helpers import FIXTURE

    book = write_fixture(tmp_path / "src", text if text is not None else FIXTURE)
    bundle = Bundle(tmp_path / "bundle").create()
    import_markdown(bundle, book, pandoc=pandoc)
    return bundle


def blocks(pandoc, text):
    return parse_markdown(pandoc, text)["blocks"]


def block_kinds(blocks_):
    return [block["t"] for block in blocks_]


def translation_divs(blocks_):
    return [
        block
        for block in blocks_
        if block["t"] == "Div" and "bbm-translation" in block["c"][0][1]
    ]


# --------------------------------------------------------------------------
# The deliverable
# --------------------------------------------------------------------------
def test_supported_structures_survive_into_markdown_and_epub(
    tmp_path, pandoc, fake_format
):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)

    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    parsed = blocks(pandoc, text)
    kinds = block_kinds(parsed)

    # Every pass-through structure is there exactly once.
    assert kinds.count("Table") == 1
    assert kinds.count("CodeBlock") == 1
    assert text.count("![A plate]") == 1

    # One heading per source heading, each with its own identifier, and the
    # translated heading is not a heading.
    headers = [block for block in parsed if block["t"] == "Header"]
    assert len(headers) == 2
    identifiers = [block["c"][1][0] for block in headers]
    assert identifiers == ["chapter-one", "notes"]
    assert len(set(identifiers)) == len(identifiers)

    # Source and translation are adjacent, separate parsed blocks -- never
    # one merged paragraph.
    for index, block in enumerate(parsed):
        if block["t"] in ("Header", "Para", "BulletList"):
            if index + 1 < len(parsed) and parsed[index + 1]["t"] == "Div":
                assert "bbm-translation" in parsed[index + 1]["c"][0][1]
    divs = translation_divs(parsed)
    assert len(divs) == 6
    # The multi-paragraph translation stayed two paragraphs and did not
    # shift the pairs after it.
    assert any(len(div["c"][1]) == 2 for div in divs)
    assert all(div["c"][0][2] == [["lang", "zh-hans"]] for div in divs)

    # The credit line survives the Markdown path into the book.
    assert "Translated by fake-test-model" in text

    with zipfile.ZipFile(bundle.epub) as archive:
        names = archive.namelist()
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert nav.count('<a href="text/ch') == 2  # one TOC entry per heading
        assert "#chapter-one" in nav and "#notes" in nav
        documents = [n for n in names if n.endswith(".xhtml")]
        body = "".join(archive.read(name).decode("utf-8") for name in documents)
        assert body.count('<div class="bbm-translation"') == 6
        assert body.count("<img") == 1
        assert body.count("<table>") == 1
        assert "never translated" in body
        assert len([n for n in names if n.startswith("EPUB/media/")]) == 1


def test_export_only_needs_no_translator_and_no_extraction(
    tmp_path, pandoc, fake_format, monkeypatch
):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    bundle.epub.unlink(missing_ok=True)

    def refuse(*args, **kwargs):
        raise AssertionError("export must not translate or extract")

    monkeypatch.setattr(FakeTranslator, "__init__", refuse)
    monkeypatch.setattr("book_maker.pipeline.docling_parser.extract_pdf", refuse)
    monkeypatch.setattr("book_maker.cli.main", refuse)

    export_epub(bundle, pandoc=pandoc)
    assert bundle.epub.is_file()


def test_edited_bilingual_markdown_is_exported_without_retranslation(
    tmp_path, pandoc, fake_format, monkeypatch
):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    edited = bundle.bilingual_markdown.read_text(encoding="utf-8").replace(
        "译:第一段。", "译:改过的第一段。"
    )
    bundle.bilingual_markdown.write_text(edited, encoding="utf-8")

    monkeypatch.setattr(
        "book_maker.cli.main",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no retranslation")),
    )
    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml")
        )
    assert "译:改过的第一段。" in body

    # A later translate run must not quietly overwrite that edit.
    with pytest.raises(PipelineError) as refused:
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    assert refused.value.detail == BILINGUAL_EDITED_EXPORT.format(bundle=bundle.root)
    assert "译:改过的第一段。" in bundle.bilingual_markdown.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Refusals: none of these may look like a finished book
# --------------------------------------------------------------------------
BROKEN = {
    "missing image": "# T\n\ntext\n\n![gone](assets/absent.png)\n",
    "escaping path": "# T\n\ntext\n\n![out](../../etc/hosts)\n",
    "remote image": "# T\n\ntext\n\n![remote](https://example.com/a.png)\n",
    "html image": '# T\n\ntext\n\n<img src="assets/plate.png" />\n',
    "reference image": "# T\n\ntext\n\n![plate][ref]\n\n[ref]: assets/plate.png\n",
    "malformed markup": "# T\n\n<table><tr><td>unclosed\n\ntext\n",
    "dangling anchor": "# T\n\nSee [later](#nowhere).\n",
}


@pytest.mark.parametrize("label", sorted(BROKEN))
def test_unsupported_or_broken_markdown_is_refused_at_import(tmp_path, pandoc, label):
    book = write_fixture(tmp_path / "src", BROKEN[label])
    bundle = Bundle(tmp_path / "bundle").create()
    with pytest.raises(PipelineError):
        import_markdown(bundle, book, pandoc=pandoc)
    assert bundle.stage_status("import") == "failed"
    assert not bundle.bilingual_markdown.exists()
    assert not bundle.epub.exists()


PRESERVED_FIXTURE = """# Chapter One

The mass energy $E = mc^2$ relation is stated in prose, and it costs $5 and
$10 and $5-$10 in ordinary currency.

$$\\int_0^1 x\\,dx = \\frac{1}{2}$$

Prose with a note.[^note] and more prose after it.

<table>
<tr><th>a</th><th>b</th></tr>
<tr><td>1</td><td>2</td></tr>
</table>

[^note]: The definition of the note.
"""


def test_math_tables_and_footnotes_are_preserved_once_and_not_translated(
    tmp_path, pandoc, fake_format, capsys
):
    """Carried through, in the source language, exactly once each.

    The prose around them is still translated, the currency amounts are
    still prose, and nothing in this fixture reaches the model as a
    formula, a table cell or a note definition.
    """
    bundle = prepared(tmp_path, pandoc, PRESERVED_FIXTURE)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")

    # An inline formula belongs to the sentence it is in, so it stands in
    # both the source sentence and its translation -- the same policy links
    # and code spans already have. Display math is a block of its own and is
    # carried once, like a table: pairing it would print the formula twice
    # with nothing else to read.
    assert text.count("E = mc^2") == 2
    assert text.count("\\int_0^1") == 1
    assert text.count("<table>") == 1
    assert text.count("[^note]: The definition of the note.") == 1
    # One reference only: two would make Pandoc print the definition twice.
    assert len(re.findall(r"\[\^note\](?!:)", text)) == 1

    sent = [t for i in FakeTranslator.instances for t in i.translated]
    joined = "\n".join(sent)
    assert "E = mc^2" not in joined
    assert "<table>" not in joined
    assert "The definition of the note." not in joined
    assert "\\int_0^1" not in joined
    # Currency is prose, here and in Pandoc: it must not have been eaten.
    assert any("$5 and" in t for t in sent)

    parsed = blocks(pandoc, text)
    assert block_kinds(parsed).count("RawBlock") == 1
    assert translation_divs(parsed), "prose around the structures was not translated"

    out = capsys.readouterr().out
    assert "Preserved without translation: math (2)." in out
    assert "Preserved without translation: HTML tables (1)." in out
    assert "Preserved without translation: footnotes (1)." in out

    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml")
        )
    assert body.count("<table>") == 1
    assert body.count("The definition of the note.") == 1


def test_repeated_headings_get_distinct_identifiers(tmp_path, pandoc, fake_format):
    text = "# Notes\n\nFirst.\n\n# Notes\n\nSecond.\n"
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    parsed = blocks(pandoc, bundle.bilingual_markdown.read_text(encoding="utf-8"))
    identifiers = [b["c"][1][0] for b in parsed if b["t"] == "Header"]
    # Pandoc's own duplicate suffix, so a link written against the source
    # still resolves in the bilingual file.
    assert identifiers == ["notes", "notes-1"]
    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
    assert "#notes" in nav and "#notes-1" in nav


def test_front_matter_above_the_first_heading_does_not_break_the_nav(
    tmp_path, pandoc, fake_format
):
    # PIN (lead, 260920, arXiv 2609.20519 run): OpenDataLoader put the logo
    # and the affiliation line above the paper's title. Pandoc turned that
    # into an untitled chapter labelled with the book title in the nav and
    # linked to the wrong file (27 entries for 26 headings); the export was
    # refused. Front matter is moved below the first heading for the
    # conversion; the bilingual file keeps the extractor's order.
    text = "<!-- page 1 -->\n\nNVIDIA\n\n# Title\n\nProse.\n"
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    before = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert before.index("NVIDIA") < before.index("# Title")

    export_epub(bundle, pandoc=pandoc)

    assert bundle.bilingual_markdown.read_text(encoding="utf-8") == before
    with zipfile.ZipFile(bundle.epub) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml") and "nav" not in name
        )
    toc = re.search(r'<nav[^>]*epub:type="toc".*?</nav>', nav, re.S).group(0)
    assert toc.count("<a href=") == 1
    assert "NVIDIA" in body and "译:NVIDIA" in body


def test_a_banner_s_translation_is_judged_with_its_banner_not_by_its_length(
    tmp_path, pandoc, fake_format
):
    # PIN (lead, 260921, docs/260921-feat-PDF_OCR_LANG_FLAG.md): the OCR
    # lost a scanned book's first heading, three short Chinese lines sat
    # above the first one kept, and the export refused the nav because one
    # line's *English* ran past eighty characters. A translation block goes
    # with the line it translates; only the source line is measured.
    banner = "A" * FRONT_MATTER_MAX_CHARS  # front matter; its translation is longer
    text = f"<!-- page 1 -->\n\n{banner}\n\n# Title\n\nProse.\n"
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    translated = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert f"译:{banner}" in translated  # 82 characters, past the banner limit

    export_epub(bundle, pandoc=pandoc)

    with zipfile.ZipFile(bundle.epub) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
    toc = re.search(r'<nav[^>]*epub:type="toc".*?</nav>', nav, re.S).group(0)
    assert toc.count("<a href=") == 1


def test_prose_above_the_first_heading_is_not_front_matter(
    tmp_path, pandoc, fake_format
):
    # An untitled preface is left where it is, and the export says why it
    # cannot build a navigation for it, rather than silently reordering it.
    preface = "This is an untitled preface that runs well past eighty characters, so it is prose."
    text = f"{preface}\n\n# Title\n\nProse.\n"
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    with pytest.raises(PipelineError) as refused:
        export_epub(bundle, pandoc=pandoc)
    assert "navigation is invalid" in refused.value.detail


def test_failed_export_leaves_the_previous_epub_in_place(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)
    before = sha256_file(bundle.epub)

    broken = bundle.bilingual_markdown.read_text(encoding="utf-8") + (
        "\n![gone](assets/absent.png)\n"
    )
    bundle.bilingual_markdown.write_text(broken, encoding="utf-8")
    with pytest.raises(PipelineError) as failed:
        export_epub(bundle, pandoc=pandoc)
    assert "missing image" in failed.value.detail
    assert sha256_file(bundle.epub) == before
    assert not bundle.work_file("book_bilingual.epub.part").exists()


def test_export_fails_when_pandoc_itself_fails(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)
    before = sha256_file(bundle.epub)

    failing = tmp_path / "pandoc-that-fails"
    failing.write_text("#!/bin/sh\nexec 1>&2; echo 'boom'; exit 3\n")
    failing.chmod(0o755)
    with pytest.raises(PipelineError) as failed:
        export_epub(bundle, pandoc=str(failing))
    assert "exited 3" in failed.value.detail
    assert sha256_file(bundle.epub) == before


def test_missing_pandoc_stops_the_run_before_any_translation(
    tmp_path, pandoc, fake_format, capsys
):
    write_fixture(tmp_path / "src")
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            str(tmp_path / "no-such-pandoc"),
            "run",
            str(tmp_path / "src" / "book.md"),
            "--output",
            str(tmp_path / "bundle"),
            "--",
            *OPTIONS,
        ]
    )
    assert code == 1
    assert PANDOC_REQUIRED in capsys.readouterr().out
    assert FakeTranslator.instances == []
    assert not (tmp_path / "bundle" / "book_bilingual.md").exists()


# --------------------------------------------------------------------------
# State: resume, invalidation, completion
# --------------------------------------------------------------------------
def test_resume_translates_only_the_pending_blocks(tmp_path, pandoc, fake_format):
    # One block per batch, so the interruption lands on a batch boundary and
    # the assertion is about resume rather than about batch granularity.
    options = OPTIONS + ["--batch_size", "1"]
    bundle = prepared(tmp_path, pandoc)
    FakeTranslator.fail_after = 2

    with pytest.raises(PipelineError) as stopped:
        translate_bundle(bundle, options, pandoc=pandoc)
    # The interrupt path exits zero; the stage must still be a failure.
    assert "stopped before finishing" in stopped.value.detail
    assert bundle.stage_status("translate") == "failed"
    assert bundle.work_file(TRANSLATE_STATE).is_file()
    assert not bundle.bilingual_markdown.exists()
    first = [text for i in FakeTranslator.instances for text in i.translated]

    FakeTranslator.fail_after = None
    FakeTranslator.instances = []
    translate_bundle(bundle, options, pandoc=pandoc)

    second = [text for i in FakeTranslator.instances for text in i.translated]
    assert second, "the resumed run translated nothing"
    assert not set(first) & set(second), "already-translated blocks were repaid"
    body = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert body.count("译:Chapter One") == 1
    assert bundle.stage_status("translate") == "completed"


def test_a_completed_translation_is_reused_on_rerun(
    tmp_path, pandoc, fake_format, capsys
):
    # PIN (lead, 260920, Codex review of feat/pdf-cli-flags): rerunning the
    # same command over a finished bundle -- after a failed export, or to
    # rebuild the EPUB -- must not buy the same translation twice.
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    written = bundle.bilingual_markdown.read_bytes()

    FakeTranslator.instances = []
    result = translate_bundle(bundle, OPTIONS, pandoc=pandoc)

    assert result == bundle.bilingual_markdown
    assert [t for i in FakeTranslator.instances for t in i.translated] == []
    assert bundle.bilingual_markdown.read_bytes() == written
    assert bundle.stage_status("translate") == "completed"
    assert "Translation reused" in capsys.readouterr().out


def test_deleting_the_bilingual_file_translates_again(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    bundle.bilingual_markdown.unlink()

    FakeTranslator.instances = []
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    assert [t for i in FakeTranslator.instances for t in i.translated]
    assert bundle.bilingual_markdown.is_file()


@pytest.mark.parametrize(
    "changed",
    [
        ["--api_format", "faketest", "--language", "ja"],
        ["--api_format", "faketest", "--language", "zh-hans", "--batch_size", "3"],
        ["--api_format", "faketest", "--language", "zh-hans", "--use_context"],
    ],
    ids=["target", "chunking", "context"],
)
def test_changed_settings_refuse_stale_translation_state(
    tmp_path, pandoc, fake_format, changed
):
    bundle = prepared(tmp_path, pandoc)
    FakeTranslator.fail_after = 2
    with pytest.raises(PipelineError):
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)

    FakeTranslator.fail_after = None
    with pytest.raises(PipelineError) as refused:
        translate_bundle(bundle, changed, pandoc=pandoc)
    assert refused.value.detail == SETTINGS_CHANGED


def test_edited_source_refuses_stale_translation_state(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    FakeTranslator.fail_after = 2
    with pytest.raises(PipelineError):
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)

    FakeTranslator.fail_after = None
    bundle.source.write_text(
        bundle.source.read_text(encoding="utf-8").replace("Chapter One", "Chapter Two"),
        encoding="utf-8",
    )
    with pytest.raises(PipelineError) as refused:
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    assert refused.value.detail == SETTINGS_CHANGED


def test_edited_prompt_file_refuses_stale_translation_state(
    tmp_path, pandoc, fake_format
):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Translate to {language}: {text}", encoding="utf-8")
    options = OPTIONS + ["--prompt", str(prompt)]
    bundle = prepared(tmp_path, pandoc)
    FakeTranslator.fail_after = 2
    with pytest.raises(PipelineError):
        translate_bundle(bundle, options, pandoc=pandoc)

    FakeTranslator.fail_after = None
    prompt.write_text("Render into {language}: {text}", encoding="utf-8")
    with pytest.raises(PipelineError) as refused:
        translate_bundle(bundle, options, pandoc=pandoc)
    assert refused.value.detail == SETTINGS_CHANGED


def test_completion_record_states_what_was_finished(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    record = json.loads(
        bundle.work_file("translate.result.json").read_text(encoding="utf-8")
    )
    assert record["completed"] is True
    assert record["untranslated_batches"] == 0
    assert record["pairs"] == 6
    assert record["output_sha256"] == sha256_file(bundle.bilingual_markdown)


# --------------------------------------------------------------------------
# The harness surface
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "option,tokens",
    [
        ("--book_name", ["--book_name", "other.md"]),
        ("--single_translate", ["--single_translate"]),
        ("--resume", ["--resume"]),
        ("--batch", ["--batch"]),
        ("--batch-use", ["--batch-use"]),
        ("--retranslate", ["--retranslate", "a", "b", "c", "d"]),
        ("--plan-dry-run", ["--plan-dry-run"]),
        ("--plan-classify", ["--plan-classify", "agent"]),
    ],
)
def test_harness_refuses_options_it_owns_or_cannot_deliver(option, tokens):
    with pytest.raises(PipelineError) as refused:
        check_options(tokens)
    assert option in refused.value.detail


def test_a_translation_command_line_that_does_not_parse_is_refused_early():
    """`run` must not pay for extraction and then fail on a typo."""
    with pytest.raises(PipelineError) as refused:
        check_options(["--no-such-flag", "1"])
    assert "--no-such-flag" in refused.value.detail


def test_harness_run_produces_both_deliverables(tmp_path, pandoc, fake_format, capsys):
    write_fixture(tmp_path / "src")
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "run",
            str(tmp_path / "src" / "book.md"),
            "--output",
            str(tmp_path / "bundle"),
            "--title",
            "A Small Book",
            "--",
            *OPTIONS,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0, out
    bundle = Bundle(tmp_path / "bundle")
    assert bundle.bilingual_markdown.is_file()
    assert bundle.epub.is_file()
    assert "Bilingual Markdown saved:" in out
    assert "Bilingual EPUB saved:" in out
    assert "Preserved without translation: tables (1)." in out
    with zipfile.ZipFile(bundle.epub) as archive:
        assert "A Small Book" in archive.read("EPUB/content.opf").decode("utf-8")


def test_harness_rejects_an_unknown_input_suffix(tmp_path, pandoc, capsys):
    (tmp_path / "book.docx").write_bytes(b"x")
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "import",
            str(tmp_path / "book.docx"),
            "--output",
            str(tmp_path / "b"),
        ]
    )
    assert code == 1
    assert "neither a PDF nor a Markdown file" in capsys.readouterr().out


def test_manifest_never_records_a_key(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS + ["--key", "sk-secret-value"], pandoc=pandoc)
    manifest = bundle.manifest_path.read_text(encoding="utf-8")
    assert "sk-secret-value" not in manifest
    assert '"secret:' in manifest


def test_sample_runs_are_visible_as_samples(tmp_path, pandoc, fake_format):
    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS + ["--test", "--test_num", "2"], pandoc=pandoc)
    assert bundle.read_manifest()["translation"]["sample"] is True
    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        assert "(sample)" in archive.read("EPUB/content.opf").decode("utf-8")


def test_image_paths_with_spaces_and_nested_directories_are_carried(
    tmp_path, pandoc, fake_format
):
    source = tmp_path / "src"
    (source / "figures" / "ch1").mkdir(parents=True)
    (source / "figures" / "ch1" / "plate one.png").write_bytes(PNG)
    book = source / "book.md"
    book.write_text(
        "# T\n\nProse.\n\n![one](<figures/ch1/plate one.png>)\n", encoding="utf-8"
    )
    bundle = Bundle(tmp_path / "bundle").create()
    import_markdown(bundle, book, pandoc=pandoc)
    assert (bundle.assets / "figures" / "ch1" / "plate one.png").is_file()
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        assert len([n for n in archive.namelist() if n.startswith("EPUB/media/")]) == 1


# --------------------------------------------------------------------------
# The CLI seam
# --------------------------------------------------------------------------
def test_the_seam_leaves_an_ordinary_markdown_run_exactly_as_it_was(
    tmp_path, fake_format
):
    """`main(argv)` with no loader class renders the old file, byte for byte.

    The reading-edition layout -- blank lines, heading ids, fenced divs --
    is opt-in. A plain `--book_name book.md` run must not acquire any of it.
    """
    from book_maker import cli

    book = tmp_path / "plain.md"
    book.write_text("# Title\n\nOne paragraph.\n", encoding="utf-8")
    cli.main(["--book_name", str(book), *OPTIONS])

    written = (tmp_path / "plain_bilingual.md").read_text(encoding="utf-8")
    assert written == "# Title\n# 译:Title\nOne paragraph.\n译:One paragraph."


def test_the_seam_substitutes_the_markdown_loader_without_patching_anything(
    tmp_path, fake_format
):
    from book_maker import cli
    from book_maker.loader.md_loader import MarkdownBookLoader
    from book_maker.pipeline.reading_edition import ReadingEditionMarkdownLoader

    built = []

    class Probe(ReadingEditionMarkdownLoader):
        def __init__(self, *args, **kwargs):
            built.append(self)
            super().__init__(*args, **kwargs)

    book = tmp_path / "plain.md"
    book.write_text("# Title\n\nOne paragraph.\n", encoding="utf-8")
    cli.main(["--book_name", str(book), *OPTIONS], markdown_loader_class=Probe)

    assert len(built) == 1
    written = (tmp_path / "plain_bilingual.md").read_text(encoding="utf-8")
    assert "# Title {#title}" in written
    # The class used on its own states no language: the bundle harness is
    # what supplies the CLI's normalized tag, and nothing invents one.
    assert "::: {.bbm-translation}" in written
    # The registry itself was never touched.
    from book_maker.loader import BOOK_LOADER_DICT

    assert BOOK_LOADER_DICT["md"] is MarkdownBookLoader


def test_an_html_image_inside_a_code_fence_is_not_mistaken_for_one(
    tmp_path, pandoc, fake_format
):
    """The raw-text checks must not refuse a book that documents HTML."""
    text = (
        "# Manual\n\nHow to embed a picture:\n\n"
        '```html\n<img src="logo.png" alt="logo" />\n```\n\n'
        "That is the whole trick.\n"
    )
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)
    assert '<img src="logo.png"' in bundle.bilingual_markdown.read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------
# What is allowed to become a published EPUB
# --------------------------------------------------------------------------
def make_epub(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


MINIMAL = {
    "mimetype": "application/epub+zip",
    "META-INF/container.xml": "<container/>",
    "EPUB/content.opf": "<package/>",
    "EPUB/text/ch001.xhtml": "<html><body><p>ok</p></body></html>",
}


@pytest.mark.parametrize(
    "damage,expected",
    [
        ({}, "no mimetype entry"),
        ({"mimetype": "application/zip"}, "mimetype is"),
        ({"META-INF/container.xml": None}, "no META-INF/container.xml"),
        ({"EPUB/text/ch001.xhtml": None}, "no content document"),
        (
            {"EPUB/text/ch001.xhtml": "<html><body><p>unclosed</body></html>"},
            "not well-formed XML",
        ),
        ({"EPUB/content.opf": None}, "no package document"),
    ],
    ids=[
        "no-mimetype",
        "wrong-mimetype",
        "no-container",
        "no-document",
        "bad-xhtml",
        "no-opf",
    ],
)
def test_a_damaged_package_is_never_published(tmp_path, damage, expected):
    from book_maker.pipeline.epub_export import validate_epub

    entries = dict(MINIMAL)
    if not damage:
        entries.pop("mimetype")
    for name, value in damage.items():
        if value is None:
            entries.pop(name)
        else:
            entries[name] = value
    candidate = make_epub(tmp_path / "candidate.epub", entries)
    with pytest.raises(PipelineError) as refused:
        validate_epub(candidate)
    assert expected in refused.value.detail


def test_a_file_that_is_not_a_zip_is_never_published(tmp_path):
    from book_maker.pipeline.epub_export import validate_epub

    candidate = tmp_path / "candidate.epub"
    candidate.write_bytes(b"this is not a zip archive")
    with pytest.raises(PipelineError) as refused:
        validate_epub(candidate)
    assert "unreadable EPUB" in refused.value.detail


def test_an_image_pandoc_could_not_read_is_a_failure_not_a_warning(
    tmp_path, pandoc, fake_format, monkeypatch
):
    """Pandoc reports an unreachable resource and still exits 0.

    Preflight normally catches this first; this guards the case where it
    cannot -- the check has to be on the run's output, not on its status.
    """
    from book_maker.pipeline import epub_export

    bundle = prepared(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)
    before = sha256_file(bundle.epub)

    real = epub_export.run_tool

    class Result:
        returncode = 0
        stdout = ""
        stderr = "[WARNING] Could not fetch resource assets/plate.png\n"

    def warn_instead(argv, stdin_text=None):
        if "-t" in argv and argv[argv.index("-t") + 1] == "epub3":
            real(argv, stdin_text)  # still writes the candidate file
            return Result()
        return real(argv, stdin_text)

    monkeypatch.setattr(epub_export, "run_tool", warn_instead)
    with pytest.raises(PipelineError) as failed:
        export_epub(bundle, pandoc=pandoc)
    assert "could not read a resource" in failed.value.detail
    assert sha256_file(bundle.epub) == before
    assert not bundle.work_file("book_bilingual.epub.part").exists()


def test_an_image_inside_prose_appears_once_and_keeps_its_sentence(
    tmp_path, pandoc, fake_format
):
    """Inline protection restores a span into both copies of a paragraph.

    For a link that is right; for a picture it means printing the picture
    twice. The reading edition drops it from the translated copy and keeps
    everything else the model returned.
    """
    source = tmp_path / "src"
    source.mkdir(parents=True)
    (source / "plate.png").write_bytes(PNG)
    book = source / "book.md"
    book.write_text(
        "# Chapter\n\n"
        "The diagram ![A plate](plate.png) sits inside this sentence and "
        "the sentence continues after it.\n",
        encoding="utf-8",
    )
    bundle = Bundle(tmp_path / "bundle").create()
    import_markdown(bundle, book, pandoc=pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)

    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert text.count("![A plate]") == 1
    divs = translation_divs(blocks(pandoc, text))
    assert len(divs) == 2  # the heading and the paragraph
    rendered = text.split("::: {.bbm-translation")[2]
    assert "译:" in rendered
    assert "the sentence continues after it" in rendered

    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml")
        )
        assert body.count("<img") == 1
        assert len([n for n in archive.namelist() if n.startswith("EPUB/media/")]) == 1


def test_an_internal_link_written_against_the_source_still_resolves(
    tmp_path, pandoc, fake_format
):
    text = (
        "# Book\n\nSee [the notes](#notes) and [the repeat](#notes-1).\n\n"
        "# Notes\n\nFirst note section.\n\n# Notes\n\nSecond note section.\n"
    )
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    # Export runs the same link check over the bilingual file: an anchor
    # that stopped resolving would be a refusal here, not a broken EPUB.
    export_epub(bundle, pandoc=pandoc)

    bilingual = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert "{#notes}" in bilingual and "{#notes-1}" in bilingual
    with zipfile.ZipFile(bundle.epub) as archive:
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml")
        )
    assert 'id="notes"' in body and 'id="notes-1"' in body
    assert "#notes-1" in body


class _NumberedTranslator(FakeTranslator):
    # A model that keeps the source's numbering, the way a real one does
    # for a paper's "1. Introduction".
    def _answer(self, text):
        stripped = text.strip()
        if stripped.startswith("#"):
            hashes, _, title = stripped.partition(" ")
            number, _, rest = title.partition(" ")
            return f"{hashes} {number} 译:{rest}"
        if stripped.startswith("A step"):
            return "(a) 译:" + stripped
        return f"译:{stripped}"


def test_a_numbered_translation_stays_prose(tmp_path, pandoc, monkeypatch):
    # PIN (lead, 260920, arXiv 2609.20519 run): "1. Introduction" came back
    # as "1. 引言", which Pandoc read as an ordered list inside the
    # translation div; the escaped marker keeps it a heading's prose. A
    # source block that is itself a list keeps its markers.
    from pipeline_helpers import register_fake_format

    register_fake_format(monkeypatch, cls=_NumberedTranslator)
    text = "# 1. Introduction\n\nA step.\n\n1. first\n2. second\n"
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    bilingual = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert "1\\. 译:Introduction" in bilingual
    assert "(a\\) 译:A step." in bilingual
    parsed = blocks(pandoc, bilingual)
    lists = [b for b in parsed if b["t"] == "OrderedList"]
    # the source's own list, translated, and nothing else
    assert len(lists) == 1
    divs = translation_divs(parsed)
    assert not any(
        inner["t"] == "OrderedList" for div in divs[:2] for inner in div["c"][1]
    )


# --------------------------------------------------------------------------
# Control characters and raw TeX: refused before the model is paid
# --------------------------------------------------------------------------
# PIN: lead 260925, skill field test, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md
# -- a control character (docling reads some glyphs as U+0000, U+0006,
# U+0019) or the `\u0000` escape a model writes back for one broke the
# EPUB export after the translation had been paid for. The check that
# refuses them runs on source.md before translation, on every run,
# including a source.md the operator edited after extraction.
@pytest.mark.parametrize(
    "inserted, named",
    [
        ("\x00", "control character U+0000"),
        ("\x06", "control character U+0006"),
        ("\x19", "control character U+0019"),
        ("\\u0000", "raw tex"),
    ],
)
def test_a_control_character_or_raw_tex_is_refused_before_translation(
    tmp_path, pandoc, fake_format, inserted, named
):
    bundle = prepared(tmp_path, pandoc)
    edited = bundle.source.read_text(encoding="utf-8").replace(
        "ordinary prose", f"ordinary {inserted} prose"
    )
    bundle.source.write_text(edited, encoding="utf-8")

    with pytest.raises(PipelineError) as refused:
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    assert named in refused.value.detail
    assert FakeTranslator.instances == []
    assert not bundle.bilingual_markdown.exists()


def test_a_control_character_inside_a_code_fence_is_refused_too(tmp_path, pandoc):
    """XML forbids it everywhere, so a fence does not make it safe."""
    text = "# T\n\ntext\n\n```\nbad \x01 byte\n```\n"
    book = write_fixture(tmp_path / "src", text)
    bundle = Bundle(tmp_path / "bundle").create()
    with pytest.raises(PipelineError) as refused:
        import_markdown(bundle, book, pandoc=pandoc)
    assert "control character U+0001 at line 6" in refused.value.detail


# PIN: lead 260925, skill field test, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md
# -- an OCR'd `## *习题5.22` (an emphasis opener that never closes) made
# Pandoc read the appended `{#习题5.22}` as heading text, and the EPUB
# showed it in the <h2> and in the contents. Every heading id we stamp
# must parse as the heading's attribute; legitimate emphasis stays.
def test_a_heading_with_a_stray_emphasis_marker_keeps_its_id_out_of_the_text(
    tmp_path, pandoc, fake_format
):
    text = (
        "# Book\n\nOpening prose.\n\n## *习题5.22\n\nAn exercise.\n\n"
        "## **Stray start\n\nMore.\n\n## Notes on *Hamlet*\n\nEnd.\n"
    )
    bundle = prepared(tmp_path, pandoc, text)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    bilingual = bundle.bilingual_markdown.read_text(encoding="utf-8")

    headers = [
        block
        for block in blocks(pandoc, bilingual)
        if block["t"] == "Header" and block["c"][0] == 2
    ]
    from book_maker.pipeline.preflight import plain_text

    shown = [plain_text(block["c"][2]) for block in headers]
    assert shown == ["*习题5.22", "**Stray start", "Notes on Hamlet"]
    assert all("{#" not in title for title in shown)
    # The emphasis a heading really has is kept as emphasis.
    assert any(inline["t"] == "Emph" for inline in headers[2]["c"][2])

    export_epub(bundle, pandoc=pandoc)
    with zipfile.ZipFile(bundle.epub) as archive:
        book = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith((".xhtml", ".ncx"))
        )
    assert "{#" not in book
    assert "*习题5.22" in book


# --------------------------------------------------------------------------
# Mending a translation by hand: the harness export rebuilds the bundle's own
# EPUB and writes nothing else; nothing is translated or recorded as
# translated. The `--to-epub` route's refusal names both steps, the export
# and the copy over the file beside the PDF.
# PIN (lead 260925, Codex review 01a0dc74,
# docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md): the export no longer
# copies anything beside the PDF (it could not tell the route's copy from an
# unrelated or edited file of the same name); no new main-CLI flag.
# --------------------------------------------------------------------------
EDIT_FROM = "译:第一段。"
EDIT_TO = "译:手工修订的第一段。"


def route_bundle(tmp_path, pandoc, pages=None):
    """A bundle as `--to-epub` leaves it: `<stem>[_pages-…]_book` beside
    `<stem>.pdf`, its manifest naming that PDF and its bytes."""
    from book_maker.pipeline.to_epub import bundle_path

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%not parsed by these tests\n")
    book = write_fixture(tmp_path / "src")
    bundle = Bundle(bundle_path(pdf, pages)).create()
    import_markdown(bundle, book, pandoc=pandoc, origin=pdf, kind="pdf")
    return pdf, bundle


def epub_text(path):
    with zipfile.ZipFile(path) as archive:
        return "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith((".xhtml", ".opf"))
        )


def edit(bundle):
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert EDIT_FROM in text
    bundle.bilingual_markdown.write_text(
        text.replace(EDIT_FROM, EDIT_TO), encoding="utf-8"
    )


@pytest.fixture
def nothing_translates(monkeypatch):
    """Any translator built, any translation run, any socket opened fails."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the export built a translator or went online")

    monkeypatch.setattr(FakeTranslator, "__init__", refuse)
    monkeypatch.setattr("book_maker.cli.main", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)


def provenance(bundle):
    manifest = bundle.read_manifest()
    return (
        manifest["translation"],
        manifest["outputs"]["bilingual_markdown"],
        manifest["stages"]["translate"],
    )


def outside(root, bundle):
    """Every file under `root` but outside `bundle`, with its bytes."""
    return {
        path: path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and bundle.root not in path.parents
    }


@pytest.mark.parametrize(
    "pages, copy_name",
    [(None, "paper_bilingual.epub"), ("1-2", "paper_pages-1-2_bilingual.epub")],
)
def test_harness_export_rebuilds_the_bundle_s_book_and_nothing_beside_the_pdf(
    tmp_path, pandoc, fake_format, request, pages, copy_name
):
    pdf, bundle = route_bundle(tmp_path, pandoc, pages)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    # A book beside the PDF under the route's own name: the export must not
    # touch it, whether it is the route's copy, an edited one or another.
    copy = tmp_path / copy_name
    copy.write_bytes(b"a book the operator keeps beside the PDF")
    edit(bundle)
    before = provenance(bundle)
    untouched = outside(tmp_path, bundle)
    FakeTranslator.instances = []
    request.getfixturevalue("nothing_translates")

    assert load_harness().main(["--pandoc", pandoc, "export", str(bundle.root)]) == 0

    assert EDIT_TO in epub_text(bundle.epub)
    assert EDIT_FROM not in epub_text(bundle.epub)
    assert copy.read_bytes() == b"a book the operator keeps beside the PDF"
    assert outside(tmp_path, bundle) == untouched
    assert FakeTranslator.instances == []
    # The machine translation's record is exactly what it was: its
    # fingerprint, the hash of the file it wrote, its stage.
    assert provenance(bundle) == before


def test_after_the_export_an_ordinary_rerun_still_refuses_and_names_both_steps(
    tmp_path, pandoc, fake_format
):
    from book_maker.pipeline.to_epub import pdf_to_epub

    pdf, bundle = route_bundle(tmp_path, pandoc, "1-2")
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    edit(bundle)
    assert load_harness().main(["--pandoc", pandoc, "export", str(bundle.root)]) == 0

    with pytest.raises(PipelineError) as refused:
        pdf_to_epub(
            pdf,
            ["--book_name", str(pdf), "--to-epub", *OPTIONS],
            pages="1-2",
            pandoc=pandoc,
            prepare_stage=lambda *a, **k: None,
            figure_stage=lambda *a, **k: None,
        )
    assert refused.value.stage == "translate"
    assert refused.value.detail == (
        "Bilingual Markdown was edited; rebuild the EPUB from it with: "
        f"python tools/pdf_to_book.py export {bundle.root}, then copy "
        f"{bundle.root / 'book_bilingual.epub'} over "
        f"{tmp_path / 'paper_pages-1-2_bilingual.epub'}."
    )
    assert EDIT_TO in bundle.bilingual_markdown.read_text(encoding="utf-8")


def test_the_refusal_quotes_every_path_with_a_space(tmp_path, pandoc, fake_format):
    from book_maker.pipeline.to_epub import pdf_to_epub

    spaced = tmp_path / "my papers"
    spaced.mkdir()
    pdf, bundle = route_bundle(spaced, pandoc)
    translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    edit(bundle)
    with pytest.raises(PipelineError) as refused:
        translate_bundle(bundle, OPTIONS, pandoc=pandoc)
    assert refused.value.detail.endswith(f"export '{bundle.root}'")

    with pytest.raises(PipelineError) as refused:
        pdf_to_epub(
            pdf,
            ["--book_name", str(pdf), "--to-epub", *OPTIONS],
            pandoc=pandoc,
            prepare_stage=lambda *a, **k: None,
            figure_stage=lambda *a, **k: None,
        )
    assert refused.value.detail.endswith(
        f"export '{bundle.root}', then copy '{bundle.epub}' over "
        f"'{spaced / 'paper_bilingual.epub'}'."
    )


def test_a_sample_stays_a_sample_through_the_harness_export(
    tmp_path, pandoc, fake_format
):
    pdf, bundle = route_bundle(tmp_path, pandoc)
    translate_bundle(bundle, OPTIONS + ["--test", "--test_num", "2"], pandoc=pandoc)
    # The sample's first two blocks are the ones translated.
    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    bundle.bilingual_markdown.write_text(
        text.replace("译:Chapter One", "译:手工修订的标题"), encoding="utf-8"
    )
    assert load_harness().main(["--pandoc", pandoc, "export", str(bundle.root)]) == 0

    assert bundle.read_manifest()["translation"]["sample"] is True
    book = epub_text(bundle.epub)
    assert "手工修订的标题" in book
    assert "(sample)" in book
    assert not (tmp_path / "paper_bilingual.epub").exists()
