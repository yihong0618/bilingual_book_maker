"""The output file says what it is, on one line.

Owner ruling, 260906: a distributed book must not be traceable back to the
operator, but a reader must still be able to see what could have gone wrong
with the translation. So the whole visible apparatus is a single small
paragraph below the book's own intro —

    Translated by gpt-5.6-luna, 2026.

— and the package metadata says nothing at all: no `bbm:` metas, no
`dc:contributor`, no `dc:description`. What survives of the old apparatus is
the recognition half, so a rerun still strips what an earlier build left
behind. Nothing here touches what identifies the original — `dc:creator`,
`dc:rights` and the rest are the source's and stay untouched. What goes is
only what describes *this file* and is no longer true of it: calibre's
record of the book it built, and this tool's own previous stamps.
"""

import re
import zipfile
from datetime import date

import pytest
from ebooklib import epub

from book_maker.loader.disclosure import (
    CREDIT_CLASS,
    is_our_colophon,
    CREDIT_PREFIX,
    DESCRIPTION_TAIL,
    GENERATOR_MARK,
    credit_name,
    model_id,
)
from book_maker.loader.epub_loader import EPUBBookLoader

OPF_NS = epub.NAMESPACES["OPF"]
DC_NS = epub.NAMESPACES["DC"]

TOOL_NAME_IN_A_CREDIT = "bilingual_book_maker"

# What an older build wrote and a rerun must still clear: the closing page,
# the two contributor credits with their role refines, the description and
# the package metas.
LEGACY_COLOPHON_ID = "bbm-translation-note"
LEGACY_COLOPHON_FILE = "bbm_translation_note.xhtml"
LEGACY_CONTRIBUTOR_ID = "bbm-trl"
LEGACY_PRODUCER_ID = "bbm-bkp"


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


class ModelB(StubModel):
    model = "vendor/b"


def _page(uid, file_name, body, title="A page"):
    item = epub.EpubHtml(title=title, file_name=file_name, lang="en")
    item.id = uid
    item.content = (
        "<html xmlns='http://www.w3.org/1999/xhtml'>"
        f"<head><title>{title}</title></head><body>{body}</body></html>"
    )
    return item


def _source(identifier="urn:uuid:source-1", metadata=(), guide=None, extra_items=()):
    """A two-document book: a title page, then a chapter."""
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Disclosure fixture")
    book.set_language("en")
    book.add_author("A. Author")
    for namespace, name, value, others in metadata:
        book.add_metadata(namespace, name, value, others)
    title = _page("titlepage", "title.xhtml", "<h1>Disclosure fixture</h1>", "Title")
    chapter = _page("chapter", "chapter.xhtml", "<p>Body text</p>", "One")
    book.add_item(title)
    book.add_item(chapter)
    for item in extra_items:
        book.add_item(item)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", title, chapter, *extra_items]
    if guide is not None:
        book.guide = guide
    return book


TITLE_PAGE_GUIDE = [{"type": "title-page", "href": "title.xhtml", "title": "Title"}]


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
    # The write routes add the source's items as they finish with them; a
    # rebuild without a translation has to do the same, or there is no
    # document in the book for the credit line to land on.
    for item in source.get_items():
        if not is_our_colophon(item):
            new_book.add_item(item)
    # `_make_new_book` does not stamp. The disclosure is applied to the
    # finished book at write time, which is the only moment the model a
    # --model_list run used is settled.
    loader._stamp_disclosure(new_book)
    return new_book


def _written_opf(tmp_path, book, name="out.epub"):
    out = tmp_path / name
    epub.write_epub(str(out), book)
    with zipfile.ZipFile(out) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return archive.read(opf_name).decode("utf-8")


_CREDIT_RE = re.compile(rf'<p class="{CREDIT_CLASS}"[^>]*>([^<]*)</p>'.encode("utf-8"))


def _credits_in(content):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return [m.decode("utf-8") for m in _CREDIT_RE.findall(content or b"")]


