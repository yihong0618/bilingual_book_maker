"""Tests for the coverage-complete translation plan (partition, don't select).

Fixtures:
- test_books/animal_farm.epub  (committed) — poem lines are per-line
  <blockquote class="calibre_14|calibre_17">; default tag selection missed them.
- books/gilgamesh.epub (local only, 3.8MB) — 51% of text lives in
  div.poetry_line*; tests skip if the file is absent.
"""

import copy as copy_mod
import json
import os
import re
import shutil
import zipfile
from collections import Counter
from io import StringIO
from unittest import mock
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
    parse_css_display,
    partition_file,
    partition_soup,
    unit_clean_text,
)

REPO = Path(__file__).resolve().parent.parent
ANIMAL_FARM = REPO / "test_books" / "animal_farm.epub"
LIBER_ESTHER = REPO / "test_books" / "Liber_Esther.epub"
GILGAMESH = REPO / "books" / "gilgamesh.epub"

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

    def test_numbers_are_content_now(self):
        # schema 3: the numeric heuristic is gone. Verse numbers (rigveda) and
        # figures reach the model; --plan-classify decides, not a regex.
        for text in ["5", "120", "3 4 5", "3.14", "1,234.56", "42%", "$3.99"]:
            assert classify_skip(text) is None, text

    def test_roman_line_refs_are_content_now(self):
        # gilgamesh span.mr / span.mn contents: skipping these also cost real
        # drop caps and the pronoun "I", which the vote only half-rescued
        for text in ["I 5", "XI 100", "IV", "I"]:
            assert classify_skip(text) is None, text

    def test_symbols(self):
        assert classify_skip("↓") == "symbol"  # ↓ span.arrow
        assert classify_skip("+") == "symbol"
        assert classify_skip("* * *") == "symbol"

    def test_links(self):
        assert classify_skip("http://example.com/x") == "link"


# ------------------------------------------------------------- css resolver


class TestCssDisplay:
    def test_parse_simple_rules(self):
        css = """
        /* comment { display:none } */
        .poem { display: block; }
        span.linenum { display : inline-block }
        div.run-in, p.also { display: inline; }
        """
        m, _conditional = parse_css_display(css)
        assert m[(None, "poem")] == "block"
        assert m[("span", "linenum")] == "inline-block"
        assert m[("div", "run-in")] == "inline"
        assert m[("p", "also")] == "inline"

    def test_resolver_css_overrides_defaults(self):
        css = "span.verse { display: block; } div.note { display: inline; }"
        resolver = _make_resolver(css)
        soup = bs(
            '<div><span class="verse">a</span><div class="note">b</div>'
            "<span>c</span><p>d</p></div>",
            "html.parser",
        )
        assert resolver.is_block(soup.find("span", class_="verse"))
        assert not resolver.is_block(soup.find("div", class_="note"))
        assert not resolver.is_block(soup.find_all("span")[1])
        assert resolver.is_block(soup.find("p"))

    def test_equal_specificity_resolves_by_source_order_not_attribute_order(self):
        # review finding: the resolver returned the first class token that
        # matched, so class="visible hidden" and class="hidden visible"
        # resolved differently from identical CSS. CSS decides equal
        # specificity by declaration order — .hidden is declared last here,
        # so both elements are hidden.
        css = ".visible { display: block } .hidden { display: none }"
        resolver = _make_resolver(css)
        soup = bs(
            '<div><p class="visible hidden">a</p>'
            '<p class="hidden visible">b</p></div>',
            "html.parser",
        )
        displays = [resolver.display_of(p) for p in soup.find_all("p")]
        assert displays == ["none", "none"]

    def test_later_stylesheet_wins_over_earlier(self):
        resolver = _make_resolver(".x { display: none }", ".x { display: block }")
        soup = bs('<p class="x">a</p>', "html.parser")
        assert resolver.display_of(soup.find("p")) == "block"

    def test_tag_class_outranks_bare_class_regardless_of_order(self):
        # specificity still dominates: p.note (0,1,1) beats .plain (0,1,0)
        # even though .plain is declared later
        css = "p.note { display: none } .plain { display: block }"
        resolver = _make_resolver(css)
        soup = bs('<p class="note plain">a</p>', "html.parser")
        assert resolver.display_of(soup.find("p")) == "none"


def _make_resolver(*css_texts):
    """A DisplayResolver from stylesheet sources, unconditional + conditional."""
    maps = [parse_css_display(c) for c in css_texts if c]
    return DisplayResolver([m[0] for m in maps], [m[1] for m in maps])


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


class TestMediaQueries:
    """`@media` decides whether a `display:none` is evidence, a verdict, or
    nothing at all — and `not` inverts every one of those answers."""

    @staticmethod
    def _verdict(prelude):
        from book_maker.loader.plan import _media_verdict

        return _media_verdict(prelude)

    def test_a_negated_print_query_applies_on_screen(self):
        # `not print` is true on every medium except print — including the
        # screen an ebook reader renders on. Matching "print" as a substring
        # read it as a print rule and dropped it.
        assert self._verdict("@media not print") == ("unconditional", None)
        assert self._verdict("@media print")[0] == "drop"

    def test_a_negated_screen_query_never_applies_here(self):
        assert self._verdict("@media not screen")[0] == "drop"
        assert self._verdict("@media not all")[0] == "drop"

    def test_a_negated_feature_query_stays_conditional(self):
        # true on a wide screen, false on a narrow one: still device-specific
        verdict, condition = self._verdict("@media not screen and (max-width: 600px)")
        assert verdict == "conditional"
        assert "not screen" in condition

    def test_a_not_print_rule_hides_text_from_the_partition(self):
        # the consequence, end to end: text a reader cannot see must not
        # reach the classifier as ordinary prose
        resolver = _make_resolver("@media not print { .apparatus { display: none } }")
        soup = bs(
            '<body><p>keep this</p><p class="apparatus">gone</p></body>',
            "html.parser",
        )
        fp = partition_soup(soup, resolver, "x.html")
        assert [u.text for u in fp.units] == ["keep this"]
        assert fp.skipped.get("hidden") == len("gone")


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
            # the <sup> footnote marker splits its paragraph into two runs
            "p",
            "p",
        ]

    def test_line_numbers_ride_along_with_their_line(self):
        # schema 3 is greedy: the line-number spans are part of the block, so
        # they reach the model with it instead of being guessed away. Nothing
        # is charged to a content skip reason any more.
        fp, _ = self._partition(MINI_GILGAMESH)
        line = next(u for u in fp.units if u.signature == "div.poetry_line")
        assert line.text.endswith("He who saw the Deep, the country's foundation,")
        assert "I 5" in line.text
        assert fp.skipped.get("roman-ref", 0) == 0
        assert fp.skipped.get("numeric", 0) == 0

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
        # The excluded <sup> stays in the document and renders between the
        # words around it, so it is a run barrier: the paragraph becomes two
        # segments and the marker keeps its place between their translations.
        fp, _ = self._partition(MINI_GILGAMESH)
        paragraphs = [u for u in fp.units if u.signature == "p"]
        joined = " ".join(u.text for u in paragraphs)
        assert "1" not in joined
        assert "Prose paragraph" in joined and "footnote marker" in joined

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

    def test_short_units_are_units_now(self):
        # schema 3: manuscript sigla and other sub-3-letter cells are units.
        # They were the trivial filter's catch, and it also ate real content
        # ("No", "Sí"); the classifier judges these instead.
        html = "<body><table><tr><td>(a)</td><td>Gilgamesh</td></tr></table></body>"
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == ["(a)", "Gilgamesh"]
        assert "trivial" not in fp.skipped
        assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
            fp.skipped.values()
        )

    def test_verse_numbers_are_units_rigveda_shape(self):
        # rigveda_sanskrit.epub: 6.1% of the book is bare verse references in
        # their own blocks. The numeric filter ate every one of them; greedy
        # keeps them as units so the classifier (or the user) can rule.
        html = (
            "<body><p class='vn'>1.1.1</p>"
            "<p class='mantra'>agním īḷe puróhitaṃ</p>"
            "<p class='vn'>1.1.2</p></body>"
        )
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == [
            "1.1.1",
            "agním īḷe puróhitaṃ",
            "1.1.2",
        ]
        assert sum(fp.skipped.values()) == 0

    def test_structural_skips_are_untouched_by_greedy(self):
        # the free/structural half of the filter must survive schema 3 intact
        html = (
            "<body><p>real prose paragraph here</p>"
            '<p style="display:none">hidden apparatus</p>'
            "<p>* * *</p>"
            "<p>https://example.com/x</p>"
            "<ruby>漢<rt>かん</rt></ruby>"
            "<svg><text>diagram label</text></svg></body>"
        )
        fp, _ = self._partition(html)
        assert [u.text for u in fp.units] == ["real prose paragraph here", "漢"]
        for reason in ("hidden", "symbol", "link", "ruby", "non-content"):
            assert fp.skipped.get(reason, 0) > 0, reason

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

    def test_drop_caps_join_their_word_without_a_vote(self):
        # The old per-signature "does this class carry prose elsewhere?" vote
        # existed only to rescue drop caps from the roman-numeral filter. With
        # the filter gone, a drop cap is simply the first letter of its unit —
        # no vote, no file-order dependence, no way to lose the "C" of "Cover".
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
        assert line.text == "IHe who saw the Deep"
        assert fp.skipped.get("roman-ref", 0) == 0

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
        fp, soup = self._partition(MINI_GILGAMESH)
        el = soup.find("div", class_="poetry_line")
        resolver = DisplayResolver([])
        unit = next(u for u in fp.units if u.signature == "div.poetry_line")
        # recomputing from the element alone must reproduce the planned text
        assert unit_clean_text(el, resolver) == unit.text


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


