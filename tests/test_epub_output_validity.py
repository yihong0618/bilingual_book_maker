"""The output must be a valid EPUB, not merely a translated one.

Every case here reproduces an epubcheck finding that the unfixed code
produced on the IDPF/W3C epub3-samples books — the codes are named in the
docstrings so a failure points straight at what a reading system would
reject.
"""

import uuid
import zipfile
from copy import copy

from bs4 import BeautifulSoup as bs
from ebooklib import epub

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import (
    EPUBBookLoaderHelper,
    backfill_toc_hrefs,
    derive_translation_identity,
    rebase_ncx_srcs,
    strip_duplicate_ids,
)


def _helper():
    return EPUBBookLoaderHelper(None, None, "", False)


def _soup(html):
    return bs(html, "html.parser")


def _rebuilder(source, language="zh-hans", single=False):
    """A loader with only what `_make_new_book` touches."""
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source
    loader.language = language
    loader.single_translate = single
    return loader


def _loader():
    """A loader with only what the insertion paths touch."""
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = type("M", (), {"TRANSLATION_ERROR_MARKER": None})()
    loader.exclude_translate_tags = "sup,code"
    loader.helper = _helper()
    return loader


# ----------------------------------------------------------- duplicate ids


def test_bilingual_clone_carries_no_id():
    """RSC-005 "Duplicate ID": the clone must not answer to the original's
    fragment identifier, or an internal link may land on the translation."""
    soup = _soup('<body><p id="c1_h">Chapter One</p></body>')
    p = soup.find("p")
    _helper().insert_trans(p, "第一章")

    paragraphs = soup.find_all("p")
    assert len(paragraphs) == 2
    assert paragraphs[0].get("id") == "c1_h"
    assert paragraphs[1].get("id") is None


def test_the_original_keeps_every_anchor_it_had():
    soup = _soup('<body><p id="p1">See <a id="ref1" href="#x">note</a>.</p></body>')
    _helper().insert_trans(soup.find("p"), "翻译")

    original, translation = soup.find_all("p")
    assert original.get("id") == "p1"
    assert original.find("a").get("id") == "ref1"
    assert translation.get("id") is None


def test_strip_duplicate_ids_clears_the_whole_subtree():
    """The clone's own text is replaced today, so only its outer id can
    survive — but the helper is what guarantees a clone is never a second
    anchor, and it has to hold for a caller that keeps the children."""
    soup = _soup('<div id="d1"><p id="p1"><a id="a1" href="#x">note</a></p></div>')
    stripped = strip_duplicate_ids(copy(soup.find("div")))

    assert stripped.get("id") is None
    assert [tag.get("id") for tag in stripped.find_all(True)] == [None, None]
    # the source is untouched
    assert soup.find("div").get("id") == "d1"


def test_single_translate_keeps_the_original_ids():
    """The original is extracted, so nothing is duplicated and the book's
    own anchors must survive — dropping them would break every link."""
    soup = _soup('<body><p id="c1_h">Chapter One</p></body>')
    _helper().insert_trans(soup.find("p"), "第一章", single_translate=True)

    paragraphs = soup.find_all("p")
    assert len(paragraphs) == 1
    assert paragraphs[0].get("id") == "c1_h"
    assert paragraphs[0].get_text() == "第一章"


# ------------------------------------------------- restricted content models


def test_figcaption_translation_stays_inside_the_caption():
    """RSC-005: a <figure> accepts one <figcaption>; a translated sibling
    gives it two."""
    soup = _soup(
        "<body><figure><img src='x.png' alt=''/>"
        "<figcaption>Figure 1. A tree</figcaption></figure></body>"
    )
    _helper().insert_trans(soup.find("figcaption"), "图1。一棵树")

    figure = soup.find("figure")
    assert len(figure.find_all("figcaption")) == 1
    assert "图1。一棵树" in figure.find("figcaption").get_text()


