"""The output file says what it is.

A translated book is a new work made by a machine, and the file should say
so where both a person and a library will see it: in the package metadata,
and on one page at the end. Nothing here touches what identifies the
original — `dc:creator`, `dc:rights` and the rest are the source's and stay
untouched. What goes is only what describes *this file* and is no longer
true of it: calibre's record of the book it built.
"""

import re
import zipfile
from datetime import date

import pytest
from ebooklib import epub

from book_maker.loader.disclosure import (
    model_id,
    COLOPHON_FILE,
    COLOPHON_ID,
    COLOPHON_TITLE,
    CONTRIBUTOR_ID,
    DESCRIPTION_TAIL,
    GENERATOR_MARK,
)
from book_maker.loader.epub_loader import EPUBBookLoader

OPF_NS = epub.NAMESPACES["OPF"]
DC_NS = epub.NAMESPACES["DC"]


class StubModel:
    """A translator that knows which model it runs."""

    TRANSLATION_ERROR_MARKER = None
    model = "x/y"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"T{text}"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


class ModellessModel(StubModel):
    """Some backends are one service with no model to name."""

    model = None


def _source(identifier="urn:uuid:source-1", metadata=()):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Disclosure fixture")
    book.set_language("en")
    book.add_author("A. Author")
    for namespace, name, value, others in metadata:
        book.add_metadata(namespace, name, value, others)
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>Body text</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    return book


def _rebuild(
    source, *, model=StubModel, disclose=True, single=False, language="zh-hans"
):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source
    loader.language = language
    loader.single_translate = single
    loader.disclose = disclose
    loader.translate_model = model() if model else None
    new_book = loader._make_new_book(source)
    # Findings 7/8: `_make_new_book` no longer stamps. The disclosure is
    # applied to the finished book at write time, which is the only moment
    # the model a --model_list run used is settled.
    loader._stamp_disclosure(new_book)
    return new_book


def _written_opf(tmp_path, book, name="out.epub"):
    out = tmp_path / name
    epub.write_epub(str(out), book)
    with zipfile.ZipFile(out) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return archive.read(opf_name).decode("utf-8")


# ------------------------------------------------------------- the credit


def test_the_tool_is_named_as_a_translator(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source()))

    assert (
        f'<dc:contributor id="{CONTRIBUTOR_ID}">bilingual_book_maker</dc:contributor>'
        in opf
    )
    assert (
        f'<meta refines="#{CONTRIBUTOR_ID}" property="role" '
        'scheme="marc:relators">trl</meta>' in opf
    )


def test_the_author_is_left_alone(tmp_path):
    """The original's creator is the original's; a translation does not
    edit it, add to it, or push the tool into it."""
    rebuilt = _rebuild(_source())

    creators = rebuilt.get_metadata("DC", "creator")

    assert [value for value, _ in creators] == ["A. Author"]


def test_the_description_names_the_model_the_run_used(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source()))

    year = date.today().year
    assert (
        f"<dc:description>AI translation (x/y, {year}).\n"
        "Original text unaltered; translation quality not verified."
        "</dc:description>" in opf
    )


def test_a_translator_with_no_model_still_says_what_made_the_file(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source(), model=ModellessModel))

    assert "AI translation (ModellessModel," in opf


def test_the_source_description_survives(tmp_path):
    source = _source(metadata=[("DC", "description", "The publisher's blurb.", None)])

    opf = _written_opf(tmp_path, _rebuild(source))

    assert "<dc:description>The publisher's blurb.</dc:description>" in opf
    assert opf.count("<dc:description>") == 2


# ------------------------------------------------------------ the colophon


def _colophon_of(book):
    return book.get_item_with_id(COLOPHON_ID)


def test_the_colophon_is_the_last_thing_in_the_book(tmp_path):
    rebuilt = _rebuild(_source())

    assert _colophon_of(rebuilt) is not None
    assert rebuilt.spine[-1] is _colophon_of(rebuilt)
    assert rebuilt.spine[0] is not _colophon_of(rebuilt)


def test_the_colophon_says_everything_it_has_to(tmp_path):
    rebuilt = _rebuild(_source(identifier="urn:uuid:source-1"))
    page = _colophon_of(rebuilt).content.decode("utf-8")

    assert "<title>" in page
    assert "Translation note" in page
    assert "x/y" in page
    assert date.today().isoformat() in page
    assert "This translation has not been reviewed by a human translator." in page


def test_the_colophon_reads_like_a_log(tmp_path):
    """One heading, then `Title: content` a line at a time. A page of facts
    about a file should not arrive dressed as a chapter."""
    page = _colophon_of(_rebuild(_source())).content.decode("utf-8")

    assert "<h1>Disclaimer</h1>" in page
    assert page.count("<h1") == 1
    assert "<p>Model: x/y</p>" in page
    assert f"<p>Date: {date.today().isoformat()}</p>" in page
    assert (
        "<p>Note: This translation has not been reviewed by a human "
        "translator.</p>" in page
    )