class TestNoShortUnitSweep:
    """Grouping means poetry only. A second tier that windowed leftover
    short units was measured at 5-33 saved requests per book (0.5-4%) and
    removed — not worth its nondeterministic window membership, and it
    caused the tier-2/poetry classification conflation bug."""

    LONG = (
        "a fully formed prose sentence that runs well past the short-unit "
        "cut-off and therefore never joins a window"
    )

    def _units(self, html, group_size=8):
        soup = bs(html, "html.parser")
        fp, _ = partition_file(
            soup, DisplayResolver([]), "x.html", poetry_group_size=group_size
        )
        return fp.units

    def test_mixed_short_run_stays_solo(self):
        # three different tags: not a poetry run, and no sweep exists to
        # batch them — one request each, classifier judges each signature
        units = self._units(
            "<body><p class='pn'>42</p><div class='vn'>1.1.1</div>"
            f"<h3 class='lbl'>Ch.</h3><p>{self.LONG}</p></body>"
        )
        assert [u.group_id for u in units] == [None, None, None, None]

    def test_poetry_still_groups(self):
        stanza = "".join(f"<div class='line'>verse line {i}</div>" for i in range(4))
        units = self._units(f"<body><div class='st'>{stanza}</div></body>")
        assert len({u.group_id for u in units}) == 1
        assert all(u.group_id is not None for u in units)

    def test_units_carry_no_semantic_poetry_flag(self):
        # zombie guard: a window is a batching shape, never a claim about
        # genre — the flag that conflated them suppressed classification of
        # half the corpus
        stanza = "".join(f"<div class='line'>verse line {i}</div>" for i in range(4))
        units = self._units(f"<body><div class='st'>{stanza}</div></body>")
        assert not any(hasattr(u, "poetry") for u in units)


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
        # signatures carry every class, sorted: div.TS1.poetry_line and
        # div.poetry_line are different shapes and different questions
        poetry = [u for u in units if "poetry_line" in u.signature]
        assert len(poetry) > 4000
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
        assert any("he walks [back and forth,]" in u.text for u in units)
        assert not any("walksback" in u.text for u in units)

    def test_report_samples_survive_rich_markup(self, plan):
        # book text is full of "[Seven] warriors [they were]" — rich reads
        # those as style tags and eats them unless the report is escaped.
        # Strip color codes before asserting: under FORCE_COLOR rich styles
        # the output, splitting the text without eating it.
        console = Console(file=StringIO(), width=200)
        console.print(escape(plan.report()))
        rendered = re.sub(r"\x1b\[[0-9;]*m", "", console.file.getvalue())
        assert "[Gilgamesh, who] saw the Deep" in rendered


# ------------------------------------------------------- plan artifact I/O


class TestPlanArtifact:
    def test_report_and_json_roundtrip(self, tmp_path):
        book = epub.read_epub(str(ANIMAL_FARM))
        plan = build_plan(book)
        text = plan.report()
        assert "coverage" in text.lower()
        assert "block:blockquote.calibre_17" in text

        out = tmp_path / "plan.json"
        plan.save_json(out, book_path=str(ANIMAL_FARM))
        import json

        data = json.loads(out.read_text())
        assert data["coverage"] == pytest.approx(plan.coverage)
        assert data["book_sha256"]
        rows = {s["key"]: s for s in data["signatures"]}
        # every row is a question until someone answers it
        assert rows["block:blockquote.calibre_17"]["action"] is None
        assert rows["block:blockquote.calibre_17"]["samples"]

    def test_signature_override_skip(self, tmp_path):
        book = epub.read_epub(str(ANIMAL_FARM))
        overrides = {"block:blockquote.calibre_17": ("skip", "user")}
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


class ClassifyingModel(FakeModel):
    """A translator that can also answer the plan's questions."""

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        self.classified = []

    def supports_structured_json(self):
        return True

    def structured_json(self, prompt, schema, model=None, accept=None):
        keys = list(schema["schema"]["required"])
        self.classified.append(keys)
        return {k: {"verdict": "translate", "content_type": "prose"} for k in keys}


class SkipOneModel(ClassifyingModel):
    """...and skips exactly one signature, which forces a re-partition."""

    def structured_json(self, prompt, schema, model=None, accept=None):
        answers = super().structured_json(prompt, schema, model, accept)
        answers[self.classified[-1][-1]] = {
            "verdict": "skip",
            "content_type": "apparatus",
        }
        return answers


def _write_decided_plan(loader, action="translate"):
    """Run agent mode to emit the plan, then answer every open question.

    The agent flow is the one that produces a plan file: it stops before
    translating so a person or an agent can rule on the rows. Tests that
    need a decided plan take the same two steps a user would.
    """
    loader.plan_classify = "agent"
    with pytest.raises(SystemExit) as exit_info:
        loader.make_bilingual_book()
    assert exit_info.value.code == 0
    plan_path = Path(loader.epub_name).with_name(
        Path(loader.epub_name).stem + "_plan.json"
    )
    data = json.loads(plan_path.read_text())
    for row in data["signatures"]:
        row["action"] = action
        row["decided_by"] = "agent"
        row["content_type"] = "prose"
    plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return plan_path


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
    loader.plan_mode = True
    loader.translate_tags = "auto"
    # tests that exercise the plan JSON set this to "agent"; the default is
    # the deliberate translate-everything mode, which writes no plan file
    loader.plan_classify = "most"
    return loader, src