def _credits_of(book):
    """(file name, text) for every credit line in the book."""
    found = []
    for item in book.get_items():
        for text in _credits_in(getattr(item, "content", None)):
            found.append((item.file_name, text))
    return found


def _credits_in_zip(path):
    found = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            for text in _credits_in(archive.read(name)):
                found.append((name, text))
    return found


def _translate_file(path, model=StubModel, **kwargs):
    loader = EPUBBookLoader(
        str(path), model, key="", resume=False, language="zh-hans", **kwargs
    )
    loader.quiet = True
    loader.make_bilingual_book()
    return path.with_name(f"{path.stem}_bilingual.epub")


# --------------------------------------------------------- the credit line


def test_the_credit_is_one_line_at_the_end_of_the_title_page(tmp_path):
    """The whole reader-facing apparatus: one paragraph, appended below the
    book's own intro, naming the model and the year."""
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE))

    assert _credits_of(rebuilt) == [
        ("title.xhtml", f"{CREDIT_PREFIX}x/y, {date.today().year}.")
    ]
    page = rebuilt.get_item_with_id("titlepage").content.decode("utf-8")
    # below the intro, not in front of it
    assert page.index("Disclosure fixture") < page.index(CREDIT_CLASS)
    assert page.index(CREDIT_CLASS) < page.index("</body>")


def test_the_line_is_small_and_muted(tmp_path):
    """Subtle: it is a note about the file, not a line of the book. Inline,
    because the book's own stylesheet knows nothing about it."""
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE))
    page = rebuilt.get_item_with_id("titlepage").content.decode("utf-8")

    style = re.search(rf'class="{CREDIT_CLASS}" style="([^"]*)"', page).group(1)
    assert "font-size" in style and "opacity" in style


def test_nothing_at_all_is_declared_in_the_package_metadata(tmp_path):
    """The ruling in one assertion: zero `bbm:` metas, no contributor of
    ours, no description of ours."""
    opf = _written_opf(tmp_path, _rebuild(_source(guide=TITLE_PAGE_GUIDE)))

    assert "bbm:" not in opf
    assert "<dc:contributor" not in opf
    assert "<dc:description" not in opf
    assert TOOL_NAME_IN_A_CREDIT not in opf
    assert "marc:relators" not in opf


def test_the_visible_apparatus_is_the_line_and_nothing_else(tmp_path):
    """No closing page, no extra spine entry, no manifest document that was
    not the book's own."""
    source = _source(guide=TITLE_PAGE_GUIDE)
    before = len(source.spine)
    rebuilt = _rebuild(source)
    opf = _written_opf(tmp_path, rebuilt)

    assert len(rebuilt.spine) == before
    assert "translation_note" not in opf
    assert "Translation Credits" not in opf
    assert GENERATOR_MARK not in opf


def test_the_author_is_left_alone(tmp_path):
    """The original's creator is the original's; a translation does not
    edit it, add to it, or push the tool into it."""
    rebuilt = _rebuild(_source())

    creators = rebuilt.get_metadata("DC", "creator")

    assert [value for value, _ in creators] == ["A. Author"]


def test_the_source_description_survives(tmp_path):
    source = _source(metadata=[("DC", "description", "The publisher's blurb.", None)])

    opf = _written_opf(tmp_path, _rebuild(source))

    assert "<dc:description>The publisher's blurb.</dc:description>" in opf
    assert opf.count("<dc:description>") == 1


def test_a_translator_with_no_model_still_says_what_made_the_file(tmp_path):
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE), model=ModellessModel)

    ((_, text),) = _credits_of(rebuilt)
    assert text == f"{CREDIT_PREFIX}ModellessModel, {date.today().year}."


def test_the_line_survives_the_write(tmp_path):
    output = tmp_path / "out.epub"
    epub.write_epub(str(output), _rebuild(_source(guide=TITLE_PAGE_GUIDE)))

    assert _credits_in_zip(output) == [
        ("EPUB/title.xhtml", f"{CREDIT_PREFIX}x/y, {date.today().year}.")
    ]