def test_nav_link_translation_goes_inside_the_link():
    """RSC-005: an EPUB 3 nav <li> accepts "(a | span), ol?" — a second <a>
    beside the first is invalid."""
    soup = _soup(
        "<body><nav epub:type='toc'><ol>"
        "<li><a href='ch01.xhtml'>Chapter One</a></li>"
        "</ol></nav></body>"
    )
    _helper().insert_trans(soup.find("a"), "第一章")

    li = soup.find("li")
    assert len(li.find_all("a")) == 1
    # the translation sits on its own line inside the link, not run into
    # the original's text
    assert li.find("a").find("br") is not None
    assert li.get_text(separator=" ").split() == ["Chapter", "One", "第一章"]


def test_nav_list_item_translation_goes_inside_its_link_not_after_the_sublist():
    """The whole <li> is the translatable owner when a partition owns the
    entry's text. Appending to the <li> is as invalid as a sibling: with a
    nested <ol> present, epubcheck reports "element span not allowed here;
    expected the element end-tag or element ol". The entry's text lives in
    its <a>, and so does its translation.
    """
    soup = _soup(
        "<body><nav epub:type='toc'><ol>"
        "<li><a href='pr01.xhtml'>Preface</a>"
        "<ol><li><a href='pr01s02.xhtml'>Acknowledgments</a></li></ol>"
        "</li></ol></nav></body>"
    )
    outer = soup.find("li")
    _helper().insert_trans(outer.find("a", recursive=False), "前言")

    # the translation is inside the entry's own link, on its own line …
    link = outer.find("a", recursive=False)
    assert link.find("br") is not None
    assert link.get_text(separator=" ") == "Preface 前言"
    # … and the <li>'s direct children are still exactly (a, ol)
    assert [c.name for c in outer.find_all(recursive=False)] == ["a", "ol"]


def test_nav_list_item_owner_targets_its_link():
    """Same shape, but the caller hands over the <li> itself — which is what
    a whole-block partition does."""
    soup = _soup(
        "<body><nav epub:type='toc'><ol>"
        "<li><a href='pr01.xhtml'>Preface</a>"
        "<ol><li><a href='pr01s02.xhtml'>Acknowledgments</a></li></ol>"
        "</li></ol></nav></body>"
    )
    outer = soup.find("li")
    _helper().insert_trans(outer, "前言")

    assert [c.name for c in outer.find_all(recursive=False)] == ["a", "ol"]
    assert outer.find("a", recursive=False).get_text(separator=" ") == "Preface 前言"


def test_nav_heading_keeps_its_translation_inside():
    """A nav document allows one heading before its <ol>."""
    soup = _soup(
        "<body><nav epub:type='toc'><h2>Table of Contents</h2>"
        "<ol><li><a href='ch01.xhtml'>One</a></li></ol></nav></body>"
    )
    _helper().insert_trans(soup.find("h2"), "目录")

    nav = soup.find("nav")
    assert len(nav.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])) == 1
    assert "目录" in nav.find("h2").get_text()


def test_an_ordinary_paragraph_still_gets_a_translated_sibling():
    """The rule must not overreach: everywhere else, bilingual output is a
    second block, which is what makes it readable side by side."""
    soup = _soup("<body><p>Chapter One</p></body>")
    _helper().insert_trans(soup.find("p"), "第一章")

    assert [p.get_text() for p in soup.find_all("p")] == ["Chapter One", "第一章"]


def test_a_figcaption_outside_a_figure_is_still_treated_as_a_singleton():
    soup = _soup("<body><figcaption>Figure 1</figcaption></body>")
    _helper().insert_trans(soup.find("figcaption"), "图1")

    assert len(soup.find_all("figcaption")) == 1


# ------------------------------------------- the excluded-tag insertion path


def test_preserving_tags_path_drops_ids_on_the_clone():
    """The code-tag branch clones the paragraph itself, so it needs the
    same treatment as the plain path."""
    loader = _loader()
    soup = _soup('<body><p id="p1">Run <code>ls</code> first.</p></body>')
    loader._insert_trans_preserving_tags(soup.find("p"), "先运行")

    paragraphs = soup.find_all("p")
    assert len(paragraphs) == 2
    assert paragraphs[1].get("id") is None
    assert paragraphs[1].find("code") is None