class TestQuietMode:
    """--quiet is for log files and agent runs: progress bars and
    per-paragraph echoes off, reports and errors still on."""

    @staticmethod
    def _plain(s):
        # rich styles the echoes, splitting tokens with ANSI codes
        return re.sub(r"\x1b\[[0-9;]*m", "", s)

    def test_echoes_and_bars_off_reports_still_on(self, tmp_path, capsys):
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.quiet = True
        loader.make_bilingual_book()
        captured = capsys.readouterr()
        # no translated echo (the plan report still shows clipped source
        # samples — that is the deliverable, not the echo), no tqdm bar
        assert "T[" not in self._plain(captured.out)
        assert "it/s" not in captured.err
        assert "Translation plan:" in captured.out
        assert (src.parent / (src.stem + "_bilingual.epub")).exists()

    def test_default_still_echoes(self, tmp_path, capsys):
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()
        assert "T[" in self._plain(capsys.readouterr().out)


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
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        plan_path = _write_decided_plan(loader)

        data = json.loads(plan_path.read_text())
        for row in data["signatures"]:
            if row["key"] == "block:blockquote.calibre_17":
                row["action"] = "skip"
        plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        edited = plan_path.read_text()

        loader2, _ = _make_loader(tmp_path, FakeModel)
        loader2.only_filelist = "index_split_004.html"
        loader2.plan_classify = "agent"
        loader2.make_bilingual_book()

        assert plan_path.read_text() == edited, "edited plan was overwritten"
        # and the override was actually applied: no calibre_17 text translated
        sent = [t for call in loader2.translate_model.list_calls for t in call]
        assert not any("Beasts of every land and clime" in t for t in sent)

    def test_a_plan_of_open_questions_is_asked_again_not_refused(
        self, tmp_path, capsys
    ):
        # --plan-dry-run (and agent mode's own first run) write a plan whose
        # rows are all null *by design*. The next run must put those
        # questions back to whoever can answer them — refusing the file we
        # told the user to generate turns the documented workflow into a
        # traceback.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.plan_classify = "agent"
        with pytest.raises(SystemExit) as first:
            loader.make_bilingual_book()
        assert first.value.code == 0
        plan_path = src.parent / (src.stem + "_plan.json")
        rows = json.loads(plan_path.read_text())["signatures"]
        assert rows and all(r["action"] is None for r in rows)

        capsys.readouterr()
        loader2, _ = _make_loader(tmp_path, FakeModel)
        loader2.only_filelist = "index_split_004.html"
        loader2.plan_classify = "agent"
        with pytest.raises(SystemExit) as second:
            loader2.make_bilingual_book()
        assert second.value.code == 0
        assert "Paste the block below" in capsys.readouterr().out
        assert not loader2.translate_model.list_calls, "nothing may be paid for"

    def test_model_mode_answers_a_plan_that_only_drafted_the_questions(self, tmp_path):
        # The other half of the same seam: an existing plan file is not
        # proof that its questions were answered, so classification is owed
        # to whatever is still null in it.
        loader, src = _make_loader(tmp_path, ClassifyingModel)
        loader.only_filelist = "index_split_004.html"
        loader.plan_classify = "agent"
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()
        plan_path = src.parent / (src.stem + "_plan.json")

        loader2, _ = _make_loader(tmp_path, ClassifyingModel)
        loader2.only_filelist = "index_split_004.html"
        loader2.plan_classify = "model"
        loader2.make_bilingual_book()

        assert loader2.translate_model.classified, "the drafted rows went unasked"
        rows = json.loads(plan_path.read_text())["signatures"]
        assert all(r["action"] == "translate" for r in rows)
        assert all(r["decided_by"] == "llm" for r in rows)

    def test_a_model_run_that_skips_nothing_still_records_dispositions(self, tmp_path):
        # `decide()` writes the verdict, never the outcome, and the only
        # other place that computed one ran while every row was still a
        # question. A run whose model skipped nothing therefore saved a plan
        # claiming a decision on every row and an outcome on none of them.
        loader, src = _make_loader(tmp_path, ClassifyingModel)
        loader.only_filelist = "index_split_004.html"
        loader.plan_classify = "model"
        loader.make_bilingual_book()

        rows = json.loads((src.parent / (src.stem + "_plan.json")).read_text())[
            "signatures"
        ]
        assert rows and all(r["action"] == "translate" for r in rows)
        assert all(r["disposition"] for r in rows), [
            r["key"] for r in rows if not r["disposition"]
        ]

    def test_the_plan_that_ships_is_the_plan_that_was_guarded(
        self, tmp_path, monkeypatch
    ):
        # A skip re-partitions the book, and *that* plan is what gets written
        # back. Guarding only the pre-classification one left this mode able
        # to write a run to a position the guard exists to refuse.
        from book_maker.loader.epub_loader import EPUBBookLoader

        guarded = []
        monkeypatch.setattr(
            EPUBBookLoader,
            "_guard_unsafe_units",
            lambda self, plan: guarded.append(plan),
        )
        loader, _ = _make_loader(tmp_path, SkipOneModel)
        loader.only_filelist = "index_split_004.html"
        loader.plan_classify = "model"
        loader.plan_min_coverage = 0.0
        loader.make_bilingual_book()

        assert len(guarded) == 2, "the re-partitioned plan was never guarded"
        assert guarded[1] is not guarded[0]

    def test_a_signature_a_settings_change_introduces_reaches_the_plan(self, tmp_path):
        # A decided plan plus a widened file filter: the new document's
        # signatures are new questions. Listing them in the prompt while the
        # JSON has no row to edit makes the instruction impossible to follow
        # — every rerun would print the same demand.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        plan_path = _write_decided_plan(loader)
        before = {r["key"] for r in json.loads(plan_path.read_text())["signatures"]}

        loader2, _ = _make_loader(tmp_path, FakeModel)
        loader2.only_filelist = "index_split_004.html,index_split_003.html"
        loader2.plan_classify = "agent"
        with pytest.raises(SystemExit) as exit_info:
            loader2.make_bilingual_book()
        assert exit_info.value.code == 0

        rows = json.loads(plan_path.read_text())["signatures"]
        keys = {r["key"] for r in rows}
        assert keys - before, "the widened filter added no signature to work with"
        # the new rows are askable, and the answered ones were left alone
        assert {r["key"] for r in rows if r["action"] is None} == keys - before
        assert all(
            r["action"] == "translate" and r["decided_by"] == "agent"
            for r in rows
            if r["key"] in before
        )

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

    def test_parallel_plan_context_is_chapter_local(self, tmp_path):
        # The plan branch drove the shared translate_model directly, so with
        # --use_context every chapter appended into one global context_list:
        # wrong reading order plus a data race. Each worker must translate
        # through its own clone.
        class ContextModel(FakeModel):
            def __init__(self, key, language, **kwargs):
                super().__init__(key, language, **kwargs)
                self.context_flag = True
                self.context_list = []
                self.context_translated_list = []
                self.seen_by_instance = []

            def translate_list(self, text_list):
                self.context_list.extend(text_list)
                self.seen_by_instance.extend(text_list)
                return super().translate_list(text_list)

        loader, _ = _make_loader(tmp_path, ContextModel)
        loader.set_parallel_workers(4)
        loader.make_bilingual_book()

        shared = loader.translate_model
        # the shared instance must not have accumulated every chapter's text
        assert (
            shared.context_list == []
        ), "parallel plan mode still writes context into the shared translator"

    def test_sequential_plan_keeps_one_shared_context(self, tmp_path):
        # reading order is correct sequentially, so cloning there would only
        # throw away usable context
        class ContextModel(FakeModel):
            def __init__(self, key, language, **kwargs):
                super().__init__(key, language, **kwargs)
                self.context_flag = True
                self.context_list = []
                self.context_translated_list = []

            def translate_list(self, text_list):
                self.context_list.extend(text_list)
                return super().translate_list(text_list)

        loader, _ = _make_loader(tmp_path, ContextModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()

        assert (
            loader.translate_model.context_list
        ), "sequential runs must keep accumulating shared context"

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
        plan_path = _write_decided_plan(loader)

        data = json.loads(plan_path.read_text())
        ref = build_plan(epub.read_epub(str(src)), only_files={"index_split_004.html"})
        whole = build_plan(epub.read_epub(str(src)))
        assert data["total_chars"] == ref.total_chars < whole.total_chars
        assert {row["key"] for row in data["signatures"]} == set(
            ref.build_ledger().rows
        )

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
            loader.plan_mode = True
            loader.translate_tags = "auto"
            loader.plan_classify = "agent"
            return loader

        setup, src = _make_loader(tmp_path, FakeModel)
        plan_path = _write_decided_plan(setup)
        original = plan_path.read_text()

        loader, _ = _make_loader(tmp_path, ExplodesOnBeasts)
        loader.plan_classify = "agent"
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()
        assert (src.parent / f".{src.stem}.temp.bin").exists()

        data = json.loads(original)
        for row in data["signatures"]:
            if row["key"] == "block:blockquote.calibre_17":
                row["action"] = "skip"
        plan_path.write_text(json.dumps(data, ensure_ascii=False, indent=1))

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
        resolver = _make_resolver(css)
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

    def test_single_translate_leaves_no_empty_ruby_husk(self, tmp_path):
        # RSC-005 "element ruby incomplete": the annotation used to be
        # extracted *after* the husk cleanup had already inspected the
        # wrapper, so a <ruby> that lost its base text kept its <rt> just
        # long enough to be spared, then lost that too — 1,892 empty
        # <ruby></ruby> on kusamakura, every one a file epubcheck rejects.
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs(
            "<body><p><span><ruby>山路<rt>やまみち</rt></ruby>"
            "<ruby>登<rt>のぼ</rt></ruby></span>りながら。</p></body>",
            "html.parser",
        )
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(
            fp.units[0], "Climbing the mountain path.", single_translate=True
        )
        assert soup.find("ruby") is None
        assert soup.find("rt") is None
        assert "Climbing the mountain path." in soup.get_text()

    def test_single_translate_cleans_husks_on_the_covered_path_too(self, tmp_path):
        # the same husk, other branch: when the wrapper covers the whole run
        # the translation replaces nodes[0] in place, and the later nodes'
        # rubies used to be extracted with no cleanup at all
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs(
            "<body><p><span><ruby>山<rt>やま</rt></ruby>"
            "<ruby>路<rt>みち</rt></ruby></span></p></body>",
            "html.parser",
        )
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(
            fp.units[0], "Mountain path.", single_translate=True
        )
        assert soup.find("ruby") is None
        assert "Mountain path." in soup.get_text()

    def test_single_translate_keeps_a_wrapper_holding_a_link_target(self, tmp_path):
        # <span> is an emptied husk, but the <a id> inside it is the target
        # of every cross-reference to this chapter. Deleting the wrapper
        # takes the target with it and breaks links elsewhere in the book —
        # having an id and containing one are the same thing here.
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs(
            '<body><p><span><a id="target">chapter</a></span> one</p></body>',
            "html.parser",
        )
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "ZHANG YI", single_translate=True)
        assert soup.find(id="target") is not None
        assert "ZHANG YI" in soup.get_text()

    def test_single_translate_still_removes_a_husk_that_targets_nothing(self, tmp_path):
        # the complement: an emptied wrapper with nothing to preserve is
        # still removed, so the fix above does not turn into "keep everything"
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p><span><a>chapter</a></span> one</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "ZHANG YI", single_translate=True)
        assert soup.find("span") is None and soup.find("a") is None

    def test_single_translate_keeps_breaks_between_its_lines(self, tmp_path):
        # <br>-separated lines are separate runs now: each is translated
        # where it stands, and the breaks that separate them stay. The old
        # behaviour merged them into one unit and then had to delete the
        # breaks it had made meaningless.
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one<br/>two<br/>three</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        assert [u.text for u in fp.units] == ["one", "two", "three"]
        for unit, translated in zip(fp.units, ["YI", "ER", "SAN"]):
            loader._insert_plan_translation(unit, translated, single_translate=True)
        assert len(soup.find_all("br")) == 2
        assert soup.find("p").get_text().split() == ["YI", "ER", "SAN"]

    def test_single_translate_keeps_breaks_inside_pre(self, tmp_path):
        # inside <pre> a break is part of the preformatted text, not a
        # separator between runs, so the run spans it and it must survive
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><pre>one<br/>two</pre></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        assert len(fp.units) == 1
        loader._insert_plan_translation(fp.units[0], "YI ER", single_translate=True)
        assert soup.find("br") is not None

    def test_single_translate_keeps_breaks_it_does_not_own(self, tmp_path):
        # a leading/trailing break is layout around the unit, not a
        # separator inside it — removing it would change the page
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p><br/>one</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "YI", single_translate=True)
        assert soup.find("br") is not None

    def test_bilingual_mode_leaves_the_original_breaks_alone(self, tmp_path):
        # the source's own line structure must survive: "one" and "two" stay
        # separate lines, and both source lines stay in the document.
        # (This used to assert a single <br>, from when a two-run paragraph
        # was cloned whole. Cloning is what reversed multi-run translations;
        # the run's translation is now anchored to the run instead, which
        # adds a break of its own.)
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one<br/>two</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "YI", single_translate=False)
        assert list(soup.find("p").stripped_strings) == ["one", "YI", "two"]
        assert len(soup.find_all("p")) == 1

    def test_bilingual_multi_run_owner_keeps_each_translation_with_its_run(
        self, tmp_path
    ):
        """Every run of a <br>-separated paragraph is translated where it
        stands, in document order.

        Cloning the owner once per run put all three copies immediately
        after the source *in reverse* — T3, T2, T1 — because each
        insert_after() landed before the previous one, and none of them
        next to the line it translated. <br>-hung verse is the common
        shape here, not a corner case.
        """
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one<br/>two<br/>three</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        assert [u.text for u in fp.units] == ["one", "two", "three"]
        assert {u.owner_runs for u in fp.units} == {3}

        for unit, translated in zip(fp.units, ["T1", "T2", "T3"]):
            loader._insert_plan_translation(unit, translated, single_translate=False)

        # exact document order: each translation directly after its own run
        assert list(soup.find("p").stripped_strings) == [
            "one",
            "T1",
            "two",
            "T2",
            "three",
            "T3",
        ]
        # and still one paragraph — no clone of the whole owner
        assert len(soup.find_all("p")) == 1

    def test_bilingual_anchored_translation_clones_the_run_wrapper(self, tmp_path):
        """A run whose markup covers the whole line renders its translation
        in a clone of that markup, not a bare <span>.

        `span.lin { margin-left: 5em }` is the mahabharata's verse indent:
        a bare <span> put every Sanskrit line at 5em and every English line
        at 0. Ids are stripped from the clone — a second rendering, not a
        second anchor — same as block clones."""
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs(
            '<body><p><span class="lin" id="v1">one</span><br/>'
            '<span class="lin">two</span></p></body>',
            "html.parser",
        )
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        assert [u.text for u in fp.units] == ["one", "two"]
        for unit, translated in zip(fp.units, ["T1", "T2"]):
            loader._insert_plan_translation(unit, translated, single_translate=False)

        trans = [s for s in soup.find_all("span") if s.get_text() in ("T1", "T2")]
        assert [s.get("class") for s in trans] == [["lin"], ["lin"]]
        assert all(s.get("id") is None for s in trans)
        assert list(soup.find("p").stripped_strings) == ["one", "T1", "two", "T2"]

    def test_bilingual_anchored_translation_keeps_bare_span_for_fragment_markup(
        self, tmp_path
    ):
        """The complement: markup covering only part of the run must not be
        cloned — an <em> around one word would italicize the whole
        translated sentence."""
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one <em>two</em><br/>three</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        assert fp.units[0].text == "one two"
        loader._insert_plan_translation(fp.units[0], "T1", single_translate=False)

        assert len(soup.find_all("em")) == 1  # no cloned <em>
        trans = soup.find("span")
        assert trans.get_text() == "T1"

    def test_bilingual_run_split_by_a_retained_skip_stays_paired(self, tmp_path):
        """The other way an owner holds several runs: something retained
        renders between them. Here an excluded <code> splits the sentence,
        so the two halves must keep their own translations."""
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>before <code>ls</code> after</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html", exclude_tags=("code",))
        assert [u.text for u in fp.units] == ["before", "after"]

        for unit, translated in zip(fp.units, ["QIAN", "HOU"]):
            loader._insert_plan_translation(unit, translated, single_translate=False)

        assert list(soup.find("p").stripped_strings) == [
            "before",
            "QIAN",
            "ls",
            "after",
            "HOU",
        ]
        assert len(soup.find_all("p")) == 1

    def test_the_break_before_a_translation_is_written_self_closing(self, tmp_path):
        """`<br></br>` is well-formed XML — epubcheck accepts it — but an
        HTML5 parser reads the closing tag as a second <br>, so a reading
        system in compatibility mode shows a double line break between every
        run and its translation. bs4 writes the pair unless the tag is
        marked as a void element."""
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one<br/>two</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "YI", single_translate=False)

        written = soup.decode()
        assert "<br></br>" not in written, written
        assert "<br/>" in written
        # order is what a reader sees: the run, then the break, then the
        # translation — not the translation wedged between run and break
        assert soup.find("p").decode_contents().startswith("one<br/><span>YI</span>")

    def test_make_tag_knows_which_tags_are_void(self):
        """The rule the insertion paths depend on, stated once. bs4 learns
        void elements from a parser builder, and a hand-built tag has none;
        `soup.new_tag()` cannot stand in because `element.soup` is None on
        parsed nodes, so an element cannot hand us its tree."""
        from bs4 import BeautifulSoup

        from book_maker.loader.helper import make_tag

        assert str(make_tag("br")) == "<br/>"
        assert str(make_tag("hr")) == "<hr/>"
        assert str(make_tag("span")) == "<span></span>"
        assert 'style="x"' in str(make_tag("span", style="x"))
        # the premise of the docstring above, pinned: if a future bs4 starts
        # populating .soup, new_tag becomes an option and this can be revisited
        node = BeautifulSoup("<body><p>hi</p></body>", "html.parser").find("p")
        assert getattr(node, "soup", None) is None

    def test_bilingual_single_run_owner_still_clones(self, tmp_path):
        """The rule must not overreach: an ordinary one-run paragraph keeps
        the readable side-by-side block it always produced."""
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>alpha</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        assert fp.units[0].owner_runs == 1
        loader._insert_plan_translation(fp.units[0], "JIA", single_translate=False)

        assert [p.get_text() for p in soup.find_all("p")] == ["alpha", "JIA"]

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
        m, conditional = parse_css_display(
            "@media print { .noscreen { display: none } }"
        )
        assert m == {} and conditional == {}

    def test_media_screen_rules_are_unwrapped(self):
        m, conditional = parse_css_display(
            "@media screen { span.verse { display: block } }"
        )
        assert m[("span", "verse")] == "block"
        assert conditional == {}

    def test_font_face_and_page_blocks_dropped(self):
        m, _conditional = parse_css_display(
            "@font-face { font-family: x; src: url(y) } "
            "@page { margin: 1em } p { display: block }"
        )
        assert m == {("p", None): "block"}

    def test_supports_is_conditional_and_nested_print_still_drops(self):
        # @supports is true on some reading systems and false on others, so
        # its rules are evidence, never applied; a print branch inside it is
        # dropped either way.
        m, conditional = parse_css_display(
            "@supports (display: flex) { @media print { .a { display:none } } "
            ".b { display: inline } }"
        )
        assert m == {}
        assert (None, "a") not in conditional
        assert conditional[(None, "b")] == ["@supports (display: flex)"]

    # -- item 5: br / whitespace glue --------------------------------------

    def test_br_separates_runs(self):
        # a <br> renders between the lines, so one translation cannot span
        # both: each line is its own run, written back where it belongs
        fp, _ = self._partition("<body><p>line one<br/>line two</p></body>")
        assert [u.text for u in fp.units] == ["line one", "line two"]

    def test_br_inside_pre_is_not_a_barrier(self):
        # inside <pre> the break is already rendered by the source's own
        # whitespace; splitting there would fragment preformatted text
        fp, _ = self._partition("<body><pre>line one<br/>line two</pre></body>")
        assert [u.text for u in fp.units] == ["line oneline two"]

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
        # segment-level classification: the brackets are prose punctuation
        # that happens to sit in its own text node, not decorative symbols
        assert [u.text for u in fp.units] == ["he walks [back and forth,]"]
        # nothing was charged to "symbol": the punctuation is in the text
        assert fp.skipped["symbol"] == 0

    def test_isolated_punctuation_stays_in_the_segment(self):
        fp, _ = self._partition("<body><p><b>one</b> [ <b>two</b></p></body>")
        assert [u.text for u in fp.units] == ["one [ two"]

    def test_a_symbol_only_segment_still_skips(self):
        fp, _ = self._partition("<body><p><span>\u2766</span></p></body>")
        assert fp.units == []
        assert fp.skipped["symbol"] == 1

    def test_unit_clean_text_glues_skipped_nodes_too(self):
        soup = bs(
            "<body><p>he <em>walks</em> [<em>back and forth</em>,]</p></body>",
            "html.parser",
        )
        assert (
            unit_clean_text(soup.p, DisplayResolver([])) == "he walks [back and forth,]"
        )

    # -- item 6: svg / math ------------------------------------------------

    def test_svg_and_math_are_non_content(self):
        fp, _ = self._partition(
            "<body><p>Formula <math><mi>x</mi><mtext>otherwise</mtext></math> "
            "appears here.</p>"
            "<svg><title>Diagram title</title><text>axis label</text></svg></body>"
        )
        # <math> renders between the words, so it separates the two runs
        assert [u.text for u in fp.units] == ["Formula", "appears here."]
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
        plan.save_json(str(path), book_path=str(ANIMAL_FARM))
        assert json.loads(path.read_text())["schema_version"] == PLAN_SCHEMA_VERSION

    def test_rows_carry_the_evidence_a_planner_judges_from(self, tmp_path):
        # tag name (signature), count, total + share, and average length:
        # the same numbers the uncertainty heuristic keyed on
        import json

        plan = build_plan(epub.read_epub(str(ANIMAL_FARM)))
        path = tmp_path / "p.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM))
        rows = json.loads(path.read_text())["signatures"]
        for row in rows:
            assert row["units"] > 0
            assert row["mean_chars"] == round(row["chars"] / row["units"], 1)
            assert 0 <= row["pct"] <= 100
            assert row["samples"]

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
            resumed.plan_mode = True
            resumed.translate_tags = "auto"
            with pytest.raises(SystemExit) as excinfo:
                resumed.make_bilingual_book()
            assert excinfo.value.code == 1
        finally:
            loader_mod.PLAN_SCHEMA_VERSION = old

    def test_resume_refuses_a_legacy_tag_mode_cache(self, tmp_path):
        # review finding: a cache from an ordinary --translate-tags p run is
        # a bare list with no fingerprint. Its slots index a p-tag sequence,
        # not the plan's unit list, so replaying it positionally pairs
        # headings and verse lines with unrelated prose translations.
        # Upstream #549 generalized the refusal: every checkpoint carries a
        # version, and a legacy bare list is rejected in both modes.
        import pickle

        import book_maker.loader.epub_loader as loader_mod

        loader, src = _make_loader(tmp_path, FakeModel)
        with open(loader.bin_path, "wb") as f:
            pickle.dump(["translated p 1", "translated p 2"], f)

        # the refusal now lands in the constructor's load_state(), before any
        # plan is built — a legacy cache never reaches the translation loop
        with pytest.raises((SystemExit, ValueError)) as excinfo:
            resumed = loader_mod.EPUBBookLoader(
                str(src), FakeModel, "dummy-key", resume=True, language="zh-hans"
            )
            resumed.plan_mode = True
            resumed.translate_tags = "auto"
            resumed.make_bilingual_book()
        if isinstance(excinfo.value, SystemExit):
            assert excinfo.value.code == 1
        else:
            assert "Legacy EPUB resume checkpoints" in str(excinfo.value)

    def test_resume_refused_after_the_book_file_changes(self, tmp_path):
        # review finding: deleting the (sha-mismatching) plan JSON is the
        # documented recovery from a replaced book — the cache must not
        # survive it and apply the old book's translations to new units.
        import book_maker.loader.epub_loader as loader_mod

        class ExplodesOnBeasts(FakeModel):
            def translate_list(self, text_list):
                if any("Beasts of England" in t for t in text_list):
                    raise RuntimeError("boom")
                return super().translate_list(text_list)

        setup, src = _make_loader(tmp_path, FakeModel)
        _write_decided_plan(setup)
        loader, _ = _make_loader(tmp_path, ExplodesOnBeasts)
        loader.plan_classify = "agent"
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

        shutil.copy(LIBER_ESTHER, src)  # same path, different book
        (src.parent / (src.stem + "_plan.json")).unlink()

        resumed = loader_mod.EPUBBookLoader(
            str(src), FakeModel, "dummy-key", resume=True, language="zh-hans"
        )
        resumed.plan_mode = True
        resumed.translate_tags = "auto"
        with pytest.raises(SystemExit) as excinfo:
            resumed.make_bilingual_book()
        assert excinfo.value.code == 1

    def test_plan_resume_finishes_the_book_without_retranslating(self, tmp_path):
        # the checkpoint payload moved into upstream #549's versioned format
        # (job ids + translations, plan_fingerprint alongside), so the happy
        # path needs a round trip of its own: every refusal test above would
        # still pass if resume simply never reused anything.
        import book_maker.loader.epub_loader as loader_mod

        class StopsPartway(FakeModel):
            calls = 0
            budget = 3

            def translate_list(self, text_list):
                type(self).calls += 1
                if type(self).calls > type(self).budget:
                    raise RuntimeError("boom")
                return super().translate_list(text_list)

        loader, src = _make_loader(tmp_path, StopsPartway)
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()
        done = len(loader.p_to_save)
        assert done > 0, "nothing was checkpointed, the test proves nothing"

        StopsPartway.calls = 0
        StopsPartway.budget = 10**6
        resumed = loader_mod.EPUBBookLoader(
            str(src), StopsPartway, "dummy-key", resume=True, language="zh-hans"
        )
        resumed.plan_mode = True
        resumed.translate_tags = "auto"
        assert len(resumed.p_to_save) == done
        resumed.make_bilingual_book()

        reference, _ = _make_loader(tmp_path / "ref", FakeModel)
        reference.make_bilingual_book()
        assert resumed.p_to_save == reference.p_to_save
        # what the checkpoint already holds must not be sent again
        sent = [t for call in resumed.translate_model.list_calls for t in call]
        assert len(sent) == len(reference.p_to_save) - done

    def test_empty_filtered_plan_fails_loud(self, tmp_path):
        # review finding: coverage of an empty plan is vacuously 100%, so a
        # misspelled --only_filelist used to pass the gate and produce a
        # book with every document dropped
        loader, _ = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "no_such_file.html"
        with pytest.raises(SystemExit) as excinfo:
            loader.make_bilingual_book()
        assert excinfo.value.code == 1

    def test_clone_fatal_flag_propagates_without_an_exception(self, tmp_path):
        # review finding: gemini marks itself fatal and returns error
        # markers instead of raising, so a clone's death stayed invisible to
        # the shared model and the other workers kept firing
        loader, _ = _make_loader(tmp_path, FakeModel)
        clone = copy_mod.copy(loader.translate_model)
        clone.context_list = []
        clone.context_translated_list = []

        def marker_return(text_list):
            clone._fatal_error_detected = True
            return [clone.TRANSLATION_ERROR_MARKER] * len(text_list)

        clone.translate_list = marker_return
        loader._translate_texts_aligned(["one", "two"], clone)
        assert loader.translate_model._fatal_error_detected

    def test_recovery_book_replay_honors_file_filters(self, tmp_path):
        # review finding: the positional cache is written over the filtered
        # item sequence, so a recovery book that walks every document
        # consumes slot 0 in an unselected chapter and shifts every
        # translation onto unrelated text.
        # Upstream #549 replays the same job plan the run executed, which
        # drops only-list-excluded documents from the recovery book outright;
        # either shape is fine as long as no unselected chapter carries
        # translations.
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()
        loader._save_temp_book()

        temp = src.parent / (src.stem + "_bilingual_temp.epub")
        with zipfile.ZipFile(temp) as z:
            names = [n for n in z.namelist() if n.endswith("html")]
            selected = next(n for n in names if "index_split_004" in n)
            unselected = [n for n in names if "index_split_004" not in n]
            for name in unselected:
                assert "T[" not in z.read(name).decode(
                    "utf-8", "ignore"
                ), f"unselected chapter {name} consumed cache slots"
            assert "T[" in z.read(selected).decode("utf-8", "ignore")

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


