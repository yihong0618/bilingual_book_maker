"""Tests for the coverage-complete translation plan (partition, don't select).

Fixtures:
- test_books/animal_farm.epub  (committed) — poem lines are per-line
  <blockquote class="calibre_14|calibre_17">; default tag selection missed them.
- gilgamesh.epub (repo root, local only, 3.8MB) — 51% of text lives in
  div.poetry_line*; tests skip if the file is absent.
"""

import os
import shutil
import zipfile
from io import StringIO
from pathlib import Path

import pytest
from bs4 import BeautifulSoup as bs
from ebooklib import epub
from rich.console import Console
from rich.markup import escape

from book_maker.loader.plan import (
    DisplayResolver,
    build_plan,
    classify_skip,
    is_trivial_unit,
    parse_css_display,
    partition_soup,
    unit_clean_text,
)

REPO = Path(__file__).resolve().parent.parent
ANIMAL_FARM = REPO / "test_books" / "animal_farm.epub"
GILGAMESH = REPO / "gilgamesh.epub"

needs_gilgamesh = pytest.mark.skipif(
    not GILGAMESH.exists(), reason="local-only fixture gilgamesh.epub not present"
)


# ---------------------------------------------------------------- predicates


class TestClassifySkip:
    def test_translatable_prose_is_not_skipped(self):
        assert classify_skip("He who saw the Deep, the country's foundation,") is None
        assert classify_skip("Beasts of England, beasts of Ireland,") is None
        # single real word must survive, even a pronoun
        assert classify_skip("Overthrown") is None

    def test_whitespace_and_empty(self):
        assert classify_skip("") == "whitespace"
        assert classify_skip("  \n ") == "whitespace"

    def test_numeric(self):
        assert classify_skip("5") == "numeric"
        assert classify_skip("120") == "numeric"
        assert classify_skip("3 4 5") == "numeric"

    def test_decimals_percentages_and_currency(self):
        # upstream #451: bare figures and percentages are not worth a request
        assert classify_skip("3.14") == "numeric"
        assert classify_skip("-17") == "numeric"
        assert classify_skip("1,234.56") == "numeric"
        assert classify_skip("42%") == "numeric"
        assert classify_skip("50 %") == "numeric"
        assert classify_skip("$3.99") == "numeric"
        assert classify_skip("1 234,50 €") == "numeric"
        assert classify_skip("±0.5") == "numeric"
        # ... but a figure with any prose attached still gets translated
        assert classify_skip("42% of the herd") is None
        assert classify_skip("about 3.14") is None
        # a lone symbol has no digits, so it stays a symbol
        assert classify_skip("%") == "symbol"

    def test_roman_line_refs(self):
        # gilgamesh span.mr / span.mn / span.line_number contents
        assert classify_skip("I 5") == "roman-ref"
        assert classify_skip("XI 100") == "roman-ref"
        assert classify_skip("IV") == "roman-ref"

    def test_symbols(self):
        assert classify_skip("↓") == "symbol"  # ↓ span.arrow
        assert classify_skip("+") == "symbol"
        assert classify_skip("* * *") == "symbol"

    def test_links(self):
        assert classify_skip("http://example.com/x") == "link"


class TestTrivialUnits:
    def test_sigla_and_markers_are_trivial(self):
        # manuscript sigla in Gilgamesh's tablet tables, stray initials
        for text in ["(a)", "M", "aa", "Kf", "CJ", "1.F.", "x1", "ii"]:
            assert is_trivial_unit(text), text

    def test_real_short_content_is_kept(self):
        for text in ["THE END", "Pages", "Enkidu:", "cypress", "One ….…..,"]:
            assert not is_trivial_unit(text), text

    def test_cjk_is_dense_two_chars_is_a_word(self):
        assert not is_trivial_unit("檸檬")  # lemo.epub's entire title
        assert not is_trivial_unit("目次")

    def test_prose_tags_exempt_short_dialogue_survives(self):
        # "No." as a standalone paragraph is dialogue, not apparatus
        for tag in ("p", "blockquote", "h2", "figcaption"):
            assert not is_trivial_unit("No.", tag=tag)
            assert not is_trivial_unit("Sí", tag=tag)
        # apparatus containers still filtered
        for tag in ("td", "li", "div", "span", None):
            assert is_trivial_unit("(a)", tag=tag)


# ------------------------------------------------------------- css resolver


class TestCssDisplay:
    def test_parse_simple_rules(self):
        css = """
        /* comment { display:none } */
        .poem { display: block; }
        span.linenum { display : inline-block }
        div.run-in, p.also { display: inline; }
        """
        m = parse_css_display(css)
        assert m[(None, "poem")] == "block"
        assert m[("span", "linenum")] == "inline-block"
        assert m[("div", "run-in")] == "inline"
        assert m[("p", "also")] == "inline"

    def test_resolver_css_overrides_defaults(self):
        css = "span.verse { display: block; } div.note { display: inline; }"
        resolver = DisplayResolver([parse_css_display(css)])
        soup = bs(
            '<div><span class="verse">a</span><div class="note">b</div>'
            "<span>c</span><p>d</p></div>",
            "html.parser",
        )
        assert resolver.is_block(soup.find("span", class_="verse"))
        assert not resolver.is_block(soup.find("div", class_="note"))
        assert not resolver.is_block(soup.find_all("span")[1])
        assert resolver.is_block(soup.find("p"))