def test_the_colophon_is_model_date_and_the_note_and_nothing_else(tmp_path):
    """Owner decision (260905): the reader's page stays concise — model,
    date, the note. Tool credit, source identifier and languages live in
    the machine record, not on the page."""
    rebuilt = _rebuild(_source(identifier="urn:uuid:source-1"))
    page = _colophon_of(rebuilt).content.decode("utf-8")

    assert page.count("<p>") == 3
    assert "Translated by" not in page
    assert "Source identifier" not in page
    assert "Target language" not in page
    assert "urn:uuid:source-1" not in page
    assert "zh-hans" not in page


def test_the_colophon_carries_no_structure_a_reader_has_to_parse(tmp_path):
    """No list markup, no bold, no emphasis: plain lines, so nobody reads
    the page as a document with a shape."""
    page = _colophon_of(_rebuild(_source())).content.decode("utf-8")

    for markup in ("<ul", "<ol", "<li", "<strong", "<em", "<b>", "<i>"):
        assert markup not in page, markup


def test_the_colophon_keeps_what_identifies_it(tmp_path):
    """The generator marker is how a rerun recognises its own page, and the
    document title is what a reading system shows. The reformat moved
    neither — only the heading, which is the reader's."""
    page = _colophon_of(_rebuild(_source())).content.decode("utf-8")

    assert f'<meta name="generator" content="{GENERATOR_MARK}"/>' in page
    assert f"<title>{COLOPHON_TITLE}</title>" in page


def test_a_single_translation_gets_the_colophon_too(tmp_path):
    rebuilt = _rebuild(_source(), single=True)

    assert rebuilt.spine[-1] is _colophon_of(rebuilt)


def test_the_colophon_is_a_document_at_the_package_root(tmp_path):
    rebuilt = _rebuild(_source())
    colophon = _colophon_of(rebuilt)

    assert colophon.file_name == COLOPHON_FILE
    assert colophon.media_type == "application/xhtml+xml"


def test_the_colophon_is_not_put_in_the_navigation(tmp_path):
    """It is a note about the file, not a chapter of the book."""
    rebuilt = _rebuild(_source())

    def titles(entries):
        for entry in entries:
            if isinstance(entry, tuple):
                yield from titles(entry[1])
                yield getattr(entry[0], "title", "")
            else:
                yield getattr(entry, "title", "")

    assert "Translation note" not in set(titles(rebuilt.toc))


# ------------------------------------------------------- rebuilding a rebuild


def _translate_file(path):
    loader = EPUBBookLoader(
        str(path), StubModel, key="", resume=False, language="zh-hans"
    )
    loader.quiet = True
    loader.make_bilingual_book()
    return path.with_name(f"{path.stem}_bilingual.epub")