# ------------------------------------------- llm signature classification


from book_maker.loader.classify.candidates import gather_candidates
from book_maker.loader.classify.model import (
    PAGE_SIZE,
    VERDICTS,
    PlanClassifyError,
    PlanClassifyFatal,
    PlanUnresolvedError,
    build_prompt,
    build_schema,
    classify_plan,
    lint_verdicts,
    verdict_decisions,
)
from book_maker.loader.ledger import Ledger, PlanLedgerError, make_key


def _ledger(rows):
    """rows: [(scope, signature, [texts])] -> a finalized Ledger."""
    ledger = Ledger()
    for scope, signature, texts in rows:
        for text in texts:
            ledger.add_occurrence(scope, signature, len(text), text)
    return ledger.finalize(sum(len(t) for _s, _g, ts in rows for t in ts))


PROSE_LINE = "He who saw the Deep, the country's foundation, and knew it all."


class FakeClassifier:
    model = "fake-clf"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def structured_json(self, prompt, schema, model=None, accept=None):
        self.calls.append({"prompt": prompt, "schema": schema, "model": model})
        return self.result


def _answer(keys, verdict="translate", content_type="prose"):
    return {k: {"verdict": verdict, "content_type": content_type} for k in keys}


class TestCandidateTotality:
    """Every signature is a question. The shape filters that used to stand
    between a signature and the classifier are the defect, not the design."""

    def test_code_like_and_name_like_signatures_are_both_asked_about(self):
        # the two 260811 smoke failures, verbatim: pre.screen was withheld as
        # "long and varied", p.editor as "poetry"
        ledger = _ledger(
            [
                (
                    "block",
                    "pre.screen",
                    [f"<div id='x{i}'>markup line</div>" * 6 for i in range(103)],
                ),
                ("block", "p.editor", ["Brian Sawyer", "Dan Fauxsmith"]),
                ("block", "h2.chapter_title", [f"Chapter {i}" for i in range(9)]),
                ("block", "p", [PROSE_LINE] * 60),
            ]
        )
        keys = [c["key"] for c in gather_candidates(ledger)]
        for expected in (
            "block:pre.screen",
            "block:p.editor",
            "block:h2.chapter_title",
            "block:p",
        ):
            assert expected in keys, f"{expected} was withheld from the classifier"

    def test_candidates_are_every_undecided_row_largest_first(self):
        ledger = _ledger(
            [
                ("block", "p", [PROSE_LINE] * 20),
                ("block", "p.note", ["note"] * 3),
                ("inline", "span.line-no", ["I 5", "I 6"]),
            ]
        )
        assert [c["key"] for c in gather_candidates(ledger)] == list(ledger.rows)
        assert len(gather_candidates(ledger)) == 3
        chars = [c["chars"] for c in gather_candidates(ledger)]
        assert chars == sorted(chars, reverse=True)

        ledger.decide("block:p.note", "translate", "user", "prose")
        assert [c["key"] for c in gather_candidates(ledger)] == [
            "block:p",
            "inline:span.line-no",
        ]

    def test_every_candidate_reaches_the_schema_and_the_prompt(self):
        ledger = _ledger(
            [
                ("block", "pre.screen", ["<b>code</b>"] * 4),
                ("inline", "span.line-no", ["I 5"]),
            ]
        )
        candidates = gather_candidates(ledger)
        schema = build_schema(candidates)
        assert set(schema["schema"]["required"]) == {
            "block:pre.screen",
            "inline:span.line-no",
        }
        prompt = build_prompt(candidates)
        assert "block:pre.screen" in prompt and "inline:span.line-no" in prompt

    def test_no_shape_gate_constants_survive(self):
        # zombie guard: re-adding any of these re-adds a decision we made
        # for the model without showing it the question
        from book_maker.loader.classify import candidates as mod

        for gone in (
            "CERTAIN_TAGS",
            "UNCERTAIN_MAX_PCT",
            "UNCERTAIN_MEAN_CHARS",
            "UNCERTAIN_UNIQUE_RATIO",
            "uncertain_candidates",
        ):
            assert not hasattr(mod, gone), f"{gone} is back"