def test_preserving_tags_path_respects_restricted_containers():
    loader = _loader()
    soup = _soup(
        "<body><figure><figcaption>Listing <code>ls</code> 1</figcaption>"
        "</figure></body>"
    )
    loader._insert_trans_preserving_tags(soup.find("figcaption"), "清单1")

    assert len(soup.find("figure").find_all("figcaption")) == 1
    assert "清单1" in soup.get_text()


# --------------------------------------------------------------- the OPF


def test_new_book_gets_an_ncx_when_the_source_has_none(tmp_path):
    """OPF-049 "Item id ncx was not found in the manifest": ebooklib always
    writes <spine toc="ncx">, so the reference dangles without the item."""
    source = epub.EpubBook()
    source.set_title("No NCX Here")
    source.set_language("en")

    rebuilt = _rebuilder(source)._make_new_book(source)

    assert [i for i in rebuilt.get_items() if isinstance(i, epub.EpubNcx)]


def test_new_book_does_not_add_a_second_ncx(tmp_path):
    source = epub.EpubBook()
    source.set_title("Has NCX")
    source.set_language("en")
    source.add_item(epub.EpubNcx())

    rebuilt = _rebuilder(source)._make_new_book(source)

    assert len([i for i in rebuilt.get_items() if isinstance(i, epub.EpubNcx)]) <= 1


def test_link_metadata_is_dropped_rather_than_written_as_meta(tmp_path):
    """RSC-005 'attribute "rel" not allowed here': ebooklib parses OPF
    <link rel=… href=…> into metadata but writes it back as <meta>, where
    neither attribute is legal."""
    source = epub.EpubBook()
    source.set_title("Linked")
    source.set_language("en")
    # the shape ebooklib's reader produces for <link rel=… href=…>: the OPF
    # namespace, a null value, and the attributes in `others`. Building it
    # under any other namespace would be dropped by the namespace filter
    # instead, and the test would pass without exercising this rule.
    source.add_metadata(
        epub.NAMESPACES["OPF"],
        "link",
        None,
        {
            "rel": "dcterms:conformsTo",
            "href": "http://www.idpf.org/epub/a11y/accessibility-20170105.html",
        },
    )

    rebuilt = _rebuilder(source)._make_new_book(source)

    for namespace, entries in rebuilt.metadata.items():
        assert "link" not in entries, f"link metadata survived in {namespace!r}"

    out = tmp_path / "linked.epub"
    epub.write_epub(str(out), rebuilt)
    with zipfile.ZipFile(out) as book:
        opf = next(n for n in book.namelist() if n.endswith(".opf"))
        content = book.read(opf).decode("utf-8")
    assert 'rel="dcterms:conformsTo"' not in content


# ------------------------------------------------------- the book's identity


def _opf_of(path):
    with zipfile.ZipFile(path) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return archive.read(name).decode("utf-8")


def _identified_source(uid="http://www.gutenberg.org/ebooks/25545"):
    source = epub.EpubBook()
    source.set_identifier(uid)
    source.set_title("Identified")
    source.set_language("en")
    return source


def test_translation_identity_is_deterministic_and_distinct():
    """Rebuilding the same translation must name the same book — ebooklib's
    per-run uuid makes every rerun a new library entry — but it must not be
    the source's identifier either, or a reader deduplicates the
    translation against the original."""
    source = _identified_source()

    first = _rebuilder(source)._make_new_book(source)
    second = _rebuilder(source)._make_new_book(source)

    assert first.uid == second.uid
    assert first.uid != source.uid
    uuid.UUID(first.uid)  # a well-formed uuid, not a concatenation


def test_translation_identity_varies_with_language_and_mode():
    """zh and ja translations of one source are different books, and so are
    the bilingual and single renderings."""
    source = _identified_source()

    zh = _rebuilder(source, "zh-hans")._make_new_book(source).uid
    ja = _rebuilder(source, "ja")._make_new_book(source).uid
    single = _rebuilder(source, "zh-hans", single=True)._make_new_book(source).uid

    assert len({zh, ja, single}) == 3