def test_a_rebuild_of_a_translated_book_stacks_nothing(tmp_path):
    """Run the whole tool on its own output and every disclosure is
    replaced, not doubled: one contributor, one machine-translation
    description, one colophon item, one spine entry for it.

    The manifest is what makes this a real test rather than a metadata one
    — the source's colophon arrives as an ordinary document, and a second
    entry under the same id is a book no reading system will open."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())

    once = _translate_file(source)
    twice = _translate_file(once)

    for output in (once, twice):
        with zipfile.ZipFile(output) as archive:
            opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
            opf = archive.read(opf_name).decode("utf-8")
        assert opf.count(f'id="{CONTRIBUTOR_ID}"') == 1, output.name
        assert opf.count("AI translation (") == 1, output.name
        assert opf.count(f'href="{COLOPHON_FILE}"') == 1, output.name
        assert opf.count(f'idref="{COLOPHON_ID}"') == 1, output.name
        assert opf.index(f'idref="{COLOPHON_ID}"') > opf.rindex("<spine")


# ------------------------------------------------- what did the work: the label


def _service(cls, model=None):
    """A registered translator with nothing constructed behind it."""
    translator = cls.__new__(cls)
    translator.model = model
    return lambda: translator


def test_an_engine_route_is_a_machine_translation(tmp_path):
    """Google, DeepL and the other fixed services are machine translation;
    the label says so, and the service is what is named."""
    from book_maker.translator import Google

    opf = _written_opf(tmp_path, _rebuild(_source(), model=_service(Google)))

    year = date.today().year
    assert f"Machine translation (google, {year})" in opf
    assert "AI translation (" not in opf


def test_a_model_route_is_an_ai_translation(tmp_path):
    from book_maker.translator import ChatGPTAPI

    opf = _written_opf(
        tmp_path, _rebuild(_source(), model=_service(ChatGPTAPI, "gpt-x"))
    )

    assert "AI translation (gpt-x," in opf
    assert "Machine translation (" not in opf


def test_every_registered_format_gets_the_label_its_kind_earns():
    from book_maker.loader.disclosure import (
        AI_LABEL,
        ENGINE_LABEL,
        translation_label,
    )
    from book_maker.translator import FORMAT_DICT, LLM_FORMATS, ROUTE_DICT

    for key, cls in FORMAT_DICT.items():
        expected = AI_LABEL if key in LLM_FORMATS else ENGINE_LABEL
        assert translation_label(_service(cls)()) == expected, key
    for key, cls in ROUTE_DICT.items():
        assert translation_label(_service(cls)()) == AI_LABEL, key
    assert translation_label(StubModel()) == AI_LABEL
    assert translation_label(None) == AI_LABEL


def test_the_description_is_two_lines(tmp_path):
    """The claim on one line, the caveat on the next: a reader's metadata
    pane shows them as two sentences, not one run-on."""
    rebuilt = _rebuild(_source())

    ((description, _),) = rebuilt.get_metadata("DC", "description")
    lines = description.split("\n")

    assert len(lines) == 2
    assert lines[0].startswith("AI translation (x/y, ") and lines[0].endswith(").")
    assert lines[1] == "Original text unaltered; translation quality not verified."


def test_a_stamp_written_on_one_line_is_still_ours(tmp_path):
    """The caveat moved onto its own line after the first books were
    stamped; a description from before the break is recognised all the
    same, or a rerun of such a book would claim both."""
    one_line = (
        "Machine translation (x/y, 2025). Original text unaltered; "
        "translation quality not verified."
    )
    source = _source(metadata=[("DC", "description", one_line, None)])

    opf = _written_opf(tmp_path, _rebuild(source))

    assert opf.count("<dc:description>") == 1
    assert "2025" not in opf
    assert "AI translation (x/y," in opf


def test_a_prior_engine_stamp_is_replaced_by_a_model_rerun(tmp_path):
    """Either label is ours: a book first translated by Google and then by
    a model must not keep saying Google did it."""
    source = _source(
        metadata=[
            (
                "DC",
                "description",
                f"Machine translation (google, 2025{DESCRIPTION_TAIL}",
                None,
            )
        ]
    )

    opf = _written_opf(tmp_path, _rebuild(source))

    assert opf.count(DESCRIPTION_TAIL) == 1
    assert "google" not in opf
    assert "AI translation (x/y," in opf


# ------------------------------------------------------------- the off switch


def test_nothing_is_disclosed_when_disclosure_is_off(tmp_path):
    rebuilt = _rebuild(_source(), disclose=False)
    opf = _written_opf(tmp_path, rebuilt)

    assert f'id="{CONTRIBUTOR_ID}"' not in opf
    assert "AI translation (" not in opf
    assert _colophon_of(rebuilt) is None
    assert COLOPHON_FILE not in opf


def test_the_loader_takes_the_switch(tmp_path):
    source_path = tmp_path / "book.epub"
    epub.write_epub(str(source_path), _source())

    loader = EPUBBookLoader(
        str(source_path),
        StubModel,
        key="",
        resume=False,
        language="zh-hans",
        disclose=False,
    )

    assert loader.disclose is False
    assert _colophon_of(loader._make_new_book(loader.origin_book)) is None


def test_disclosure_is_on_by_default(tmp_path):
    source_path = tmp_path / "book.epub"
    epub.write_epub(str(source_path), _source())

    loader = EPUBBookLoader(
        str(source_path), StubModel, key="", resume=False, language="zh-hans"
    )

    assert loader.disclose is True


# ---------------------------------------------------------- calibre's record


CALIBRE_METAS = [
    (None, "meta", None, {"name": "calibre:title_sort", "content": "Sorted"}),
    (None, "meta", None, {"name": "calibre:timestamp", "content": "2016-06-01"}),
]


def _calibre_source():
    source = _source()
    opf_metas = source.metadata.setdefault(OPF_NS, {}).setdefault("meta", [])
    opf_metas.append((None, {"name": "calibre:title_sort", "content": "Sorted"}))
    opf_metas.append((None, {"name": "calibre:timestamp", "content": "2016-06-01"}))
    opf_metas.append((None, {"name": "cover", "content": "cover"}))
    opf_metas.append(
        ("Yes", {"property": "ibooks:specified-fonts"}),
    )
    source.metadata["http://calibre.kovidgoyal.net/2009/metadata"] = {
        "series": [("Some series", {})]
    }
    return source


def test_calibres_record_of_the_file_it_built_is_dropped(tmp_path):
    """It describes a different file — the one calibre made, not this one."""
    opf = _written_opf(tmp_path, _rebuild(_calibre_source()))

    assert "calibre" not in opf


def test_what_the_reading_system_needs_is_kept(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_calibre_source()))

    assert '<meta name="cover" content="cover"/>' in opf
    assert 'property="ibooks:specified-fonts"' in opf


def test_calibre_metadata_goes_even_with_disclosure_off(tmp_path):
    """Dropping a false description of the file is not a disclosure; it is
    correctness, and has no switch."""
    opf = _written_opf(tmp_path, _rebuild(_calibre_source(), disclose=False))

    assert "calibre" not in opf


# ------------------------------------------------------------- the model id


def test_every_translator_answers_what_model_it_runs():
    """No translator may leave the file unable to say what made it."""
    from book_maker.translator import FORMAT_DICT, ROUTE_DICT

    for name, translator in {**FORMAT_DICT, **ROUTE_DICT}.items():
        assert isinstance(getattr(translator, "model_name", None), property), name


def test_the_llm_translators_report_the_model_they_were_given():
    from book_maker.translator import FORMAT_DICT, LLM_FORMATS

    for name in LLM_FORMATS:
        translator = FORMAT_DICT[name].__new__(FORMAT_DICT[name])
        translator.model = "vendor/some-model"
        assert translator.model_name == "vendor/some-model", name


def _keys_of(cls):
    """Every `--api_format` / `--provider` key `cls` is registered under.

    A set, because the hermetic stand-in is registered under more than one
    key; a translator answers with one of them.
    """
    from book_maker.translator import FORMAT_DICT, ROUTE_DICT

    return {
        key
        for registry in (FORMAT_DICT, ROUTE_DICT)
        for key, registered in registry.items()
        if registered is cls
    }


def test_a_service_with_no_model_names_the_service():
    """No model to name → the `--api_format` key the service is selected
    by, not the Python class name."""
    from book_maker.translator import FORMAT_DICT

    for key, cls in FORMAT_DICT.items():
        translator = cls.__new__(cls)
        translator.model = None
        assert translator.model_name in _keys_of(cls), key
        assert translator.model_name != cls.__name__, key


def test_a_provider_route_with_no_model_names_the_provider():
    from book_maker.translator import ROUTE_DICT

    for key, cls in ROUTE_DICT.items():
        translator = cls.__new__(cls)
        translator.model = None
        assert translator.model_name in _keys_of(cls), key


def test_a_translator_registered_nowhere_names_its_class():
    from book_maker.translator.base_translator import Base

    class Unregistered(Base):
        def __init__(self):
            pass

        def rotate_key(self):
            pass

        def translate(self, text):
            return text

    assert Unregistered().model_name == "Unregistered"


# ------------------------------- findings 3, 4, 9: names that do not collide


def _source_with(extra_items=(), extra_metadata=(), identifier="urn:uuid:source-1"):
    book = _source(identifier=identifier)
    for item in extra_items:
        book.add_item(item)
        book.spine.append(item)
    for namespace, name, value, others in extra_metadata:
        book.add_metadata(namespace, name, value, others)
    return book


def _chapter(uid, file_name, text="A real chapter."):
    item = epub.EpubHtml(title=uid, file_name=file_name, lang="en")
    item.id = uid
    item.content = (
        "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>t</title></head>"
        f"<body><p>{text}</p></body></html>"
    )
    return item


def _output_of(tmp_path, source, name="book.epub"):
    path = tmp_path / name
    epub.write_epub(str(path), source)
    return _translate_file(path)


def _members_and_opf(output):
    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        opf_name = next(n for n in members if n.endswith(".opf"))
        return members, archive.read(opf_name).decode("utf-8")


def _manifest_ids(opf):
    return re.findall(r'<item\b[^>]*\bid="([^"]+)"', opf)


def _all_ids(opf):
    return re.findall(r'\bid="([^"]+)"', opf)


def _assert_sound(members, opf):
    assert len(members) == len(set(members)), "a duplicate zip member"
    ids = _all_ids(opf)
    assert len(ids) == len(set(ids)), f"a duplicate id: {ids}"


def test_a_publishers_colophon_id_is_not_taken_over(tmp_path):
    """Finding 3/9: an `id="colophon"` on something of the book's own must
    keep its id, its file and its place — and ours must go somewhere else."""
    source = _source_with([_chapter("colophon", "notes.xhtml", "The book's notes.")])

    members, opf = _output_of(tmp_path, source), None
    members, opf = _members_and_opf(members)

    _assert_sound(members, opf)
    assert 'id="colophon"' in opf
    assert "notes.xhtml" in opf
    assert "The book" in _text_of(tmp_path, "notes.xhtml")


def test_a_publishers_colophon_filename_is_not_taken_over(tmp_path):
    """Finding 3/9: the source's own colophon.xhtml survives; ours never
    wanted that name in the first place."""
    source = _source_with([_chapter("front", "colophon.xhtml", "Set in Bembo.")])

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "EPUB/colophon.xhtml" in members
    assert "Bembo" in _text_of(tmp_path, "colophon.xhtml")


def test_our_own_id_on_a_real_chapter_is_left_alone(tmp_path):
    """Finding 3/4/9: recognition is by the marker in the document, never by
    id — a chapter that happens to carry ours is content, and is translated
    and kept while the note is allocated a suffixed name."""
    source = _source_with(
        [_chapter(COLOPHON_ID, "chapter-two.xhtml", "Chapter two begins.")]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "EPUB/chapter-two.xhtml" in members
    body = _text_of(tmp_path, "chapter-two.xhtml")
    assert "Chapter two begins." in body
    assert "TChapter two begins." in body  # StubModel's translation, still done
    assert f'id="{COLOPHON_ID}-2"' in opf
    assert "bbm_translation_note-2.xhtml" in opf


def test_our_contributor_id_on_someone_elses_metadata_is_left_alone(tmp_path):
    """Finding 4: `dc:creator id="bbm-trl"` belongs to the book. Ours takes
    the next id rather than colliding with it."""
    source = _source_with(
        extra_metadata=[("DC", "creator", "Alice", {"id": CONTRIBUTOR_ID})]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert f'<dc:creator id="{CONTRIBUTOR_ID}">Alice</dc:creator>' in opf
    assert f'<dc:contributor id="{CONTRIBUTOR_ID}-2">' in opf
    assert f'refines="#{CONTRIBUTOR_ID}-2"' in opf


def _text_of(tmp_path, file_name):
    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        return archive.read(f"EPUB/{file_name}").decode("utf-8")


class ModelB(StubModel):
    model = "vendor/b"


# ------------------------------------- finding 5: --retranslate copies items


def test_retranslate_does_not_copy_the_previous_translation_note(tmp_path):
    """Finding 5: `retranslate_book` copies every item but the one it edits,
    so a previous output's note was carried into the new book.

    Retranslated with a different model, so a stale note surviving is
    visible: keeping the old page is not merely a duplicate, it is the
    wrong claim about who did the work.
    """
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(source), ModelB, key="", resume=False, language="zh-hans"
    )
    loader.quiet = True
    loader.retranslate = [str(once), "chapter.xhtml", "Body text", "Body text"]
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()

    # retranslate_book writes back over the book it was given
    members, opf = _members_and_opf(once)
    _assert_sound(members, opf)
    notes = [m for m in members if "translation_note" in m]
    assert len(notes) == 1
    with zipfile.ZipFile(once) as archive:
        page = archive.read(notes[0]).decode("utf-8")
    assert "vendor/b" in page
    assert "x/y" not in page


# ------------------------------------------ finding 6: the recovery replay


def test_the_recovery_book_survives_a_second_generation_source(tmp_path):
    """Finding 6: `_save_temp_book` filtered our note out of the plan but
    still asked for one plan per document, so an interrupted run over a
    previous output died with StopIteration instead of saving."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(once), StubModel, key="", resume=False, language="zh-hans"
    )
    loader.quiet = True
    loader.make_bilingual_book()
    # the interruption: some translations done, the run cut short
    loader.p_to_save = loader.p_to_save[:1]

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    assert temp.exists()
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)