class TestVerdictPersistence:
    def test_translate_verdicts_persist_with_provenance(self):
        ledger = _ledger([("block", "p", ["prose"]), ("block", "p.c", ["(c) 2020"])])
        clf = FakeClassifier(
            {
                "block:p": {"verdict": "translate", "content_type": "prose"},
                "block:p.c": {
                    "verdict": "skip",
                    "content_type": "publisher boilerplate",
                },
            }
        )
        decisions, _ = classify_plan(ledger, clf)
        for key, (verdict, content_type) in decisions.items():
            ledger.decide(key, verdict, "llm", content_type)

        assert ledger.rows["block:p"]["action"] == "translate"
        assert ledger.rows["block:p"]["decided_by"] == "llm"
        assert ledger.rows["block:p"]["content_type"] == "prose"
        assert ledger.rows["block:p.c"]["action"] == "skip"
        assert ledger.undecided_keys() == []

    def test_no_llm_skip_action_exists(self):
        from book_maker.loader.plan import VALID_PLAN_ACTIONS

        assert "llm-skip" not in VALID_PLAN_ACTIONS
        assert "force-translate" not in VALID_PLAN_ACTIONS
        assert set(VALID_PLAN_ACTIONS) == {"translate", "skip"}

    def test_round_trip_is_idempotent(self, tmp_path):
        ledger = _ledger([("block", "p", ["prose"]), ("block", "p.c", ["(c)"])])
        ledger.decide("block:p", "translate", "llm", "prose")
        ledger.decide("block:p.c", "skip", "agent", "boilerplate")
        meta = {"book_sha256": "a" * 64}
        path = tmp_path / "plan.json"
        ledger.save(path, meta)
        first = path.read_text()

        reloaded = Ledger.load(path, expected_sha256="a" * 64)
        reloaded.save(path, meta)
        assert path.read_text() == first
        # a skipped row keeps its row, its evidence and its provenance
        assert reloaded.rows["block:p.c"]["decided_by"] == "agent"
        assert reloaded.rows["block:p.c"]["samples"]