# ---------------------------------------------------------- where it lands


def test_the_guide_decides_which_page_takes_the_line(tmp_path):
    """A book whose title page is not the first document still gets the line
    on the title page."""
    later = _page("front", "front.xhtml", "<p>Front matter.</p>", "Front")
    source = _source(
        guide=[{"type": "titlepage", "href": "title.xhtml", "title": "Title"}],
    )
    source.add_item(later)
    source.spine.insert(1, later)

    rebuilt = _rebuild(source)

    assert [name for name, _ in _credits_of(rebuilt)] == ["title.xhtml"]


def test_a_guideless_book_takes_the_first_linear_document(tmp_path):
    """No guide and no landmarks: the first thing a reader opens is as close
    to a title page as the book has said."""
    rebuilt = _rebuild(_source())

    assert [name for name, _ in _credits_of(rebuilt)] == ["title.xhtml"]


def test_the_epub3_landmarks_are_read_when_there_is_no_guide(tmp_path):
    """ebooklib parses the EPUB 2 `<guide>` and stops; an EPUB 3 book keeps
    the same statement in its navigation document."""
    nav = epub.EpubHtml(title="Nav", file_name="landmarks.xhtml", lang="en")
    nav.id = "landmarks"
    nav.properties = ["nav"]
    nav.content = (
        "<html xmlns='http://www.w3.org/1999/xhtml' "
        "xmlns:epub='http://www.idpf.org/2007/ops'><head><title>Nav</title></head>"
        "<body><nav epub:type='landmarks'><ol>"
        "<li><a epub:type='titlepage' href='title.xhtml'>Title</a></li>"
        "</ol></nav></body></html>"
    )
    source = _source(extra_items=[nav])
    # the landmark points backwards, so the spine fallback would answer
    # differently
    source.spine = [
        "nav",
        nav,
        source.get_item_with_id("chapter"),
        source.get_item_with_id("titlepage"),
    ]

    rebuilt = _rebuild(source)

    assert [name for name, _ in _credits_of(rebuilt)] == ["title.xhtml"]


def test_the_navigation_document_never_takes_the_line(tmp_path):
    """It is a table of contents, not a page of the book: a line appended to
    it reads as an entry."""
    source = _source()
    # nothing but the nav in front of the chapter
    source.spine = ["nav", source.get_item_with_id("chapter")]

    rebuilt = _rebuild(source)

    assert [name for name, _ in _credits_of(rebuilt)] == ["chapter.xhtml"]


def test_a_book_with_nowhere_to_put_the_line_is_refused_before_it_is_touched():
    """The stamp is all-or-nothing: it fails before it has written
    anything, so the caller's warning is true."""
    from book_maker.loader import disclosure

    book = _rebuild(_source(), disclose=False)
    for item in list(book.get_items()):
        book.items.remove(item)
    book.spine = []
    before = len(book.items)

    with pytest.raises(ValueError):
        disclosure.stamp_disclosure(book, "x/y")

    assert len(book.items) == before


def test_a_document_with_no_body_is_passed_over(tmp_path):
    """A fragment with nothing to append to costs the next candidate, never
    a broken page."""
    broken = _page("broken", "broken.xhtml", "", "Broken")
    broken.content = b"<html><head><title>Broken</title></head></html>"
    source = _source()
    source.add_item(broken)
    source.spine = ["nav", broken, source.get_item_with_id("chapter")]

    rebuilt = _rebuild(source)

    assert [name for name, _ in _credits_of(rebuilt)] == ["chapter.xhtml"]


# ------------------------------------------------- what did the work


def _service(cls, model=None):
    """A registered translator with nothing constructed behind it."""
    translator = cls.__new__(cls)
    translator.model = model
    return lambda: translator