# -------------------------------- finding 7: a prior stamp is ours to rewrite


def test_a_second_translation_names_only_the_model_that_did_it(tmp_path):
    """Finding 7: the previous run's contributor, refine and description are
    stripped on copy and written again from this run's facts."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(str(once), ModelB, key="", resume=False, language="zh-hans")
    loader.quiet = True
    loader.make_bilingual_book()

    members, opf = _members_and_opf(once.with_name(f"{once.stem}_bilingual.epub"))
    _assert_sound(members, opf)
    assert "vendor/b" in opf
    assert "x/y" not in opf
    assert opf.count("AI translation (") == 1
    assert opf.count(f'id="{CONTRIBUTOR_ID}"') == 1


def test_a_rebuild_with_disclosure_off_carries_no_prior_stamp(tmp_path):
    """Finding 7: prior disclosure is stripped whether or not a fresh one is
    written — otherwise --no_disclosure would leave the *previous* run's
    claim standing, which is the worst of both."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(once),
        StubModel,
        key="",
        resume=False,
        language="zh-hans",
        disclose=False,
    )
    loader.quiet = True
    loader.make_bilingual_book()

    members, opf = _members_and_opf(once.with_name(f"{once.stem}_bilingual.epub"))
    _assert_sound(members, opf)
    assert "AI translation (" not in opf
    assert f'id="{CONTRIBUTOR_ID}"' not in opf
    assert not [m for m in members if "translation_note" in m]