# ---------------------------------------------------------------- partition

MINI_GILGAMESH = """
<body>
 <section class="chapter">
  <h2 class="chapter_title">Tablet I</h2>
  <div class="poetry_stanza">
   <div class="poetry_line"><span class="line_number"><span class="mr">I</span>
    <span class="mn">5</span></span>He who saw the Deep, the country's foundation,</div>
   <div class="poetry_line_indented">who knew the proper ways, was wise in all matters!</div>
  </div>
  <p>Prose paragraph <sup>1</sup> with a footnote marker.</p>
 </body>
"""


class TestPartition:
    def _partition(self, html):
        soup = bs(html, "html.parser")
        resolver = DisplayResolver([])
        return partition_soup(soup, resolver, file_name="x.html"), soup

    def test_units_are_leaf_blocks_only(self):
        fp, soup = self._partition(MINI_GILGAMESH)
        sigs = [u.signature for u in fp.units]
        # stanza wrapper must never be a unit — double-translate impossible
        assert "div.poetry_stanza" not in sigs
        assert "section.chapter" not in sigs
        assert sigs == [
            "h2.chapter_title",
            "div.poetry_line",
            "div.poetry_line_indented",
            "p",
        ]

    def test_line_numbers_are_skipped_not_translated(self):
        fp, _ = self._partition(MINI_GILGAMESH)
        line = next(u for u in fp.units if u.signature == "div.poetry_line")
        assert line.text == "He who saw the Deep, the country's foundation,"
        assert "I" not in line.text.split()
        assert fp.skipped.get("roman-ref", 0) + fp.skipped.get("numeric", 0) > 0

    def test_inline_link_does_not_split_its_paragraph(self):
        # upstream #414: with --translate-tags a,p the <a> was translated on its
        # own and the surrounding text left behind. A unit is the whole block.
        html = (
            '<body><p>Some text before, <a href="#">some link</a>: '
            "some text after</p></body>"
        )
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == [
            "Some text before, some link: some text after"
        ]

    def test_list_items_without_inner_p_are_units(self):
        # upstream #440 / #207: bullet text living directly in <li> was skipped
        # entirely by the default tag list.
        html = (
            "<body><ul><li>bark bark</li><li>meow meow</li></ul>"
            '<ol><li class="li">We could offer niche recipes.<span> </span></li>'
            '<li class="li">Alternately, generic ones.</li></ol></body>'
        )
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == [
            "bark bark",
            "meow meow",
            "We could offer niche recipes.",
            "Alternately, generic ones.",
        ]
        # the <ul>/<ol> wrappers own no text of their own, so no double pass
        assert not any(u.signature in ("ul", "ol") for u in fp.units)

    def test_sup_excluded_by_default(self):
        fp, _ = self._partition(MINI_GILGAMESH)
        p = next(u for u in fp.units if u.signature == "p")
        assert "1" not in p.text
        assert "footnote marker" in p.text

    def test_total_partition_invariant(self):
        fp, _ = self._partition(MINI_GILGAMESH)
        assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
            fp.skipped.values()
        )
        assert fp.total_chars > 0

    def test_page_list_nav_skipped(self):
        html = (
            "<body><p>real prose paragraph</p>"
            '<nav epub:type="page-list"><ol>'
            "<li><a href='x'>iii</a></li><li><a href='y'>overview</a></li>"
            "</ol></nav></body>"
        )
        fp, _ = self._partition(html)
        assert [u.signature for u in fp.units] == ["p"]
        assert fp.skipped.get("pagebreak", 0) > 0

    def test_trivial_units_skipped_with_reason(self):
        html = "<body><table><tr><td>(a)</td><td>Gilgamesh</td></tr></table></body>"
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == ["Gilgamesh"]
        assert fp.skipped.get("trivial", 0) == 3
        # invariant survives the trivial filter
        assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
            fp.skipped.values()
        )

    def test_epub_pagebreak_semantics_skipped(self):
        html = (
            "<body><p>real text here</p>"
            '<span epub:type="pagebreak" title="12">12</span>'
            '<div class="mbp_pagebreak">next!</div></body>'
        )
        fp, _ = self._partition(html)
        assert [u.signature for u in fp.units] == ["p", "div.mbp_pagebreak"]
        # numeric pagebreak content dies by predicate even without epub:type

    def test_mixed_content_block_translates_only_its_own_text(self):
        html = (
            "<body><div>Intro line of the chapter here."
            "<p>Nested paragraph text.</p></div></body>"
        )
        fp, _ = self._partition(html)
        texts = {u.signature: u.text for u in fp.units}
        assert texts["p"] == "Nested paragraph text."
        assert texts["div"] == "Intro line of the chapter here."
        # no unit contains another unit's text
        assert "Nested" not in texts["div"]

    def test_drop_cap_roman_letters_survive_signature_vote(self):
        # "C" of "Cover" in its own span must NOT be eaten as a roman numeral
        # because that span class also carries normal prose letters elsewhere;
        # Gilgamesh's span.mr ("I") IS eaten because its class never does.
        html = (
            "<body>"
            '<p><span class="dcap">C</span><span class="dcap">OVER</span></p>'
            '<p><span class="dcap">T</span><span class="dcap">ITLE</span></p>'
            '<div class="poetry_line"><span class="mr">I</span>'
            "He who saw the Deep</div>"
            "</body>"
        )
        fp, _ = self._partition(html)
        texts = [u.text for u in fp.units]
        assert "COVER" in texts
        assert "TITLE" in texts
        line = next(u for u in fp.units if u.signature == "div.poetry_line")
        assert line.text == "He who saw the Deep"
        assert fp.skipped.get("roman-ref", 0) == 1  # only span.mr's "I"

    def test_roman_numeral_inside_prose_link_is_content(self):
        # Animal Farm's chapter list: <a><span>C</span><span>HAPTER </span>
        # <span>II</span></a> — the subtree reads "CHAPTER II" as a whole, so
        # the numeral is content and must survive.
        html = (
            "<body><p>"
            '<a href="x"><span class="u">C</span><span class="n">HAPTER </span>'
            '<span class="u">II</span></a>'
            "</p></body>"
        )
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == ["CHAPTER II"]
        assert sum(fp.skipped.values()) == 0

    def test_unit_clean_text_is_stateless_recomputable(self):
        _, soup = self._partition(MINI_GILGAMESH)
        el = soup.find("div", class_="poetry_line")
        resolver = DisplayResolver([])
        assert (
            unit_clean_text(el, resolver)
            == "He who saw the Deep, the country's foundation,"
        )