def test_an_engine_is_named_the_way_a_reader_knows_it(tmp_path, monkeypatch):
    """ "Translated by google, 2026." is a registry key leaking onto the
    page."""
    from book_maker.translator import FORMAT_DICT, Google

    # The name is looked up by registry identity, and the hermetic harness
    # (when tests/hermetic is on PYTHONPATH) swaps the google entry for an
    # offline stand-in; the real class must be registered to be named.
    monkeypatch.setitem(FORMAT_DICT, "google", Google)

    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE), model=_service(Google))

    ((_, text),) = _credits_of(rebuilt)
    assert text == f"{CREDIT_PREFIX}Google Translate, {date.today().year}."


def test_a_model_route_is_named_by_its_model(tmp_path):
    from book_maker.translator import ChatGPTAPI

    rebuilt = _rebuild(
        _source(guide=TITLE_PAGE_GUIDE), model=_service(ChatGPTAPI, "gpt-x")
    )

    ((_, text),) = _credits_of(rebuilt)
    assert text == f"{CREDIT_PREFIX}gpt-x, {date.today().year}."


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


def test_an_unnamed_engine_falls_back_to_its_registry_key(monkeypatch):
    """Honest rather than pretty: a service with no display name of its own
    is at least named by the key it is selected with."""
    from book_maker.loader import disclosure
    from book_maker.translator import FORMAT_DICT, Google

    monkeypatch.setitem(FORMAT_DICT, "google", Google)
    monkeypatch.setattr(disclosure, "ENGINE_NAMES", {})

    assert credit_name(_service(Google)()) == "google"


class RotatingModel(StubModel):
    """A run given --model_list may use any of them — the openai translator's
    shape, where `_model_names` is the readable list beside the cycle."""

    model = "a"
    _model_names = ["a", "b"]


def test_a_model_list_run_names_every_model_it_could_have_used(tmp_path):
    """Which model a given paragraph went to is not knowable from here, so
    all of them are named; picking the first would be a false statement."""
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE), model=RotatingModel)

    ((_, text),) = _credits_of(rebuilt)
    assert text == f"{CREDIT_PREFIX}a, b, {date.today().year}."


# ------------------------------------------------------------- the off switch


def test_nothing_is_disclosed_when_disclosure_is_off(tmp_path):
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE), disclose=False)
    opf = _written_opf(tmp_path, rebuilt)

    assert _credits_of(rebuilt) == []
    assert "bbm" not in opf


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
    rebuilt = loader._make_new_book(loader.origin_book)
    loader._stamp_disclosure(rebuilt)
    assert _credits_of(rebuilt) == []


def test_disclosure_is_on_by_default(tmp_path):
    source_path = tmp_path / "book.epub"
    epub.write_epub(str(source_path), _source())

    loader = EPUBBookLoader(
        str(source_path), StubModel, key="", resume=False, language="zh-hans"
    )

    assert loader.disclose is True


# ------------------------------------------------------- rebuilding a rebuild


def test_a_rebuild_of_a_translated_book_stacks_nothing(tmp_path):
    """Run the whole tool on its own output and the line is replaced, not
    doubled: one credit, naming the model that did *this* run."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source(guide=TITLE_PAGE_GUIDE))

    once = _translate_file(source)
    twice = _translate_file(once, model=ModelB)

    assert _credits_in_zip(once) == [
        ("EPUB/title.xhtml", f"{CREDIT_PREFIX}x/y, {date.today().year}.")
    ]
    assert _credits_in_zip(twice) == [
        ("EPUB/title.xhtml", f"{CREDIT_PREFIX}vendor/b, {date.today().year}.")
    ]


def test_the_previous_line_is_removed_before_it_can_be_translated(tmp_path):
    """The load-bearing half of the rerun: a line left in the source is a
    paragraph like any other, so the next run would translate it and insert
    the translation beside it — where no later pass could tell the two
    apart."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source(guide=TITLE_PAGE_GUIDE))
    once = _translate_file(source)

    twice = _translate_file(once, model=ModelB)

    with zipfile.ZipFile(twice) as archive:
        page = archive.read("EPUB/title.xhtml").decode("utf-8")
    assert page.count(CREDIT_PREFIX) == 1
    assert "x/y" not in page
    # not even a translated ghost of it
    assert f"T{CREDIT_PREFIX}" not in page