def test_the_books_own_contributors_and_description_are_untouched(tmp_path):
    """Finding 7: only entries carrying *our* value and role refine go."""
    source = _source_with(
        extra_metadata=[
            ("DC", "contributor", "A. Editor", {"id": "ed"}),
            ("DC", "description", "The publisher's blurb.", None),
            ("DC", "contributor", TOOL_NAME_IN_A_CREDIT, {"id": "thanks"}),
        ]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert '<dc:contributor id="ed">A. Editor</dc:contributor>' in opf
    assert "The publisher's blurb." in opf
    # credited without a trl refine: the book's statement, not a stamp
    assert '<dc:contributor id="thanks">' in opf


TOOL_NAME_IN_A_CREDIT = "bilingual_book_maker"


# ------------------------------------------- finding 8: which model ran


class RotatingModel(StubModel):
    """A run given --model_list may use any of them — the openai translator's
    shape, where `_model_names` is the readable list beside the cycle."""

    model = "a"
    _model_names = ["a", "b"]


def test_a_model_list_run_names_every_model_it_could_have_used(tmp_path):
    """Finding 8: the model was captured before a single request ran, so a
    rotating run recorded only the first entry."""
    rebuilt = _rebuild(_source(), model=RotatingModel)
    opf = _written_opf(tmp_path, rebuilt, name="rotating.epub")

    assert "AI translation (a, b," in opf
    page = rebuilt.get_item_with_id(COLOPHON_ID).content.decode("utf-8")
    assert "a, b" in page


class _RotatingStub:
    """The openai translator's shape: `model_list` is an itertools.cycle,
    the readable list lives on `_model_names`."""

    model = "a"
    model_name = "a"

    def __init__(self, names):
        import itertools

        self._model_names = list(names)
        self.model_list = itertools.cycle(names)


class _CycleOnlyStub:
    model = "a"
    model_name = "a"

    def __init__(self):
        import itertools

        self.model_list = itertools.cycle(["a", "b"])


def test_model_id_never_iterates_a_cycle():
    """A `model_list` that is an itertools.cycle used to be iterated into a
    list: unbounded memory at write time on every real openai run (the
    260902 smoke matrix took a 16 GB machine down with it)."""
    assert model_id(_RotatingStub(["a", "b"])) == "a, b"
    assert model_id(_CycleOnlyStub()) == "a"


# --------------------- finding 2 (re-review): whose description is it


def test_a_publishers_own_machine_translation_note_survives(tmp_path):
    """Finding 2 (re-review): the matcher keyed on the opening words alone,
    so a publisher's `<dc:description>Machine translation (French edition)`
    — a statement about the book — was deleted as though this tool had
    written it."""
    source = _source_with(
        extra_metadata=[
            ("DC", "description", "Machine translation (French edition)", None)
        ]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "Machine translation (French edition)" in opf
    # ours is written beside it, not instead of it
    assert opf.count("<dc:description>") == 2
    assert DESCRIPTION_TAIL in opf


def test_our_own_description_is_still_replaced_on_a_rerun(tmp_path):
    """Finding 2 (re-review): recognising the whole sentence must not stop
    the tool from owning what it wrote."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(str(once), ModelB, key="", resume=False, language="zh-hans")
    loader.quiet = True
    loader.make_bilingual_book()

    _, opf = _members_and_opf(once.with_name(f"{once.stem}_bilingual.epub"))

    assert opf.count(DESCRIPTION_TAIL) == 1
    assert "vendor/b" in opf
    assert "x/y" not in opf


# ------------- finding 3 (re-review): the recovery save still copied the note


def _interrupted_loader(tmp_path, disclose=True, model=ModelB):
    """A loader part-way through translating a previous output."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(once),
        model,
        key="",
        resume=False,
        language="zh-hans",
        disclose=disclose,
    )
    loader.quiet = True
    loader.make_bilingual_book()
    loader.p_to_save = loader.p_to_save[:1]
    return loader, once


def test_the_recovery_book_carries_this_runs_note_not_the_last_ones(tmp_path):
    """Finding 3 (re-review): the replay skipped the old note when handing
    out plans but added it to the book anyway, so the stamp saw a note
    already there and left the previous run's claim standing."""
    loader, once = _interrupted_loader(tmp_path)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)
    notes = [m for m in members if "translation_note" in m]
    assert len(notes) == 1
    with zipfile.ZipFile(temp) as archive:
        page = archive.read(notes[0]).decode("utf-8")
    assert "vendor/b" in page
    assert "x/y" not in page