class TestPerDocumentCss:
    def _book(self):
        book = epub.EpubBook()
        book.set_identifier("t")
        book.set_title("t")
        css_hide = epub.EpubItem(
            uid="c1",
            file_name="Styles/hide.css",
            media_type="text/css",
            content=b".note { display: none; }",
        )
        css_plain = epub.EpubItem(
            uid="c2",
            file_name="Styles/plain.css",
            media_type="text/css",
            content=b"p { margin: 0; }",
        )
        d1 = epub.EpubHtml(uid="d1", file_name="Text/one.xhtml")
        d1.content = (
            '<html><head><link rel="stylesheet" href="../Styles/hide.css"/></head>'
            '<body><p>visible one</p><p class="note">hidden note</p></body></html>'
        )
        d2 = epub.EpubHtml(uid="d2", file_name="Text/two.xhtml")
        d2.content = (
            '<html><head><link rel="stylesheet" href="../Styles/plain.css"/></head>'
            '<body><p>visible two</p><p class="note">a normal aside</p></body></html>'
        )
        for it in (css_hide, css_plain, d1, d2):
            book.add_item(it)
        return book

    def test_chapter_local_display_none_stays_local(self):
        # Finding #8: hide.css is linked only by one.xhtml — its
        # .note {display:none} must not hide two.xhtml's .note prose.
        plan = build_plan(self._book())
        texts = {f.file_name: [u.text for u in f.units] for f in plan.files}
        assert "hidden note" not in texts["Text/one.xhtml"]
        assert "a normal aside" in texts["Text/two.xhtml"]

    def test_docs_without_links_fall_back_to_global_css(self):
        book = self._book()
        d3 = epub.EpubHtml(uid="d3", file_name="Text/three.xhtml")
        d3.content = '<body><p class="note">no links here</p></body>'
        book.add_item(d3)
        plan = build_plan(book)
        fp = next(f for f in plan.files if f.file_name == "Text/three.xhtml")
        # global merge includes hide.css, so .note is hidden here
        assert fp.units == []
        assert fp.skipped.get("hidden", 0) > 0


# ------------------------------------------------------------- poetry runs