def test_a_rebuild_with_disclosure_off_carries_no_prior_line(tmp_path):
    """Prior disclosure is stripped whether or not a fresh one is written —
    otherwise --no_disclosure would leave the *previous* run's claim
    standing, which is the worst of both."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source(guide=TITLE_PAGE_GUIDE))
    once = _translate_file(source)

    twice = _translate_file(once, model=ModelB, disclose=False)

    assert _credits_in_zip(twice) == []
    with zipfile.ZipFile(twice) as archive:
        assert "x/y" not in archive.read("EPUB/title.xhtml").decode("utf-8")


# ------------------------------------- what an older build left behind


def _old_apparatus_source():
    """A book carrying every piece of the apparatus this build dropped."""
    colophon = epub.EpubHtml(
        title="Translation note", file_name=LEGACY_COLOPHON_FILE, lang="en"
    )
    colophon.id = LEGACY_COLOPHON_ID
    colophon.content = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n  <head>\n'
        "    <title>Translation note</title>\n"
        f'    <meta name="generator" content="{GENERATOR_MARK}"/>\n'
        "  </head>\n  <body>\n    <h1>Translation Credits</h1>\n"
        "    <p>Model: old/model</p>\n  </body>\n</html>\n"
    ).encode("utf-8")
    source = _source(guide=TITLE_PAGE_GUIDE, extra_items=[colophon])
    source.add_metadata(
        "DC", "contributor", TOOL_NAME_IN_A_CREDIT, {"id": LEGACY_CONTRIBUTOR_ID}
    )
    source.add_metadata(
        None,
        "meta",
        "trl",
        {
            "refines": f"#{LEGACY_CONTRIBUTOR_ID}",
            "property": "role",
            "scheme": "marc:relators",
        },
    )
    source.add_metadata(
        "DC",
        "contributor",
        f"{TOOL_NAME_IN_A_CREDIT} aaaaaaa",
        {"id": LEGACY_PRODUCER_ID},
    )
    source.add_metadata(
        None,
        "meta",
        "bkp",
        {
            "refines": f"#{LEGACY_PRODUCER_ID}",
            "property": "role",
            "scheme": "marc:relators",
        },
    )
    source.add_metadata(
        "DC", "description", f"AI translation (old/model, 2025{DESCRIPTION_TAIL}"
    )
    for name, content in (
        ("bbm:bilingual_book_maker", "aaaaaaa"),
        ("bbm:model", "old/model"),
        ("bbm:date", "2025-01-01"),
        ("bbm:endpoint", "old.example.org"),
    ):
        source.add_metadata("OPF", "meta", None, {"name": name, "content": content})
    return source


def test_a_rerun_over_an_old_style_book_leaves_none_of_its_apparatus(tmp_path):
    """Old apparatus stripped, one credit line written, and nothing of the
    previous run's claim standing anywhere."""
    source = tmp_path / "old.epub"
    epub.write_epub(str(source), _old_apparatus_source())

    output = _translate_file(source, model=ModelB)

    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        opf_name = next(n for n in members if n.endswith(".opf"))
        opf = archive.read(opf_name).decode("utf-8")
    assert "bbm:" not in opf
    assert "<dc:contributor" not in opf
    assert "<dc:description" not in opf
    assert "marc:relators" not in opf
    assert "old/model" not in opf and "aaaaaaa" not in opf
    assert not [m for m in members if "translation_note" in m]
    assert _credits_in_zip(output) == [
        ("EPUB/title.xhtml", f"{CREDIT_PREFIX}vendor/b, {date.today().year}.")
    ]


def test_the_old_closing_page_goes_even_with_disclosure_off(tmp_path):
    source = tmp_path / "old.epub"
    epub.write_epub(str(source), _old_apparatus_source())

    output = _translate_file(source, model=ModelB, disclose=False)

    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        opf_name = next(n for n in members if n.endswith(".opf"))
        opf = archive.read(opf_name).decode("utf-8")
    assert not [m for m in members if "translation_note" in m]
    assert "bbm:" not in opf
    assert "<dc:description" not in opf
    assert _credits_in_zip(output) == []