def test_the_recovery_book_carries_no_note_with_disclosure_off(tmp_path):
    """Finding 3 (re-review): --no_disclosure kept the previous run's note,
    which is the one outcome the flag exists to prevent."""
    loader, once = _interrupted_loader(tmp_path, disclose=False)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)
    assert not [m for m in members if "translation_note" in m]
    assert DESCRIPTION_TAIL not in opf


# ------------------ finding 4 (re-review): only a rotating run names several


class _StoredListStub:
    """Codex's shape: `set_model_list` keeps every name, but every request
    goes to `self.model`. Naming them all would be a false claim."""

    model = "a"
    model_name = "a"
    model_list = ["a", "b"]


def test_a_stored_model_list_without_rotation_names_one_model():
    """Finding 4 (re-review): a finite `model_list` was read as rotation.
    Only `_model_names`, which the rotating translator keeps, means that."""
    assert model_id(_StoredListStub()) == "a"


# ------------------------------- a fixed-layout book has no page for the note


FIXED_LAYOUT_META = ("OPF", "meta", "pre-paginated", {"property": "rendition:layout"})


def _colophon_entry(book):
    return next(
        entry
        for entry in book.spine
        if getattr(entry, "file_name", "").startswith("bbm_translation_note")
    )


def test_the_note_declares_itself_reflowable_in_a_fixed_layout_book():
    """A pre-paginated package requires page dimensions of every spine
    document (epubcheck HTM-046), and the note has none to give: its length
    is whatever the model id makes it. So it says it is not laid out like
    the rest of the book — the same property the corpus's own fixed-layout
    books put on their prose pages — instead of being handed an invented
    page size."""
    rebuilt = _rebuild(_source(metadata=[FIXED_LAYOUT_META]))

    assert _colophon_entry(rebuilt).spine_properties == ["rendition:layout-reflowable"]