class TestPoetryGrouping:
    def test_animal_farm_poem_grouped_by_stanza(self):
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book, poetry_group_size=8)
        fp = next(f for f in plan.files if "004" in f.file_name)
        # calibre_14 = stanza head, calibre_17 = continuation; calibre_7 is
        # the chapter heading and must NOT be poetry-grouped with the poem
        poem = [
            u
            for u in fp.units
            if u.signature in ("blockquote.calibre_14", "blockquote.calibre_17")
        ]
        assert len(poem) >= 20  # Beasts of England has 7 stanzas x 4 lines
        assert all(u.group_id is not None for u in poem)
        groups = {}
        for u in poem:
            groups.setdefault(u.group_id, []).append(u)
        for members in groups.values():
            assert 1 <= len(members) <= 8
        # stanza heads (calibre_14) start groups
        multi = [g for g in groups.values() if len(g) > 1]
        assert multi, "poem lines must be batched together for context"
        for members in multi:
            assert members[0].signature == "blockquote.calibre_14"

    def test_prose_paragraphs_not_poetry_grouped(self):
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book)
        fp = next(f for f in plan.files if "004" in f.file_name)
        prose = [u for u in fp.units if u.signature == "p.calibre_13"]
        assert prose and all(u.group_id is None for u in prose)


# ------------------------------------------------------------ whole books


class TestAnimalFarmPlan:
    def test_coverage_and_invariant(self):
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book)
        assert plan.coverage >= 0.95
        for fp in plan.files:
            assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
                fp.skipped.values()
            )

    def test_poem_text_is_covered(self):
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book)
        all_text = " ".join(u.text for f in plan.files for u in f.units)
        assert "Beasts of England, beasts of Ireland," in all_text
        assert "Tyrant Man shall be o'erthrown," in all_text.replace("’", "'")


@needs_gilgamesh
class TestGilgameshPlan:
    @pytest.fixture(scope="class")
    def plan(self):
        return build_plan(epub.read_epub(str(GILGAMESH)))

    def test_coverage(self, plan):
        # default tag selection covered only 45% of this book
        assert plan.coverage >= 0.90

    def test_nested_units_own_disjoint_text(self, plan):
        # Mixed-content ancestors may be units for their OWN direct text, but
        # a descendant unit's text must never be duplicated into an ancestor
        # unit (single ownership of every text node).
        for fp in plan.files:
            by_el = {id(u.element): u for u in fp.units}
            for u in fp.units:
                for anc in u.element.parents:
                    outer = by_el.get(id(anc))
                    if outer is not None and len(u.text) > 8:
                        assert u.text not in outer.text

    def test_display_none_content_skipped_not_leaked(self, plan):
        # li.hidden_content (Kindle-removed TOC entries) is display:none —
        # its text must be skipped as hidden, not merged into the parent ol.
        toc = next(f for f in plan.files if "part0002" in f.file_name)
        assert toc.skipped.get("hidden", 0) > 0
        for u in toc.units:
            assert "CoverTitle" not in u.text

    def test_poetry_lines_are_units_without_line_numbers(self, plan):
        units = [u for f in plan.files for u in f.units]
        poetry = [u for u in units if u.signature.startswith("div.poetry_line")]
        assert len(poetry) > 4500
        sample = next(u for u in poetry if "He who saw the Deep" in u.text)
        assert not sample.text.startswith("I 5")

    def test_poetry_grouping(self, plan):
        units = [u for f in plan.files for u in f.units]
        poetry = [u for u in units if u.signature == "div.poetry_line"]
        grouped = [u for u in poetry if u.group_id is not None]
        assert len(grouped) / len(poetry) > 0.9

    def test_editorial_brackets_do_not_merge_words(self, plan):
        # "he <em>walks</em> [<em>back and forth</em>,]": the skipped " ["
        # node must still separate the words it stood between
        units = [u for f in plan.files for u in f.units]
        assert any("he walks back and forth" in u.text for u in units)
        assert not any("walksback" in u.text for u in units)

    def test_report_samples_survive_rich_markup(self, plan):
        # book text is full of "[Seven] warriors [they were]" — rich reads
        # those as style tags and eats them unless the report is escaped
        console = Console(file=StringIO(), width=200)
        console.print(escape(plan.report()))
        assert "[Seven] warriors [they were]" in console.file.getvalue()


# ------------------------------------------------------- plan artifact I/O


class TestPlanArtifact:
    def test_report_and_json_roundtrip(self, tmp_path):
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book)
        text = plan.report()
        assert "coverage" in text.lower()
        assert "blockquote.calibre_17" in text

        out = tmp_path / "plan.json"
        plan.save_json(out, book_path=str(ANIMAL_FARM))
        import json

        data = json.loads(out.read_text())
        assert data["coverage"] == pytest.approx(plan.coverage)
        assert data["book_sha256"]
        sigs = {s["signature"]: s for s in data["signatures"]}
        assert sigs["blockquote.calibre_17"]["action"] == "translate"

    def test_signature_override_skip(self, tmp_path):
        book = epub.read_epub(str(ANIMAL_FARM))
        overrides = {"blockquote.calibre_17": "skip"}
        plan = build_plan(book, overrides=overrides)
        units = [u for f in plan.files for u in f.units]
        assert not any(u.signature == "blockquote.calibre_17" for u in units)
        # overridden chars are accounted as skipped, invariant intact
        for fp in plan.files:
            assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
                fp.skipped.values()
            )


# ------------------------------------------------- loader integration (e2e)