def test_the_books_own_contributors_and_description_are_untouched(tmp_path):
    """Only entries carrying *our* value and role refine go."""
    source = _source(
        metadata=[
            ("DC", "contributor", "A. Editor", {"id": "ed"}),
            ("DC", "description", "The publisher's blurb.", None),
            ("DC", "contributor", TOOL_NAME_IN_A_CREDIT, {"id": "thanks"}),
        ]
    )

    opf = _written_opf(tmp_path, _rebuild(source))

    assert '<dc:contributor id="ed">A. Editor</dc:contributor>' in opf
    assert "The publisher's blurb." in opf
    # credited without a trl refine: the book's statement, not a stamp
    assert '<dc:contributor id="thanks">' in opf


def test_a_publishers_own_machine_translation_note_survives(tmp_path):
    """A publisher's `<dc:description>Machine translation (French edition)`
    is a statement about the book, not a stamp this tool wrote."""
    source = _source(
        metadata=[("DC", "description", "Machine translation (French edition)", None)]
    )

    opf = _written_opf(tmp_path, _rebuild(source))

    assert "Machine translation (French edition)" in opf
    assert opf.count("<dc:description>") == 1


# ---------------------------------------------------------- calibre's record


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


class _StoredListStub:
    """Codex's shape: `set_model_list` keeps every name, but every request
    goes to `self.model`. Naming them all would be a false claim."""

    model = "a"
    model_name = "a"
    model_list = ["a", "b"]


def test_a_stored_model_list_without_rotation_names_one_model():
    """A finite `model_list` was once read as rotation. Only
    `_model_names`, which the rotating translator keeps, means that."""
    assert model_id(_StoredListStub()) == "a"


# ------------------------------- names that do not collide


def _output_of(tmp_path, source, name="book.epub"):
    path = tmp_path / name
    epub.write_epub(str(path), source)
    return _translate_file(path)


def _members_and_opf(output):
    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        opf_name = next(n for n in members if n.endswith(".opf"))
        return members, archive.read(opf_name).decode("utf-8")


def _all_ids(opf):
    return re.findall(r'\bid="([^"]+)"', opf)


def _assert_sound(members, opf):
    assert len(members) == len(set(members)), "a duplicate zip member"
    ids = _all_ids(opf)
    assert len(ids) == len(set(ids)), f"a duplicate id: {ids}"


def _text_of(tmp_path, file_name):
    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        return archive.read(f"EPUB/{file_name}").decode("utf-8")


def test_a_publishers_colophon_is_not_taken_over(tmp_path):
    """An `id="colophon"` on something of the book's own, and a
    `colophon.xhtml` of its own, must keep their id, their file and their
    place."""
    source = _source(guide=TITLE_PAGE_GUIDE)
    source.add_item(_page("colophon", "colophon.xhtml", "<p>Set in Bembo.</p>"))
    source.spine.append(source.get_item_with_id("colophon"))

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert 'id="colophon"' in opf
    assert "EPUB/colophon.xhtml" in members
    assert "Bembo" in _text_of(tmp_path, "colophon.xhtml")


def test_a_page_carrying_an_old_builds_id_is_still_content(tmp_path):
    """Recognition is by the marker inside the document, never by id — a
    chapter that happens to carry an old build's is translated and kept."""
    source = _source(guide=TITLE_PAGE_GUIDE)
    chapter = _page(
        LEGACY_COLOPHON_ID, "chapter-two.xhtml", "<p>Chapter two begins.</p>"
    )
    source.add_item(chapter)
    source.spine.append(chapter)

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "EPUB/chapter-two.xhtml" in members
    body = _text_of(tmp_path, "chapter-two.xhtml")
    assert "Chapter two begins." in body
    assert "TChapter two begins." in body  # StubModel's translation, still done