def test_a_reflowable_book_says_nothing_about_the_note_s_layout():
    """Nothing to override, so nothing is written: the property would be a
    `rendition:` name in a book that has no reason to resolve one."""
    rebuilt = _rebuild(_source())

    assert not getattr(_colophon_entry(rebuilt), "spine_properties", None)


def test_a_document_pinned_pre_paginated_does_not_make_the_book_so():
    """Package level only. A reflowable book that pins individual documents
    leaves everything it did not name reflowable, the note included."""
    rebuilt = _rebuild(
        _source(
            metadata=[
                (
                    "OPF",
                    "meta",
                    "pre-paginated",
                    {"refines": "#chapter", "property": "rendition:layout"},
                )
            ]
        )
    )

    assert not getattr(_colophon_entry(rebuilt), "spine_properties", None)


def test_the_spine_property_reaches_the_opf(tmp_path):
    """ebooklib's spine writer emits `idref` and `linear` and nothing else,
    so the property only exists in the file because `epub_loader` wraps it.
    Nothing is installed here on purpose: importing the loader is what
    installs the wrapper, so a caller that stamps a book and writes it
    without ever building a loader gets the same OPF."""
    opf = _written_opf(tmp_path, _rebuild(_source(metadata=[FIXED_LAYOUT_META])))

    assert (
        f'<itemref idref="{COLOPHON_ID}" properties="rendition:layout-reflowable"/>'
        in opf
    )


def test_the_wrapper_is_an_addition_not_a_replacement(tmp_path, monkeypatch):
    """Under ebooklib's own writer the spine is exactly what it always was,
    and an item carrying a property nobody reads is not an error. Pinned
    explicitly rather than by leaving the class alone: any test in this
    file that builds a real loader installs the wrapper process-wide."""
    from book_maker.loader import epub_loader

    monkeypatch.setattr(
        epub.EpubWriter,
        "_write_opf_spine",
        epub_loader._EBOOKLIB_WRITE_OPF_SPINE,
    )
    opf = _written_opf(tmp_path, _rebuild(_source(metadata=[FIXED_LAYOUT_META])))

    assert f'<itemref idref="{COLOPHON_ID}"/>' in opf


# ------------------------------- a bad entry costs a line, never the book


def _said(capsys):
    """Everything printed, with rich's terminal wrapping folded away."""
    return " ".join(capsys.readouterr().out.split())


def test_an_unreadable_metadata_entry_is_skipped_and_named(capsys):
    """`others` is meant to be a dict of attributes. A source that put
    something else there used to take the whole translation down with it on
    the first `.get()`."""
    source = _source(metadata=[("DC", "subject", "Fables", "not-a-dict")])

    rebuilt = _rebuild(source)

    assert [value for value, _ in rebuilt.get_metadata("DC", "creator")] == [
        "A. Author"
    ]
    said = _said(capsys)
    assert "1 metadata entry could not be copied" in said
    assert "subject" in said


def test_the_book_survives_a_metadata_block_it_cannot_read_at_all(capsys):
    """Several bad entries are one warning, not one warning each: a book
    with a systematically odd metadata block would otherwise bury itself."""
    source = _source(
        metadata=[("DC", "subject", f"Subject {n}", "not-a-dict") for n in range(5)]
    )

    rebuilt = _rebuild(source)

    assert rebuilt.get_metadata("DC", "title")
    said = _said(capsys)
    assert said.count("could not be copied") == 1
    assert "5 metadata entries could not be copied" in said
    assert "and 2 more" in said


def test_a_failed_stamp_still_writes_the_book(tmp_path, capsys, monkeypatch):
    """Hours of translation are not worth losing over the note at the end.
    The book goes out unstamped and the run says so."""
    from book_maker.loader import epub_loader

    def boom(*args, **kwargs):
        raise RuntimeError("no room at the end of the book")

    monkeypatch.setattr(epub_loader, "stamp_disclosure", boom)
    opf = _written_opf(tmp_path, _rebuild(_source()))

    assert "bilingual_book_maker" not in opf
    assert DESCRIPTION_TAIL not in opf
    said = _said(capsys)
    assert "could not be marked as a machine translation" in said
    assert "no room at the end of the book" in said
    # The warning has to say what is missing, not just that something is.
    assert "nothing in the file will say it was translated by a machine" in said