class FakeModel:
    """Minimal translator satisfying EPUBBookLoader's expectations."""

    TRANSLATION_ERROR_MARKER = None
    _fatal_error_detected = False

    def __init__(self, key, language, **kwargs):
        self.list_calls = []

    def translate(self, text, needprint=True):
        return f"T[{text}]"

    def translate_list(self, text_list):
        self.list_calls.append(list(text_list))
        return [f"T[{t}]" for t in text_list]


class MisalignedOnceModel(FakeModel):
    """Returns a wrong-length list on the first multi-item call."""

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        self.failed_once = False

    def translate_list(self, text_list):
        self.list_calls.append(list(text_list))
        if len(text_list) > 1 and not self.failed_once:
            self.failed_once = True
            return ["only one item"]
        return [f"T[{t}]" for t in text_list]


def _make_loader(tmp_path, model_cls, book=ANIMAL_FARM):
    from book_maker.loader.epub_loader import EPUBBookLoader

    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / book.name
    shutil.copy(book, src)
    loader = EPUBBookLoader(
        str(src),
        model_cls,
        "dummy-key",
        resume=False,
        language="zh-hans",
    )
    loader.translate_tags = "auto"
    return loader, src


class TestLoaderPlanMode:
    def test_poem_translated_in_grouped_batches(self, tmp_path):
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()

        out = src.parent / (src.stem + "_bilingual.epub")
        assert out.exists()
        with zipfile.ZipFile(out) as z:
            doc = next(n for n in z.namelist() if "004" in n and n.endswith(".html"))
            soup = bs(z.read(doc), "html.parser")
        quotes = soup.find_all("blockquote")
        texts = [q.get_text() for q in quotes]
        assert any(
            t.startswith("T[Beasts of England, beasts of Ireland,") for t in texts
        )
        # every original poem line has a translated sibling
        originals = [t for t in texts if not t.startswith("T[")]
        translated = [t for t in texts if t.startswith("T[")]
        assert len(originals) == len(translated) > 20

        # poem went through translate_list in multi-line context windows
        model = loader.translate_model
        multi = [c for c in model.list_calls if len(c) > 1]
        assert multi, "poetry must be batched, not sent line by line"
        assert all(2 <= len(c) <= 8 for c in multi)

    def test_alignment_retry_ladder(self, tmp_path):
        loader, src = _make_loader(tmp_path, MisalignedOnceModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()

        out = src.parent / (src.stem + "_bilingual.epub")
        with zipfile.ZipFile(out) as z:
            doc = next(n for n in z.namelist() if "004" in n and n.endswith(".html"))
            soup = bs(z.read(doc), "html.parser")
        texts = [q.get_text() for q in soup.find_all("blockquote")]
        originals = [t for t in texts if not t.startswith("T[")]
        translated = [t for t in texts if t.startswith("T[")]
        # a garbage-length response must not desync line alignment
        assert len(originals) == len(translated)
        assert "only one item" not in " ".join(texts)

    def test_coverage_gate_fails_loud(self, tmp_path):
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.plan_min_coverage = 1.01  # impossible on purpose
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

    def test_single_translate_preserves_unowned_content(self, tmp_path):
        # Finding #4: single-translate must replace only the unit's own text
        # nodes — nested blocks and line-number spans must survive.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.single_translate = True
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()

        out = src.parent / (src.stem + "_bilingual.epub")
        with zipfile.ZipFile(out) as z:
            doc = next(n for n in z.namelist() if "004" in n and n.endswith(".html"))
            soup = bs(z.read(doc), "html.parser")
        quotes = [q.get_text() for q in soup.find_all("blockquote")]
        # translated in place, originals replaced, one per line — none deleted
        assert len(quotes) == 29
        assert all(t.startswith("T[") for t in quotes)

    def test_plan_file_not_overwritten_and_overrides_survive(self, tmp_path):
        # Finding #2: a user-edited plan JSON must survive the real run.
        import json

        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()

        plan_path = src.parent / (src.stem + "_plan.json")
        data = json.loads(plan_path.read_text())
        for sig in data["signatures"]:
            if sig["signature"] == "blockquote.calibre_17":
                sig["action"] = "skip"
        plan_path.write_text(json.dumps(data))
        edited = plan_path.read_text()

        loader2, _ = _make_loader(tmp_path, FakeModel)
        loader2.only_filelist = "index_split_004.html"
        loader2.make_bilingual_book()

        assert plan_path.read_text() == edited, "edited plan was overwritten"
        # and the override was actually applied: no calibre_17 text translated
        sent = [t for call in loader2.translate_model.list_calls for t in call]
        assert not any("Beasts of every land and clime" in t for t in sent)

    def test_parallel_resume_cache_is_document_ordered(self, tmp_path):
        # Finding #1: cache slots must follow document order, not thread
        # completion order.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.set_parallel_workers(4)
        loader.make_bilingual_book()

        assert None not in loader.p_to_save
        # sequential reference run must produce the same cache order
        ref, _ = _make_loader(tmp_path / "ref", FakeModel)
        ref.make_bilingual_book()
        assert loader.p_to_save == ref.p_to_save

    def test_parallel_respects_only_filelist(self, tmp_path):
        # Finding #3: parallel mode must not translate filtered-out chapters.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.set_parallel_workers(4)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()
        sent = [t for call in loader.translate_model.list_calls for t in call]
        assert any("Beasts of England, beasts of Ireland," in t for t in sent)
        # text from another chapter must not be translated
        assert not any("Mr. Whymper" in t for t in sent)

    def test_test_num_caps_api_usage_even_with_parallel(self, tmp_path):
        # Finding #3: --test --test_num must cap units in parallel mode too.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.set_parallel_workers(4)
        loader.is_test = True
        loader.test_num = 1
        loader.make_bilingual_book()
        sent = [t for call in loader.translate_model.list_calls for t in call]
        assert len(sent) <= 2

    def test_parallel_chapter_failure_fails_loud(self, tmp_path):
        # Finding #5: a failed chapter must not produce a "completed" book.
        class OneChapterExplodes(FakeModel):
            def translate_list(self, text_list):
                if any("Beasts of England" in t for t in text_list):
                    raise RuntimeError("boom")
                return super().translate_list(text_list)

        loader, src = _make_loader(tmp_path, OneChapterExplodes)
        loader.set_parallel_workers(4)
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

    def test_real_run_plan_honors_file_filters(self, tmp_path):
        # The coverage gate and saved plan JSON must describe the files that
        # will actually be translated, not the whole book (review finding 1).
        import json

        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()

        data = json.loads((src.parent / (src.stem + "_plan.json")).read_text())
        ref = build_plan(epub.read_epub(str(src)), only_files={"index_split_004.html"})
        whole = build_plan(epub.read_epub(str(src)))
        assert data["total_chars"] == ref.total_chars < whole.total_chars
        assert {s["signature"] for s in data["signatures"]} == {
            r["signature"] for r in ref.signature_rows()
        }

    def test_resume_refuses_plan_edited_after_crash(self, tmp_path):
        # Resume slots are positional over the unit list; a plan edited
        # between crash and resume must be refused, not misapplied
        # (review finding 3).
        import json

        from book_maker.loader.epub_loader import EPUBBookLoader

        class ExplodesOnBeasts(FakeModel):
            def translate_list(self, text_list):
                if any("Beasts of England" in t for t in text_list):
                    raise RuntimeError("boom")
                return super().translate_list(text_list)

        def make_resume_loader(src):
            loader = EPUBBookLoader(
                str(src),
                FakeModel,
                "dummy-key",
                resume=True,
                language="zh-hans",
            )
            loader.translate_tags = "auto"
            return loader

        loader, src = _make_loader(tmp_path, ExplodesOnBeasts)
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()
        assert (src.parent / f".{src.stem}.temp.bin").exists()

        plan_path = src.parent / (src.stem + "_plan.json")
        original = plan_path.read_text()
        data = json.loads(original)
        for sig in data["signatures"]:
            if sig["signature"] == "blockquote.calibre_17":
                sig["action"] = "skip"
        plan_path.write_text(json.dumps(data))

        with pytest.raises(SystemExit) as excinfo:
            make_resume_loader(src).make_bilingual_book()
        assert excinfo.value.code == 1

        # restoring the plan lets the resume proceed to completion
        plan_path.write_text(original)
        make_resume_loader(src).make_bilingual_book()
        assert (src.parent / (src.stem + "_bilingual.epub")).exists()

    def test_each_file_partitioned_exactly_once(self, tmp_path):
        # The plan build, progress counting, and processing must share one
        # partition per file, not redo the work (review finding 4).
        from collections import Counter

        for workers in (1, 4):
            loader, src = _make_loader(tmp_path / f"w{workers}", FakeModel)
            loader.set_parallel_workers(workers)
            calls = Counter()
            orig = loader._partition_item

            def counting(soup, file_name, _orig=orig, _calls=calls):
                _calls[file_name] += 1
                return _orig(soup, file_name)

            loader._partition_item = counting
            loader.make_bilingual_book()
            assert calls, "partitioning must have happened"
            assert all(n == 1 for n in calls.values()), (workers, calls)


# ------------------------------------------- epub-format hardening (260731)


class TestEpubHardening:
    """Approved plan items 0/2-10: ruby, hiding, @media, br/glue, svg/math,
    doc-pagebreak, dfn, invisible chars, schema version, fixed layout."""

    def _partition(self, html, css=""):
        soup = bs(html, "html.parser")
        resolver = DisplayResolver([parse_css_display(css)] if css else [])
        return partition_soup(soup, resolver, file_name="x.html"), soup

    # -- item 2: ruby ------------------------------------------------------

    def test_ruby_readings_excluded_from_unit_text(self):
        fp, _ = self._partition(
            "<body><p><ruby>漢字<rt>かんじ</rt></ruby>です。</p></body>"
        )
        assert [u.text for u in fp.units] == ["漢字です。"]
        assert fp.skipped["ruby"] == len("かんじ")
        # invariant: every character accounted for
        assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
            fp.skipped.values()
        )

    def test_ruby_with_rp_fallback_parens(self):
        fp, _ = self._partition(
            "<body><p><ruby>東京<rp>(</rp><rt>とうきょう</rt><rp>)</rp></ruby>"
            "に行く。</p></body>"
        )
        assert [u.text for u in fp.units] == ["東京に行く。"]

    def test_single_translate_extracts_orphaned_furigana(self, tmp_path):
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs(
            "<body><p><ruby>漢字<rt>かんじ</rt></ruby>です。</p></body>",
            "html.parser",
        )
        resolver = DisplayResolver([])
        fp = partition_soup(soup, resolver, "x.html")
        loader._insert_plan_translation(
            fp.units[0], "It is kanji.", single_translate=True
        )
        assert soup.find("rt") is None
        assert "かんじ" not in soup.get_text()
        assert "It is kanji." in soup.get_text()

    # -- item 3: inline hiding + footnote exemption ------------------------

    def test_style_attribute_display_none_is_hidden(self):
        fp, _ = self._partition(
            "<body><p>visible prose here</p>"
            '<div style="display:none">secret machinery text</div></body>'
        )
        assert [u.text for u in fp.units] == ["visible prose here"]
        assert fp.skipped["hidden"] > 0

    def test_hidden_attribute_is_hidden(self):
        fp, _ = self._partition(
            "<body><p>visible prose here</p><div hidden>gone content</div></body>"
        )
        assert [u.text for u in fp.units] == ["visible prose here"]

    def test_aria_hidden_is_not_a_hiding_signal(self):
        fp, _ = self._partition(
            '<body><h1 aria-hidden="true">A Visible Decorated Title</h1></body>'
        )
        assert [u.text for u in fp.units] == ["A Visible Decorated Title"]

    def test_css_hidden_footnote_aside_is_still_translated(self):
        # popup-capable readers show footnote asides regardless of CSS
        fp, _ = self._partition(
            "<body><p>Prose with a marker.</p>"
            '<aside class="fn" epub:type="footnote">The footnote body text.'
            "</aside></body>",
            css=".fn { display: none }",
        )
        texts = [u.text for u in fp.units]
        assert "The footnote body text." in texts

    def test_style_hidden_doc_footnote_role_is_still_translated(self):
        fp, _ = self._partition(
            '<body><aside role="doc-footnote" style="display:none">'
            "Endnote prose survives.</aside></body>"
        )
        assert [u.text for u in fp.units] == ["Endnote prose survives."]

    # -- item 4: @media ----------------------------------------------------

    def test_media_print_rules_do_not_hide_screen_text(self):
        m = parse_css_display("@media print { .noscreen { display: none } }")
        assert m == {}

    def test_media_screen_rules_are_unwrapped(self):
        m = parse_css_display("@media screen { span.verse { display: block } }")
        assert m[("span", "verse")] == "block"

    def test_font_face_and_page_blocks_dropped(self):
        m = parse_css_display(
            "@font-face { font-family: x; src: url(y) } "
            "@page { margin: 1em } p { display: block }"
        )
        assert m == {("p", None): "block"}

    def test_supports_unwrapped_and_nested_media(self):
        m = parse_css_display(
            "@supports (display: flex) { @media print { .a { display:none } } "
            ".b { display: inline } }"
        )
        assert (None, "a") not in m
        assert m[(None, "b")] == "inline"

    # -- item 5: br / whitespace glue --------------------------------------

    def test_br_separates_words(self):
        fp, _ = self._partition("<body><p>line one<br/>line two</p></body>")
        assert [u.text for u in fp.units] == ["line one line two"]

    def test_whitespace_only_nodes_glue_inline_siblings(self):
        fp, _ = self._partition("<body><p><b>one</b> <b>two</b></p></body>")
        assert [u.text for u in fp.units] == ["one two"]

    def test_wbr_and_drop_caps_still_join_without_space(self):
        # drop-cap split spelled S|tone so the fragment is itself a word —
        # the repo's typos CI reads bare split fragments as misspellings
        fp, _ = self._partition(
            "<body><p>super<wbr/>cali</p>"
            "<p><span>S</span>tone walls do not a prison make</p></body>"
        )
        assert [u.text for u in fp.units] == [
            "supercali",
            "Stone walls do not a prison make",
        ]

    def test_unit_clean_text_glues_too(self):
        soup = bs("<body><p>one<br/>two <b>three</b></p></body>", "html.parser")
        resolver = DisplayResolver([])
        assert unit_clean_text(soup.p, resolver) == "one two three"

    def test_skipped_symbol_node_leaves_its_whitespace_behind(self):
        # gilgamesh: "he <em>walks</em> [<em>back and forth</em>,]" — the " ["
        # node is dropped as a symbol, but the space it carried must survive
        fp, _ = self._partition(
            "<body><p>he <em>walks</em> [<em>back and forth</em>,]</p></body>"
        )
        assert [u.text for u in fp.units] == ["he walks back and forth"]
        assert fp.skipped["symbol"] > 0

    def test_skipped_node_glue_does_not_double_space(self):
        fp, _ = self._partition("<body><p><b>one</b> [ <b>two</b></p></body>")
        assert [u.text for u in fp.units] == ["one two"]

    def test_unit_clean_text_glues_skipped_nodes_too(self):
        soup = bs(
            "<body><p>he <em>walks</em> [<em>back and forth</em>,]</p></body>",
            "html.parser",
        )
        assert unit_clean_text(soup.p, DisplayResolver([])) == "he walks back and forth"

    # -- item 6: svg / math ------------------------------------------------

    def test_svg_and_math_are_non_content(self):
        fp, _ = self._partition(
            "<body><p>Formula <math><mi>x</mi><mtext>otherwise</mtext></math> "
            "appears here.</p>"
            "<svg><title>Diagram title</title><text>axis label</text></svg></body>"
        )
        assert [u.text for u in fp.units] == ["Formula appears here."]
        assert fp.skipped["non-content"] > 0

    # -- item 7: role="doc-pagebreak" --------------------------------------

    def test_role_doc_pagebreak_skipped(self):
        fp, _ = self._partition(
            "<body><p>Prose before the break.</p>"
            '<span role="doc-pagebreak" title="47">page 47</span></body>'
        )
        assert [u.text for u in fp.units] == ["Prose before the break."]
        assert fp.skipped["pagebreak"] == len("page 47")

    # -- item 8: dfn is inline ---------------------------------------------

    def test_dfn_does_not_split_its_paragraph(self):
        fp, _ = self._partition(
            "<body><p>The term <dfn>widget</dfn> means a small thing.</p></body>"
        )
        assert [u.text for u in fp.units] == ["The term widget means a small thing."]

    # -- item 9: invisible characters --------------------------------------

    def test_soft_hyphen_and_zero_width_stripped_from_unit_text(self):
        fp, _ = self._partition(
            "<body><p>con­struction and zero​width﻿ here</p></body>"
        )
        assert [u.text for u in fp.units] == ["construction and zerowidth here"]

    # -- item 0: schema version --------------------------------------------

    def test_plan_json_carries_schema_version(self, tmp_path):
        from book_maker.loader.plan import PLAN_SCHEMA_VERSION
        import json

        plan = build_plan(epub.read_epub(str(ANIMAL_FARM)))
        path = tmp_path / "p.json"
        plan.save_json(str(path))
        assert json.loads(path.read_text())["schema_version"] == PLAN_SCHEMA_VERSION

    def test_resume_refused_across_schema_versions(self, tmp_path):
        import book_maker.loader.plan as plan_mod
        import book_maker.loader.epub_loader as loader_mod

        class ExplodesOnBeasts(FakeModel):
            def translate_list(self, text_list):
                if any("Beasts of England" in t for t in text_list):
                    raise RuntimeError("boom")
                return super().translate_list(text_list)

        loader, src = _make_loader(tmp_path, ExplodesOnBeasts)
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

        old = loader_mod.PLAN_SCHEMA_VERSION
        loader_mod.PLAN_SCHEMA_VERSION = old + 1
        try:
            resumed = loader_mod.EPUBBookLoader(
                str(src), FakeModel, "dummy-key", resume=True, language="zh-hans"
            )
            resumed.translate_tags = "auto"
            with pytest.raises(SystemExit) as excinfo:
                resumed.make_bilingual_book()
            assert excinfo.value.code == 1
        finally:
            loader_mod.PLAN_SCHEMA_VERSION = old

    # -- item 10: fixed layout ---------------------------------------------

    def test_is_fixed_layout(self):
        from book_maker.loader.plan import is_fixed_layout

        book = epub.EpubBook()
        assert not is_fixed_layout(book)
        book.add_metadata(
            None, "meta", "pre-paginated", {"property": "rendition:layout"}
        )
        assert is_fixed_layout(book)

    def test_reflowable_book_is_not_fixed_layout(self):
        from book_maker.loader.plan import is_fixed_layout

        assert not is_fixed_layout(epub.read_epub(str(ANIMAL_FARM)))

    # -- lead review: filter semantics parity with process_item ------------

    def test_only_filelist_wins_over_exclude(self):
        # process_item semantics: an only-list wins outright; exclude applies
        # only when no only-list is given. The plan must judge the same set.
        target = "index_split_004.html"
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book, only_files={target}, exclude_files={target})
        assert [f.file_name for f in plan.files] == [target]