# ------------------------------------- --retranslate copies items


def test_retranslate_does_not_copy_the_previous_credit(tmp_path):
    """`retranslate_book` copies every item but the one it edits, so a
    previous output's line was carried into the new book.

    Retranslated with a different model, so a stale line surviving is
    visible: keeping the old one is not merely a duplicate, it is the wrong
    claim about who did the work.
    """
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source(guide=TITLE_PAGE_GUIDE))
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
    assert _credits_in_zip(once) == [
        ("EPUB/title.xhtml", f"{CREDIT_PREFIX}vendor/b, {date.today().year}.")
    ]


# ------------------------------------------ the recovery replay


def _interrupted_loader(tmp_path, disclose=True, model=ModelB):
    """A loader part-way through translating a previous output."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source(guide=TITLE_PAGE_GUIDE))
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


def test_the_recovery_book_survives_a_second_generation_source(tmp_path):
    """`_save_temp_book` asks for one plan per document; an interrupted run
    over a previous output must not die on the arithmetic."""
    loader, once = _interrupted_loader(tmp_path, model=StubModel)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    assert temp.exists()
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)


def test_the_recovery_book_carries_this_runs_line_not_the_last_ones(tmp_path):
    """The replay copies the source's documents; the previous run's line
    must not be among them."""
    loader, once = _interrupted_loader(tmp_path)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)
    assert _credits_in_zip(temp) == [
        ("EPUB/title.xhtml", f"{CREDIT_PREFIX}vendor/b, {date.today().year}.")
    ]


def test_the_recovery_book_carries_no_line_with_disclosure_off(tmp_path):
    """--no_disclosure kept the previous run's note once, which is the one
    outcome the flag exists to prevent."""
    loader, once = _interrupted_loader(tmp_path, disclose=False)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)
    assert _credits_in_zip(temp) == []


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
    """Hours of translation are not worth losing over the line at the front
    of it. The book goes out unstamped and the run says so."""
    from book_maker.loader import epub_loader

    def boom(*args, **kwargs):
        raise RuntimeError("no room on the title page")

    monkeypatch.setattr(epub_loader, "stamp_disclosure", boom)
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE))

    assert _credits_of(rebuilt) == []
    said = _said(capsys)
    assert "could not be marked as a machine translation" in said
    assert "no room on the title page" in said
    # The warning has to say what is missing, not just that something is.
    assert "nothing in the file will say it was translated by a machine" in said


def test_a_stamp_that_fails_halfway_leaves_nothing_behind(monkeypatch):
    """The line and the record stand or fall together. A record naming a
    model with no line in front of the reader says the opposite of what the
    ruling asks for, and the caller is entitled to write the book after a
    failure."""
    from book_maker.loader import disclosure

    class BoomRecord:
        glossary_bytes = None

        def record(self, when=None):
            raise RuntimeError("cannot build the record")

    # Built with the stamp off, so this exercises `stamp_disclosure` alone
    # and not the loader's own guard around it.
    book = _rebuild(_source(guide=TITLE_PAGE_GUIDE), disclose=False)
    before = len(book.items)

    with pytest.raises(RuntimeError):
        disclosure.stamp_disclosure(book, "x/y", translation_metadata=BoomRecord())

    assert _credits_of(book) == []
    assert len(book.items) == before


def test_a_stale_credit_that_cannot_be_removed_costs_a_warning(capsys, monkeypatch):
    """The strip runs on every source. A book whose documents cannot be read
    must not take the translation down with it, but the operator has to hear
    that the output may name two translators."""
    from book_maker.loader import epub_loader

    def boom(*args, **kwargs):
        raise AttributeError("no content")

    monkeypatch.setattr(epub_loader, "strip_prior_credit", boom)
    rebuilt = _rebuild(_source(guide=TITLE_PAGE_GUIDE))

    assert rebuilt.get_metadata("DC", "title")
    said = _said(capsys)
    assert "earlier translation credit could not be removed" in said
    assert "may end up naming two translators" in said


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