class TestFailClosedResidue:
    def test_unsure_leaves_the_row_undecided_and_raises(self):
        ledger = _ledger([("block", "p", ["prose"]), ("block", "p.x", ["???"])])
        clf = FakeClassifier(
            {
                "block:p": {"verdict": "translate", "content_type": "prose"},
                "block:p.x": {"verdict": "unsure", "content_type": "unclear"},
            }
        )
        with pytest.raises(PlanUnresolvedError) as excinfo:
            classify_plan(ledger, clf)
        error = excinfo.value
        assert error.unresolved == ["block:p.x"]
        assert error.resolved == {"block:p": ("translate", "prose")}

    def test_a_missing_content_type_is_not_an_answer(self):
        # a reply that skipped naming the content did not do the reasoning
        # the schema asked for
        ledger = _ledger([("block", "p", ["prose"])])
        candidates = gather_candidates(ledger)
        verdicts, answered = lint_verdicts({"block:p": {"verdict": "skip"}}, candidates)
        assert answered == set()
        assert verdicts["block:p"][0] == "unsure"

        verdicts, answered = lint_verdicts(
            {"block:p": {"verdict": "skip", "content_type": "  "}}, candidates
        )
        assert answered == set()

    def test_partial_page_failure_keeps_the_verdicts_it_bought(self):
        rows = [("block", f"p.h{i}", [f"HEAD {i}"]) for i in range(20)]

        class SecondPageExplodes:
            model = "fake"
            calls = 0

            def structured_json(self, prompt, schema, model=None, accept=None):
                type(self).calls += 1
                if self.calls > 1:
                    raise RuntimeError("boom")
                return _answer(schema["schema"]["required"], "skip", "running head")

        with pytest.raises(PlanUnresolvedError) as excinfo:
            classify_plan(_ledger(rows), SecondPageExplodes())
        # the first page's twelve answers were paid for and are still true
        assert len(excinfo.value.resolved) == PAGE_SIZE
        assert len(excinfo.value.unresolved) == 20 - PAGE_SIZE
        assert "boom" in str(excinfo.value)

    def test_partial_ledger_is_written_atomically(self, tmp_path):
        # a reader must never see a half-written plan: it cannot tell a
        # crashed writer from a damaged plan
        ledger = _ledger([("block", "p", ["prose"])])
        ledger.decide("block:p", "translate", "llm", "prose")
        path = tmp_path / "plan.json"

        real_replace = os.replace
        seen = {}

        def spy(src, dst):
            seen["tmp_existed"] = os.path.exists(src)
            seen["dst_existed_before"] = os.path.exists(dst)
            return real_replace(src, dst)

        with mock.patch("book_maker.loader.ledger.os.replace", spy):
            ledger.save(path, {"book_sha256": "a" * 64})
        assert seen == {"tmp_existed": True, "dst_existed_before": False}
        assert json.loads(path.read_text())["signatures"][0]["action"] == "translate"

    def test_a_failed_save_leaves_no_temp_file_behind(self, tmp_path):
        ledger = _ledger([("block", "p", ["prose"])])
        path = tmp_path / "plan.json"
        with mock.patch(
            "book_maker.loader.ledger.json.dump", side_effect=RuntimeError("disk")
        ):
            with pytest.raises(RuntimeError):
                ledger.save(path, {"book_sha256": "a" * 64})
        assert list(tmp_path.iterdir()) == []