def test_a_stamp_that_fails_halfway_leaves_nothing_behind(monkeypatch):
    """The credit and the note stand or fall together. A `dc:contributor`
    naming a translator with no note behind it says less than saying
    nothing, and the caller is entitled to write the book after a failure."""
    from book_maker.loader import disclosure

    def boom(*args, **kwargs):
        raise RuntimeError("cannot build the note")

    monkeypatch.setattr(disclosure, "build_colophon", boom)
    # Built with the stamp off, so this exercises `stamp_disclosure` alone
    # and not the loader's own guard around it.
    book = _rebuild(_source(), disclose=False)
    before = len(book.spine)

    with pytest.raises(RuntimeError):
        disclosure.stamp_disclosure(book, "x/y", "zh-hans")

    assert not book.get_metadata("DC", "contributor")
    assert not book.get_metadata("DC", "description")
    assert len(book.spine) == before


def test_unreadable_prefix_declarations_do_not_stop_the_book(capsys, monkeypatch):
    """The prefixes are read straight from the source's OPF, so a package
    this cannot parse arrives here."""
    from book_maker.loader import epub_loader

    def boom(*args, **kwargs):
        raise ValueError("not an OPF")

    monkeypatch.setattr(epub_loader, "package_prefixes", boom)
    rebuilt = _rebuild(_source())

    assert rebuilt.get_metadata("DC", "title")
    assert "prefix declarations could not be read" in _said(capsys)


def test_an_unidentifiable_prior_stamp_does_not_stop_the_book(capsys, monkeypatch):
    """Runs before the copy loop, on the same metadata and with the same
    failure mode, where nothing else would catch it."""
    from book_maker.loader import epub_loader

    def boom(*args, **kwargs):
        raise AttributeError("'str' object has no attribute 'get'")

    monkeypatch.setattr(epub_loader, "tool_contributor_ids", boom)
    rebuilt = _rebuild(_source())

    assert rebuilt.get_metadata("DC", "title")
    said = _said(capsys)
    assert "an earlier translation stamp could not be identified" in said
    assert "may now credit both runs" in said


def test_an_underivable_identifier_does_not_stop_the_book(capsys, monkeypatch):
    """ebooklib's fresh uuid stands in. That is a real loss — the same
    translation run twice stops naming the same book — and is said so."""
    from book_maker.loader import epub_loader

    def boom(*args, **kwargs):
        raise ValueError("no identifier to derive from")

    monkeypatch.setattr(epub_loader, "derive_translation_identity", boom)
    rebuilt = _rebuild(_source())

    assert rebuilt.get_metadata("DC", "title")
    assert "could not derive a stable identifier" in _said(capsys)


def test_the_wrapper_names_what_it_wraps_so_a_reload_cannot_double_it():
    """The capture at import reads `_bbm_wraps`. Without it, reloading this
    module after the wrapper is installed captures the wrapper, which then
    calls itself on the next write until RecursionError."""
    from book_maker.loader import epub_loader

    wrapped = epub_loader._write_opf_spine_patch._bbm_wraps

    assert wrapped is not epub_loader._write_opf_spine_patch
    # What a re-import would capture, run against what is installed now.
    installed = epub.EpubWriter._write_opf_spine
    assert getattr(installed, "_bbm_wraps", installed) is wrapped


def test_one_badly_shaped_value_does_not_cost_its_whole_namespace(capsys):
    """A three-element entry where the reader expects two. Unpacked in a
    generator it raises at the loop, not at the entry, and everything else
    in the namespace — dc:language, dc:identifier — goes with it."""
    source = _source()
    source.metadata[DC_NS].setdefault("subject", []).append(("Fables", None, "extra"))

    rebuilt = _rebuild(source)

    # The target language is written in front of the source's; what matters
    # here is that the source's survived the malformed neighbour at all.
    assert "en" in [value for value, _ in rebuilt.get_metadata("DC", "language")]
    assert [value for value, _ in rebuilt.get_metadata("DC", "creator")] == [
        "A. Author"
    ]
    said = _said(capsys)
    assert "1 metadata entry could not be copied" in said
    assert "subject" in said


def test_a_book_that_cannot_take_the_stamp_is_refused_before_it_is_touched():
    """The commit half assumes two things about the book, and they are the
    only way it can fail. Checked while nothing has been written, so the
    caller's warning is true: no half-applied credit is left behind."""
    from book_maker.loader import disclosure

    book = _rebuild(_source(), disclose=False)
    # `_iter_metadata` tolerates this shape, so such a book reaches the stamp.
    book.metadata[None] = ["not a dict of entries"]
    before = len(book.metadata.get(DC_NS, {}).get("contributor", []))

    with pytest.raises(TypeError):
        disclosure.stamp_disclosure(book, "x/y", "zh-hans")

    assert len(book.metadata.get(DC_NS, {}).get("contributor", [])) == before
    assert not book.metadata.get(DC_NS, {}).get("description")