def test_source_identifier_is_kept_without_a_colliding_id(tmp_path):
    """RSC-005 'Duplicate "id"': the derived identity is written under
    ebooklib's id="id"; a source identifier that also carries id="id" must
    lose the attribute, not the value."""
    source = _identified_source()
    rebuilt = _rebuilder(source)._make_new_book(source)

    out = tmp_path / "identified.epub"
    epub.write_epub(str(out), rebuilt)
    opf = _opf_of(out)

    assert opf.count('id="id"') == 1, "two elements answer to the same id"
    assert rebuilt.uid in opf
    assert "http://www.gutenberg.org/ebooks/25545" in opf


def test_an_identifierless_source_derives_nothing():
    """No identifier means nothing stable to derive from; the helper must
    say so instead of minting an identity out of thin air."""
    new_book = epub.EpubBook()
    before = new_book.uid

    class Bare:
        uid = None

    assert derive_translation_identity(new_book, Bare(), "zh-hans") is None
    assert new_book.uid == before  # untouched


# ------------------------------------------------------------------ the NCX


def test_a_section_without_an_href_is_given_one():
    """RSC-010: ebooklib writes <content src=""/> for an hrefless Section —
    a TOC entry that navigates nowhere on NCX-reading systems."""
    toc = [
        (
            epub.Section("Part One"),
            [epub.Link("ch01.xhtml", "One", "ch01")],
        )
    ]

    backfill_toc_hrefs(toc)

    assert toc[0][0].href == "ch01.xhtml"


def test_nested_sections_are_backfilled_from_their_own_subtree():
    inner = (epub.Section("Inner"), [epub.Link("ch02.xhtml", "Two", "ch02")])
    outer = (epub.Section("Outer"), [inner])

    backfill_toc_hrefs([outer])

    assert inner[0].href == "ch02.xhtml"
    assert outer[0].href == "ch02.xhtml"


def test_a_section_with_an_href_keeps_it():
    section = epub.Section("Named", href="intro.xhtml")
    toc = [(section, [epub.Link("ch01.xhtml", "One", "ch01")])]

    backfill_toc_hrefs(toc)

    assert section.href == "intro.xhtml"


NCX = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
    "<navMap>{}</navMap></ncx>"
).format


def _srcs(ncx_bytes):
    soup = bs(ncx_bytes, "html.parser")
    return [c["src"] for c in soup.find_all("content")]


def test_a_subdirectory_ncx_gets_its_srcs_rebased():
    """RSC-007: ebooklib writes navpoint srcs relative to the OPF root, but
    the writer keeps the imported NCX's own location — from xhtml/toc.ncx a
    root-relative src resolves to xhtml/xhtml/…, a file that does not
    exist (kusamakura)."""
    ncx = NCX(
        '<navPoint id="a"><content src="xhtml/one.xhtml"/></navPoint>'
        '<navPoint id="b"><content src="xhtml/two.xhtml#frag"/></navPoint>'
    ).encode()

    out = rebase_ncx_srcs(ncx, "xhtml/toc.ncx")

    assert _srcs(out) == ["one.xhtml", "two.xhtml#frag"]


def test_a_root_ncx_passes_through_byte_identical():
    ncx = NCX('<navPoint id="a"><content src="xhtml/one.xhtml"/></navPoint>').encode()

    assert rebase_ncx_srcs(ncx, "toc.ncx") is ncx


def test_srcs_outside_the_ncx_directory_step_out_of_it():
    ncx = NCX('<navPoint id="a"><content src="text/ch1.xhtml"/></navPoint>').encode()

    assert _srcs(rebase_ncx_srcs(ncx, "nav/toc.ncx")) == ["../text/ch1.xhtml"]


def test_absolute_and_empty_srcs_are_left_alone():
    ncx = NCX(
        '<navPoint id="a"><content src="https://example.com/x"/></navPoint>'
        '<navPoint id="b"><content src=""/></navPoint>'
    ).encode()

    assert _srcs(rebase_ncx_srcs(ncx, "xhtml/toc.ncx")) == [
        "https://example.com/x",
        "",
    ]