class TestLedgerContract:
    def test_schema_mismatch_is_a_hard_error(self, tmp_path):
        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "book_sha256": "a" * 64,
                    "signatures": [{"key": "block:p", "action": "translate"}],
                }
            )
        )
        with pytest.raises(PlanLedgerError, match="schema 3"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_undecided_rows_refuse_to_translate(self, tmp_path):
        ledger = _ledger([("block", "p", ["prose"])])
        path = tmp_path / "plan.json"
        ledger.save(path, {"book_sha256": "a" * 64})
        loaded = Ledger.load(path, expected_sha256="a" * 64)
        with pytest.raises(PlanLedgerError, match="undecided"):
            loaded.require_decided(path)

    def test_a_verdict_about_an_unknown_row_is_a_bug(self):
        ledger = _ledger([("block", "p", ["prose"])])
        with pytest.raises(PlanLedgerError, match="never asked about"):
            ledger.decide("block:nope", "skip", "llm", "x")

    def test_samples_stride_the_whole_book_not_its_first_page(self):
        texts = [f"line {i:03d}" for i in range(200)]
        ledger = _ledger([("block", "p.v", texts)])
        samples = ledger.rows["block:p.v"]["samples"]
        assert len(samples) == 5
        assert samples[0] == "line 000"
        # evidence from the end of the book, not five consecutive openers
        assert any(s > "line 100" for s in samples)

    def test_the_longest_occurrence_is_always_shown(self):
        # GhV-oeb-page.epub, live 260813: block:article strided to five short
        # credit lines, so the model named it "publisher boilerplate" and
        # skipped 4102 chars — including a 1552-char acknowledgements
        # paragraph of ordinary prose that no sample had shown.
        texts = [f"credit line {i}" for i in range(40)]
        texts.insert(
            17,
            "Nous exprimons nos tres vifs remerciements aux 900 "
            "membres des commissions de degustation reunies "
            "specialement pour l'elaboration de ce guide.",
        )
        ledger = _ledger([("block", "article", texts)])
        samples = ledger.rows["block:article"]["samples"]
        assert len(samples) == 5
        assert any(s.startswith("Nous exprimons") for s in samples), samples

    def test_reserving_the_longest_slot_keeps_the_stride(self):
        texts = [f"line {i:03d}" for i in range(200)]
        ledger = _ledger([("block", "p.v", texts)])
        samples = ledger.rows["block:p.v"]["samples"]
        # all same length here, so nothing is displaced and the stride stands
        assert samples[0] == "line 000"
        assert any(s > "line 100" for s in samples)


class TestConsideredButUndecided:
    """An "unsure" is a refusal to decide, not a refusal to look.

    The name the model produced is paid-for evidence, and it used to be
    dropped on every path — including the graceful one — so an agent
    answering the row started from nothing.
    """

    def test_unsure_names_survive_a_later_fatal_page(self):
        """Page one answers twelve rows "unsure" (with names); page two dies
        of a transport failure. Nothing is decided — correctly — but the
        twelve names must come back out with the error instead of vanishing."""
        from book_maker.loader.classify.model import PlanClassifyFatal, classify_plan

        rows = [("block", f"p.s{i}", [f"SAMPLE {i}"]) for i in range(13)]
        ledger = _ledger(rows)

        class DiesOnSecondPage:
            model = "fake-clf"

            def __init__(self):
                self.calls = 0

            def structured_json(self, prompt, schema, model=None, accept=None):
                self.calls += 1
                if self.calls > 1:
                    raise PlanClassifyFatal("transport down")
                return {
                    key: {"verdict": "unsure", "content_type": "editorial apparatus"}
                    for key in schema["schema"]["required"]
                }

        with pytest.raises(PlanClassifyFatal) as failure:
            classify_plan(ledger, DiesOnSecondPage())

        considered = failure.value.considered
        assert considered, "the names from the answered page were thrown away"
        assert set(considered.values()) == {"editorial apparatus"}

        # and they can be recorded without deciding anything
        for key, name in considered.items():
            ledger.note_content_type(key, name)
        assert ledger.rows["block:p.s0"]["content_type"] == "editorial apparatus"
        assert ledger.rows["block:p.s0"]["action"] is None
        assert ledger.rows["block:p.s0"]["decided_by"] is None
        assert len(ledger.undecided_keys()) == 13

    def test_unsure_names_ride_along_with_the_graceful_failure(self):
        from book_maker.loader.classify.model import PlanUnresolvedError, classify_plan

        ledger = _ledger([("block", "p", [PROSE_LINE]), ("block", "p.x", ["X"])])
        clf = FakeClassifier(
            {
                "block:p": {"verdict": "translate", "content_type": "prose"},
                "block:p.x": {"verdict": "unsure", "content_type": "sigla"},
            }
        )
        with pytest.raises(PlanUnresolvedError) as failure:
            classify_plan(ledger, clf)

        assert failure.value.resolved == {"block:p": ("translate", "prose")}
        assert failure.value.considered["block:p.x"] == "sigla"

    def test_a_name_never_decides_anything_by_itself(self):
        ledger = _ledger([("block", "p", ["prose"])])
        ledger.note_content_type("block:p", "running head")
        assert ledger.rows["block:p"]["action"] is None
        # and a decided row is not silently re-named
        ledger.decide("block:p", "skip", "user", "running head")
        assert ledger.note_content_type("block:p", "something else") is None
        assert ledger.rows["block:p"]["content_type"] == "running head"


class TestInlineConditionalCssEvidence:
    def test_an_inline_row_records_the_css_that_hides_it_on_some_devices(self):
        """`@media (max-width: 600px) { span.line-no { display: none } }` is
        the strongest evidence there is that a span carries apparatus rather
        than prose — and the inline decision path is exactly where it was
        being dropped. Evidence, never a verdict: the row still asks."""
        from book_maker.loader.plan import TranslationPlan, partition_soup

        resolver = _make_resolver(
            "@media (max-width: 600px) { span.line-no { display: none } }"
        )
        soup = bs(
            "<body><p>The wine-dark sea "
            '<span class="line-no">I 5</span> rolled on.</p></body>',
            "html.parser",
        )
        fp = partition_soup(soup, resolver, "x.html")

        plan = TranslationPlan([fp], (), 8)
        ledger = plan.build_ledger()

        row = ledger.rows["inline:span.line-no"]
        assert row["conditional_css"] == ["@media (max-width: 600px)"]
        assert row["action"] is None

    def test_an_inline_row_with_no_conditional_css_stays_empty(self):
        from book_maker.loader.plan import TranslationPlan, partition_soup

        soup = bs(
            '<body><p>Prose <span class="term">here</span> ends.</p></body>',
            "html.parser",
        )
        fp = partition_soup(soup, _make_resolver(""), "x.html")
        plan = TranslationPlan([fp], (), 8)

        assert plan.build_ledger().rows["inline:span.term"]["conditional_css"] == []


class TestLedgerRowStateMachine:
    """A row is either an open question or an accountable decision.

    Anything between the two is the state schema 4 exists to abolish: an
    action nobody is recorded as having taken, on content nobody named.
    """

    def _row(self, **overrides):
        row = {
            "key": "block:p.note",
            "scope": "block",
            "units": 1,
            "chars": 10,
            "samples": ["sample"],
            "conditional_css": [],
            "action": None,
            "decided_by": None,
            "content_type": None,
            "disposition": None,
        }
        row.update(overrides)
        return row

    def _write(self, tmp_path, *rows):
        from book_maker.loader.ledger import PLAN_SCHEMA_VERSION

        path = tmp_path / "plan.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": PLAN_SCHEMA_VERSION,
                    "book_sha256": "a" * 64,
                    "signatures": list(rows),
                }
            )
        )
        return path

    # ------------------------------------------------------------ decide()

    def test_a_hand_edited_list_fails_clean_not_with_a_traceback(self, tmp_path):
        # These are the fields the tool's own instructions tell people to
        # edit, so `"decided_by": ["llm"]` is one bracket away — and
        # `x in frozenset` needs a hashable x, so it came back as a raw
        # `TypeError: unhashable type: 'list'` instead of this module's
        # promise: a plan that cannot be trusted fails loud, but clean.
        path = self._write(
            tmp_path, self._row(action="skip", decided_by=["llm"], content_type="x")
        )
        with pytest.raises(PlanLedgerError, match="must be a string"):
            Ledger.load(path, expected_sha256="a" * 64)

        path = self._write(tmp_path, self._row(action={"a": 1}, decided_by="llm"))
        with pytest.raises(PlanLedgerError, match="must be a string"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_an_action_without_a_decider_is_refused(self):
        ledger = _ledger([("block", "p", ["prose"])])
        with pytest.raises(PlanLedgerError, match="decided_by null"):
            ledger.decide("block:p", "skip", None, "running head")

    def test_an_action_without_a_content_type_is_refused(self):
        ledger = _ledger([("block", "p", ["prose"])])
        with pytest.raises(PlanLedgerError, match="no content_type"):
            ledger.decide("block:p", "skip", "llm")

    def test_a_blank_content_type_is_no_content_type(self):
        ledger = _ledger([("block", "p", ["prose"])])
        with pytest.raises(PlanLedgerError, match="no content_type"):
            ledger.decide("block:p", "translate", "user", "   ")

    def test_a_decider_without_an_action_is_refused(self):
        ledger = _ledger([("block", "p", ["prose"])])
        with pytest.raises(PlanLedgerError, match="was not made"):
            ledger.decide("block:p", None, "llm", "prose")

    def test_an_unsure_name_is_kept_and_can_be_ruled_on_later(self):
        """A model that answers "unsure" still named the content. That name
        is evidence: it stays on the undecided row, and a later decision may
        rest on it instead of re-deriving it."""
        ledger = _ledger([("block", "p", ["prose"])])
        ledger.rows["block:p"]["content_type"] = "editorial apparatus"

        ledger.decide("block:p", "skip", "agent")

        assert ledger.rows["block:p"]["content_type"] == "editorial apparatus"
        assert ledger.rows["block:p"]["decided_by"] == "agent"

    # -------------------------------------------------------------- load()

    def test_load_refuses_an_action_with_no_provenance(self, tmp_path):
        path = self._write(tmp_path, self._row(action="skip"))
        with pytest.raises(PlanLedgerError, match="decided_by null"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_an_action_with_no_content_type(self, tmp_path):
        path = self._write(tmp_path, self._row(action="skip", decided_by="user"))
        with pytest.raises(PlanLedgerError, match="no content_type"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_a_decider_with_no_action(self, tmp_path):
        path = self._write(tmp_path, self._row(decided_by="llm"))
        with pytest.raises(PlanLedgerError, match="was not made"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_duplicate_keys(self, tmp_path):
        """Last-wins would make the effective decision depend on JSON order
        and silently discard one of two contradictory edits."""
        path = self._write(
            tmp_path,
            self._row(action="translate", decided_by="user", content_type="prose"),
            self._row(action="skip", decided_by="user", content_type="prose"),
        )
        with pytest.raises(PlanLedgerError, match="duplicate key"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_a_scope_that_contradicts_its_key(self, tmp_path):
        path = self._write(tmp_path, self._row(scope="inline"))
        with pytest.raises(PlanLedgerError, match="contradicts the key"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_a_row_missing_a_contract_field(self, tmp_path):
        row = self._row()
        del row["decided_by"]
        path = self._write(tmp_path, row)
        with pytest.raises(PlanLedgerError, match="missing field"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_malformed_evidence(self, tmp_path):
        path = self._write(tmp_path, self._row(samples="not a list"))
        with pytest.raises(PlanLedgerError, match="malformed samples"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_refuses_an_unusable_key(self, tmp_path):
        path = self._write(tmp_path, self._row(key="p.note"))
        with pytest.raises(PlanLedgerError, match="unusable key"):
            Ledger.load(path, expected_sha256="a" * 64)

    def test_load_keeps_an_undecided_row_that_names_its_content(self, tmp_path):
        path = self._write(tmp_path, self._row(content_type="editorial apparatus"))
        loaded = Ledger.load(path, expected_sha256="a" * 64)
        assert loaded.rows["block:p.note"]["content_type"] == "editorial apparatus"
        assert loaded.rows["block:p.note"]["action"] is None

    # ---------------------------------------------------- require_decided()

    def test_require_decided_catches_a_row_written_behind_the_api(self, tmp_path):
        """Defense in depth: this is the last gate before money is spent, so
        it re-checks rather than trusting that every writer used decide()."""
        ledger = _ledger([("block", "p", ["prose"])])
        ledger.rows["block:p"]["action"] = "translate"  # no decided_by, no name

        with pytest.raises(PlanLedgerError, match="decided_by null"):
            ledger.require_decided(tmp_path / "plan.json")


class TestClassifyPaging:
    def test_candidates_are_paged_and_merged(self):
        rows = [("block", f"p.h{i}", [f"HEAD {i}"]) for i in range(25)]

        class Pager:
            model = "fake"

            def __init__(self):
                self.pages = []

            def structured_json(self, prompt, schema, model=None, accept=None):
                keys = schema["schema"]["required"]
                self.pages.append(keys)
                return _answer(keys, "skip", "running head")

        clf = Pager()
        decisions, candidates = classify_plan(_ledger(rows), clf)

        assert len(candidates) == 25
        assert [len(p) for p in clf.pages] == [12, 12, 1]
        assert len(decisions) == 25
        flat = [k for page in clf.pages for k in page]
        assert len(set(flat)) == 25

    def test_an_unanswered_page_is_re_asked_in_smaller_pieces(self):
        rows = [("block", f"p.h{i}", [f"HEAD {i}"]) for i in range(4)]

        class HalfDeaf:
            model = "fake"

            def __init__(self):
                self.sizes = []

            def structured_json(self, prompt, schema, model=None, accept=None):
                keys = list(schema["schema"]["required"])
                self.sizes.append(len(keys))
                if len(keys) > 1:
                    # answers all but the last: the ladder should re-ask
                    # only what went unanswered, not halve blindly
                    return _answer(keys[:-1], "translate", "prose")
                return _answer(keys, "translate", "prose")

        clf = HalfDeaf()
        decisions, _ = classify_plan(_ledger(rows), clf)
        assert len(decisions) == 4
        assert clf.sizes[0] == 4 and clf.sizes[1] == 1

    def test_a_failed_singleton_keeps_the_answers_around_it(self):
        # The page answered three of four; the fourth could not be settled
        # even alone. Unwinding out of the split loop threw away three
        # answers that were asked for, paid for, and correct.
        rows = [("block", f"p.h{i}", [f"HEAD {i}"]) for i in range(4)]

        class DeafOnOne:
            model = "fake"

            def structured_json(self, prompt, schema, model=None, accept=None):
                keys = [k for k in schema["schema"]["required"] if k != "block:p.h3"]
                return _answer(keys, "translate", "prose")

        with pytest.raises(PlanUnresolvedError) as info:
            classify_plan(_ledger(rows), DeafOnOne())
        assert info.value.unresolved == ["block:p.h3"]
        assert set(info.value.resolved) == {
            "block:p.h0",
            "block:p.h1",
            "block:p.h2",
        }


class TestModePolicy:
    """What each mode does with the plan *file* is a table, not a condition
    repeated wherever the loader happens to branch on the mode's name."""

    def test_every_mode_carries_a_policy(self):
        from book_maker.loader.classify import MODES, mode_policy

        assert [mode_policy(m).name for m in MODES] == list(MODES)
        # "most" asks nothing, so it neither reads nor writes the file
        assert not mode_policy("most").reads_saved_plan
        assert not mode_policy("most").writes_plan_file
        # the agent handoff *is* the job: stopping there is a success
        assert mode_policy("agent").handoff_exit_code == 0
        assert mode_policy("model").handoff_exit_code == 1

    def test_an_invented_mode_is_refused(self):
        from book_maker.loader.classify import mode_policy

        with pytest.raises(ValueError, match="unknown --plan-classify mode"):
            mode_policy("vibes")


class TestFileSha256Cache:
    def test_the_book_is_hashed_once_per_state(self, tmp_path):
        # One run hashes the book to validate a saved plan, to stamp the one
        # it writes, and to build the resume fingerprint — three full reads
        # of a file that has not changed.
        from book_maker.loader.plan import file_sha256

        book = tmp_path / "b.epub"
        book.write_bytes(b"one")
        first = file_sha256(book)
        assert file_sha256(book) is first, "recomputed an unchanged file"

        book.write_bytes(b"two different bytes")
        assert file_sha256(book) != first, "served a stale hash"


class TestClassifyErrorState:
    def test_evidence_does_not_leak_between_failures(self):
        """`considered` and `verdicts` were class attributes. Nothing mutates
        them in place today — every site assigns — but a dict on the class is
        one `e.considered[k] = v` away from carrying one run's evidence into
        the next."""
        first = PlanClassifyError("first")
        first.considered["block:p"] = "prose"
        first.verdicts["block:p"] = {"verdict": "translate"}

        second = PlanClassifyError("second")
        assert second.considered == {} and second.verdicts == {}
        assert PlanClassifyError("third").considered == {}
        assert PlanClassifyFatal("fatal").verdicts == {}


class TestInlineDisposition:
    """`disposition` is the audit trail's claim about what happened. A row
    that says "translated" when nothing was translated is worse than no
    disposition at all."""

    @staticmethod
    def _plan(overrides=None):
        from book_maker.loader.plan import TranslationPlan

        soup = bs(
            '<body><p class="note">note <span class="ref">A1</span> tail</p>' "</body>",
            "html.parser",
        )
        fp = partition_soup(
            soup, DisplayResolver([]), "x.html", overrides=overrides or {}
        )
        return TranslationPlan([fp], (), 8)

    @staticmethod
    def _decide(plan, ledger, **actions):
        for key, action in actions.items():
            ledger.decide(key, action, "user", "prose")
        return plan.record_dispositions(ledger)

    def test_inline_inside_a_skipped_block_is_not_called_translated(self):
        plan = self._plan(overrides={"block:p.note": ("skip", "user")})
        ledger = plan.build_ledger()
        self._decide(
            plan, ledger, **{"block:p.note": "skip", "inline:span.ref": "translate"}
        )
        disposition = ledger.rows["inline:span.ref"]["disposition"]
        assert disposition.startswith("not translated")

    def test_inline_inside_a_translated_block_says_so(self):
        plan = self._plan()
        ledger = plan.build_ledger()
        self._decide(
            plan,
            ledger,
            **{"block:p.note": "translate", "inline:span.ref": "translate"},
        )
        assert (
            ledger.rows["inline:span.ref"]["disposition"] == "translated with its block"
        )

    def test_an_undecided_row_reports_no_disposition(self):
        # The agent handoff writes the plan *before* anyone has ruled. Saying
        # "translated: 563 chars" there described a run that cannot happen —
        # the run refuses to start while a row is null — and then sat in the
        # file contradicting the skip the agent recorded on that same row.
        ledger = self._plan().build_ledger()
        assert [r["action"] for r in ledger.rows.values()] == [None, None]
        assert [r["disposition"] for r in ledger.rows.values()] == [None, None]


class TestAgentPrompt:
    def test_prompt_scopes_itself_to_the_open_rows(self):
        from book_maker.loader.classify import build_agent_prompt

        prompt = build_agent_prompt(
            "/tmp/book_plan.json",
            "/tmp/book.epub",
            "python3 make_book.py --book_name /tmp/book.epub",
            unresolved=["block:p.x", "inline:span.line-no"],
        )
        assert "2 row(s) are still undecided" in prompt
        assert "block:p.x" in prompt and "inline:span.line-no" in prompt
        assert "/tmp/book_plan.json" in prompt
        # the inline scope changes what a skip *means*, so it must be explained
        assert "splits the sentence" in prompt

    def test_prompt_without_a_residue_list_asks_for_every_null(self):
        from book_maker.loader.classify import build_agent_prompt

        prompt = build_agent_prompt("/p.json", "/b.epub", "rerun")
        assert "still undecided" not in prompt
        assert "null" in prompt


class TestNumericSignatureSuffix:
    """A signature that names two kinds of content has no right answer.

    Live 260812 on childrens-literature.epub: `block:h3` was 4 real headings
    (BIBLIOGRAPHY) plus 15 bare page folios (`<h3>190</h3>`) — one row, one
    verdict. The model answered `translate`, which is the *correct* answer to
    a mixed row, and every folio got a translated sibling: the reader sees
    each page number twice, 15 of 15 in the offline replay.

    The fix is a finer key, not a shape filter: digit-only and roman-numeral
    runs get their own signature so the decider gets a row it can answer
    separately. Nothing here skips anything — verse numbers are content, and
    deciding that is still the classifier's job.
    """

    @staticmethod
    def _partition(html, overrides=None):
        soup = bs(html, "html.parser")
        return (
            partition_soup(
                soup, DisplayResolver([]), "x.html", overrides=overrides or {}
            ),
            soup,
        )

    def test_folios_and_headings_are_separate_rows(self):
        fp, _ = self._partition(
            "<body><h3>BIBLIOGRAPHY</h3><h3>190</h3><h3>191</h3></body>"
        )
        sigs = {u.text: u.signature for u in fp.units}
        assert sigs["BIBLIOGRAPHY"] == "h3"
        assert sigs["190"] == sigs["191"] != "h3"

    def test_the_mixed_row_can_now_be_answered_separately(self):
        # the whole point: skipping folios must not cost the real headings
        html = "<body><h3>BIBLIOGRAPHY</h3><h3>190</h3><h3>191</h3></body>"
        fp, _ = self._partition(html)
        folio_key = next(u.key for u in fp.units if u.text == "190")
        kept, _ = self._partition(html, overrides={folio_key: ("skip", "llm")})
        assert [u.text for u in kept.units] == ["BIBLIOGRAPHY"]

    def test_roman_numerals_are_deliberately_not_detected(self):
        # Measured over the corpus, a strict canonical roman matcher was
        # wrong 4 rows out of 5: an index's group letters (C, D, I, L, M) and
        # jlreq's character specimens (C, I, V, d, m) are alphabet labels that
        # spell numerals. Splitting them is a worse partition than the mixed
        # row. Roman folios stay a known limitation, on purpose.
        fp, _ = self._partition(
            "<body><h3>C</h3><h3>D</h3><h3>I</h3><h3>L</h3><h3>M</h3></body>"
        )
        assert {u.signature for u in fp.units} == {"h3"}

    def test_non_ascii_digit_forms_are_not_folios(self):
        # `str.isdigit()` is true of these; they are jlreq's subject matter,
        # not its page numbers
        fp, _ = self._partition("<body><p>\uff10</p><p>\u2460</p><p>\u2776</p></body>")
        assert {u.signature for u in fp.units} == {"p"}

    def test_text_that_merely_contains_a_number_is_untouched(self):
        fp, _ = self._partition(
            "<body><p>Chapter 3</p><p>1984 was a cold year.</p></body>"
        )
        assert {u.signature for u in fp.units} == {"p"}

    def test_decorated_folios_still_read_as_numeric(self):
        fp, _ = self._partition("<body><p>[190]</p><p>— 12 —</p></body>")
        assert all(u.signature != "p" for u in fp.units)
        assert len({u.signature for u in fp.units}) == 1

    def test_english_words_that_look_roman_stay_prose(self):
        # strict canonical roman only: DID/LID/CIVIC are words, not numerals
        fp, _ = self._partition("<body><p>DID</p><p>LID</p><p>CIVIC</p></body>")
        assert {u.signature for u in fp.units} == {"p"}

    def test_classes_survive_the_suffix(self):
        fp, _ = self._partition('<body><p class="folio b">190</p></body>')
        assert fp.units[0].signature.startswith("p.b.folio")
        assert fp.units[0].signature != "p.b.folio"

    def test_the_suffix_cannot_be_spelled_by_a_document(self):
        # linear-algebra.epub really does carry 1152 class names containing
        # `#`, so `#num` was a collision; a class token cannot hold whitespace
        fp, _ = self._partition(
            '<body><p class="x#num">Real prose here.</p><p>190</p></body>'
        )
        sigs = {u.text: u.signature for u in fp.units}
        assert sigs["Real prose here."] == "p.x#num"
        assert sigs["190"] != sigs["Real prose here."]
        assert " " in sigs["190"]

    def test_fragments_left_by_an_excluded_tag_are_not_folios(self):
        # epub30-spec.epub, verbatim: <code> is an excluded tag, so this one
        # sentence survives as three runs — "0:", ", 30:", ", 38:" — and each
        # fragment reads as a numeral while the sentence does not. A folio is
        # a whole element's text.
        fp, _ = self._partition(
            "<body><p>0: <code>PK</code>, 30: <code>mimetype</code>, "
            "38: <code>application/epub+zip</code></p></body>"
        )
        assert len(fp.units) == 3
        assert {u.signature for u in fp.units} == {"p"}

    def test_inline_parent_key_tracks_the_suffixed_row(self):
        # parents_of() drives the inline disposition; a parent_key naming a
        # row the ledger no longer has would report "not translated" on text
        # that was translated
        from book_maker.loader.plan import TranslationPlan

        fp, _ = self._partition('<body><p>190 <span class="ref">A1</span></p></body>')
        plan = TranslationPlan([fp], (), 8)
        ledger = plan.build_ledger()
        parent = fp.inline_rows[0]["parent_key"]
        assert parent in ledger.rows
        assert parent == fp.units[0].key
