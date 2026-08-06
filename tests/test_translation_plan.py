"""Tests for the coverage-complete translation plan (partition, don't select).

Fixtures:
- test_books/animal_farm.epub  (committed) — poem lines are per-line
  <blockquote class="calibre_14|calibre_17">; default tag selection missed them.
- gilgamesh.epub (repo root, local only, 3.8MB) — 51% of text lives in
  div.poetry_line*; tests skip if the file is absent.
"""

import copy as copy_mod
import os
import shutil
import zipfile
from collections import Counter
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
    parse_css_display,
    partition_file,
    partition_soup,
    unit_clean_text,
)

REPO = Path(__file__).resolve().parent.parent
ANIMAL_FARM = REPO / "test_books" / "animal_farm.epub"
LIBER_ESTHER = REPO / "test_books" / "Liber_Esther.epub"
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

    def test_equal_specificity_resolves_by_source_order_not_attribute_order(self):
        # review finding: the resolver returned the first class token that
        # matched, so class="visible hidden" and class="hidden visible"
        # resolved differently from identical CSS. CSS decides equal
        # specificity by declaration order — .hidden is declared last here,
        # so both elements are hidden.
        css = ".visible { display: block } .hidden { display: none }"
        resolver = DisplayResolver([parse_css_display(css)])
        soup = bs(
            '<div><p class="visible hidden">a</p>'
            '<p class="hidden visible">b</p></div>',
            "html.parser",
        )
        displays = [resolver.display_of(p) for p in soup.find_all("p")]
        assert displays == ["none", "none"]

    def test_later_stylesheet_wins_over_earlier(self):
        first = parse_css_display(".x { display: none }")
        second = parse_css_display(".x { display: block }")
        resolver = DisplayResolver([first, second])
        soup = bs('<p class="x">a</p>', "html.parser")
        assert resolver.display_of(soup.find("p")) == "block"

    def test_tag_class_outranks_bare_class_regardless_of_order(self):
        # specificity still dominates: p.note (0,1,1) beats .plain (0,1,0)
        # even though .plain is declared later
        css = "p.note { display: none } .plain { display: block }"
        resolver = DisplayResolver([parse_css_display(css)])
        soup = bs('<p class="note plain">a</p>', "html.parser")
        assert resolver.display_of(soup.find("p")) == "none"


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


class TestShortUnitGrouping:
    """Tier 2: batch consecutive short units regardless of tag/signature.

    Greedy partitioning turns page numbers, verse refs and one-word labels
    into units of their own; tier 1 only groups structural siblings, so
    mixed junk would be one request each.
    """

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

    def test_mixed_short_run_is_windowed(self):
        # three different tags: tier 1 rejects the run (not siblings), tier 2
        # takes it — one request instead of three
        units = self._units(
            "<body><p class='pn'>42</p><div class='vn'>1.1.1</div>"
            f"<h3 class='lbl'>Ch.</h3><p>{self.LONG}</p></body>"
        )
        short, long_unit = units[:3], units[3]
        assert len({u.group_id for u in short}) == 1
        assert short[0].group_id is not None
        assert long_unit.group_id is None

    def test_isolated_short_unit_stays_solo(self):
        units = self._units(
            f"<body><p>{self.LONG}</p><div class='pn'>42</div><p>{self.LONG}</p></body>"
        )
        assert [u.group_id for u in units] == [None, None, None]

    def _mixed_rows(self, n, body=lambda i: str(i)):
        # alternating tag names so tier 1 never forms a run of its own
        return "".join(
            f"<{'p' if i % 2 else 'div'} class='v{i}'>{body(i)}"
            f"</{'p' if i % 2 else 'div'}>"
            for i in range(n)
        )

    def test_window_capped_by_line_count(self):
        units = self._units(f"<body>{self._mixed_rows(20)}</body>", group_size=8)
        sizes = Counter(u.group_id for u in units)
        assert None not in sizes
        assert sorted(sizes.values()) == [4, 8, 8]

    def test_window_capped_by_chars(self):
        # 60-char units with a generous line cap: characters must bite first
        rows = self._mixed_rows(9, body=lambda i: f"{'x' * 58} {i}")
        units = self._units(f"<body>{rows}</body>", group_size=50)
        sizes = Counter(u.group_id for u in units)
        assert max(sizes.values()) == 8  # 8 x 60 = 480 <= 500, 9th overflows
        for gid in sizes:
            chars = sum(u.chars for u in units if u.group_id == gid)
            assert chars <= 500

    def test_poetry_grouping_wins_over_the_sweep(self):
        # a real stanza must keep its tier-1 boundaries, not be re-windowed
        stanza = "".join(f"<div class='line'>verse line {i}</div>" for i in range(4))
        units = self._units(f"<body><div class='st'>{stanza}</div></body>")
        assert len({u.group_id for u in units}) == 1
        assert all(u.group_id is not None for u in units)
        assert all(u.poetry for u in units)

    def test_windows_are_not_marked_poetry(self):
        # the tiers share group_id but must stay distinguishable: verse is
        # exempt from classification, windowed apparatus is not
        units = self._units(f"<body>{self._mixed_rows(6)}</body>")
        assert all(u.group_id is not None for u in units)
        assert not any(u.poetry for u in units)


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
    loader.plan_mode = True
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
            loader.plan_mode = True
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

    def test_single_translate_drops_breaks_that_separate_nothing(self, tmp_path):
        # review finding: <br>-separated lines merge into one unit, so after
        # the later text nodes are extracted the breaks separate nothing and
        # rendered as blank lines under the translation. Tag mode (which
        # replaces the whole element) never had them.
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one<br/>two<br/>three</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "YI ER SAN", single_translate=True)
        assert soup.find("br") is None
        assert soup.find("p").get_text().strip() == "YI ER SAN"

    def test_single_translate_keeps_breaks_it_does_not_own(self, tmp_path):
        # a leading/trailing break is layout around the unit, not a
        # separator inside it — removing it would change the page
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p><br/>one</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "YI", single_translate=True)
        assert soup.find("br") is not None

    def test_bilingual_mode_leaves_the_original_breaks_alone(self, tmp_path):
        # the default path appends a translation paragraph and must not
        # touch the original's line structure
        loader, _ = _make_loader(tmp_path, FakeModel)
        soup = bs("<body><p>one<br/>two</p></body>", "html.parser")
        fp = partition_soup(soup, DisplayResolver([]), "x.html")
        loader._insert_plan_translation(fp.units[0], "YI ER", single_translate=False)
        assert len(soup.find_all("br")) == 1
        assert "one" in soup.get_text() and "YI ER" in soup.get_text()

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
        plan.save_json(str(path), book_path=str(ANIMAL_FARM))
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
        import pickle

        import book_maker.loader.epub_loader as loader_mod

        loader, src = _make_loader(tmp_path, FakeModel)
        with open(loader.bin_path, "wb") as f:
            pickle.dump(["translated p 1", "translated p 2"], f)

        resumed = loader_mod.EPUBBookLoader(
            str(src), FakeModel, "dummy-key", resume=True, language="zh-hans"
        )
        resumed.plan_mode = True
        resumed.translate_tags = "auto"
        with pytest.raises(SystemExit) as excinfo:
            resumed.make_bilingual_book()
        assert excinfo.value.code == 1

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

        loader, src = _make_loader(tmp_path, ExplodesOnBeasts)
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
        # translation onto unrelated text
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.only_filelist = "index_split_004.html"
        loader.make_bilingual_book()
        loader._save_temp_book()

        temp = src.parent / (src.stem + "_bilingual_temp.epub")
        with zipfile.ZipFile(temp) as z:
            early = next(
                n for n in z.namelist() if "index_split_002" in n and n.endswith("html")
            )
            selected = next(
                n for n in z.namelist() if "index_split_004" in n and n.endswith("html")
            )
            early_text = z.read(early).decode("utf-8", "ignore")
            selected_text = z.read(selected).decode("utf-8", "ignore")
        assert "T[" not in early_text, "unselected chapter consumed cache slots"
        assert "T[" in selected_text

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


from book_maker.loader.plan import FilePlan, TranslationPlan, Unit
from book_maker.loader.classify.candidates import gather_candidates
from book_maker.loader.classify.model import (
    PAGE_SIZE,
    VERDICTS,
    PlanClassifyError,
    build_prompt,
    build_schema,
    classify_plan,
    lint_verdicts,
    merge_verdicts,
)


def _cunit(sig, text, group_id=None, poetry=False):
    return Unit(
        element=None,
        file_name="x.html",
        signature=sig,
        text=text,
        chars=len(text),
        group_id=group_id,
        poetry=poetry,
    )


def _cplan(units=()):
    fp = FilePlan(file_name="x.html")
    fp.units = list(units)
    fp.total_chars = sum(u.chars for u in fp.units)
    return TranslationPlan([fp], ("sup", "code"), 8)


PROSE = _cunit("p", "He who saw the Deep, the country's foundation, " * 40)


class FakeClassifier:
    model = "fake-clf"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def structured_json(self, prompt, schema, model=None):
        self.calls.append({"prompt": prompt, "schema": schema, "model": model})
        return self.result


class TestClassifyCandidates:
    def test_prose_spine_is_never_a_candidate(self):
        plan = _cplan([PROSE])
        cands = gather_candidates(plan)
        assert cands == []

    def test_poetry_groups_are_never_candidates(self):
        verses = [
            _cunit("div.verse", f"short line {i}", group_id=0, poetry=True)
            for i in range(9)
        ]
        cands = gather_candidates(_cplan([PROSE, *verses]))
        assert cands == []

    def test_tier2_windowed_apparatus_is_still_a_candidate(self):
        # review finding: both grouping tiers share group_id, and the poetry
        # exemption used to key on it — any apparatus signature that landed
        # in a tier-2 window silently vanished from the classifier's
        # question list. Built through the real pipeline, not hand-set
        # group_ids, so the exemption is tested against what assign_groups
        # actually does.
        rows = "".join(
            f"<p class='pn'>4{i}</p><div class='vn'>1.{i}</div>" for i in range(10)
        )
        prose = "a fully formed prose sentence that anchors the spine " * 40
        soup = bs(f"<body><p class='txt'>{prose}</p>{rows}</body>", "html.parser")
        fp, _ = partition_file(soup, DisplayResolver([]), "x.html")
        short = [u for u in fp.units if u.signature in ("p.pn", "div.vn")]
        assert short and all(u.group_id is not None for u in short)
        plan = TranslationPlan([fp], ("sup", "code"), 8)
        cands = gather_candidates(plan)
        assert {c["signature"] for c in cands} == {"p.pn", "div.vn"}

    def test_running_head_shape_is_a_candidate(self):
        heads = [_cunit("p.header", "GILGAMESH") for _ in range(20)]
        cands = gather_candidates(_cplan([PROSE, *heads]))
        assert [c["signature"] for c in cands] == ["p.header"]
        assert "GILGAMESH" in cands[0]["samples"][0]

    def test_headings_are_never_candidates(self):
        # live finding: gpt-4o-mini demoted h2.chapter_title to skip — a wrong
        # verdict here silently loses every chapter title, so headings are
        # structurally certain and never sent
        titles = [_cunit("h2.chapter_title", "Appendix") for _ in range(8)]
        cands = gather_candidates(_cplan([PROSE, *titles]))
        assert cands == []

    def test_user_overridden_signature_is_not_asked_about(self):
        heads = [_cunit("p.header", "GILGAMESH") for _ in range(20)]
        cands = gather_candidates(
            _cplan([PROSE, *heads]), overrides={"p.header": "skip"}
        )
        assert cands == []

    def test_short_units_are_ordinary_candidates(self):
        # what the trivial filter used to eat is now a plain unit signature,
        # so it reaches the classifier through the one candidate path
        sigla = [_cunit("td.no", "No") for _ in range(4)]
        cands = gather_candidates(_cplan([PROSE, *sigla]))
        assert [c["signature"] for c in cands] == ["td.no"]

    def test_five_samples_per_signature(self):
        # more samples per signature is cheap insurance against a verdict
        # formed on an unrepresentative pair of lines
        heads = [_cunit("p.header", f"HEAD {i}") for i in range(20)]
        cands = gather_candidates(_cplan([PROSE, *heads]))
        assert len(cands[0]["samples"]) == 5

    def test_nothing_is_dropped_paging_replaces_the_cap(self):
        # the old cap silently left the smallest signatures unreviewed;
        # model mode pages through all of them instead
        units = [
            _cunit(f"p.h{i}", f"HEAD {i}")
            for i in range(PAGE_SIZE + 3)
            for _ in range(4)
        ]
        cands = gather_candidates(_cplan([PROSE, *units]))
        assert len(cands) == PAGE_SIZE + 3


class TestClassifyVerdicts:
    CANDS = [
        {
            "signature": "p.header",
            "units": 9,
            "chars": 81,
            "samples": ["GILGAMESH"],
        },
        {
            "signature": "td.no",
            "units": 4,
            "chars": 8,
            "samples": ["No"],
        },
    ]

    def test_schema_pins_one_enum_verdict_per_signature(self):
        schema = build_schema(self.CANDS)["schema"]
        assert set(schema["properties"]) == {"p.header", "td.no"}
        entry = schema["properties"]["p.header"]
        assert entry["properties"]["verdict"]["enum"] == VERDICTS
        assert entry["required"] == ["content_type", "verdict"]
        assert sorted(schema["required"]) == ["p.header", "td.no"]
        assert schema["additionalProperties"] is False

    def test_content_type_is_declared_before_verdict(self):
        # generation follows schema property order: the model must name the
        # content before ruling on it, not rationalize a verdict after
        entry = build_schema(self.CANDS)["schema"]["properties"]["p.header"]
        assert list(entry["properties"]) == ["content_type", "verdict"]

    def test_every_field_carries_a_description(self):
        entry = build_schema(self.CANDS)["schema"]["properties"]["p.header"]
        assert entry["description"]
        assert all(p["description"] for p in entry["properties"].values())

    def test_prompt_shows_labeled_samples_without_current_verdicts(self):
        prompt = build_prompt(self.CANDS)
        assert "Sample: GILGAMESH" in prompt
        assert "Sample: No" in prompt
        # the model judges cold: no anchoring on the heuristic decision
        assert "currently" not in prompt
        assert "keep it as is" in prompt

    def test_only_affirmative_skips_become_actions(self):
        # greedy plans translate every candidate already, so "translate" is
        # the status quo and only "skip" moves anything
        result = {
            "p.header": {"content_type": "running head", "verdict": "skip"},
            "td.no": {"content_type": "dialogue", "verdict": "translate"},
        }
        assert merge_verdicts(result, self.CANDS) == {"p.header": "llm-skip"}

    def test_unsure_and_status_quo_change_nothing(self):
        result = {
            "p.header": {"content_type": "?", "verdict": "unsure"},
            "td.no": {"content_type": "dialogue", "verdict": "translate"},
        }
        assert merge_verdicts(result, self.CANDS) == {}

    def test_malformed_entries_count_as_unsure(self):
        # shape-only endpoints may ignore value constraints or flatten shapes
        result = {
            "p.header": {"content_type": "x", "verdict": "banana"},
            "td.no": "translate",
        }
        assert merge_verdicts(result, self.CANDS) == {}


class TestClassifyPlan:
    def _uncertain_plan(self):
        heads = [_cunit("p.header", "GILGAMESH") for _ in range(20)]
        return _cplan([PROSE, *heads])

    def test_happy_path_returns_actions(self):
        clf = FakeClassifier(
            {"p.header": {"content_type": "running head", "verdict": "skip"}}
        )
        actions, cands = classify_plan(self._uncertain_plan(), clf, model="clf-x")
        assert actions == {"p.header": "llm-skip"}
        assert [c["signature"] for c in cands] == ["p.header"]
        call = clf.calls[0]
        # the schema is built per request: one pinned verdict per signature
        assert call["schema"]["schema"]["required"] == ["p.header"]
        assert call["model"] == "clf-x"
        assert "p.header" in call["prompt"] and "GILGAMESH" in call["prompt"]

    def test_no_candidates_never_touches_the_translator(self):
        actions, cands = classify_plan(_cplan([PROSE]), translator=None)
        assert actions == {} and cands == []

    def test_translator_without_structured_support_raises(self):
        with pytest.raises(PlanClassifyError, match="structured-output"):
            classify_plan(self._uncertain_plan(), object())

    def test_no_json_at_all_raises(self):
        # every rung of the ladder failed to produce an object
        with pytest.raises(PlanClassifyError, match="cannot produce JSON"):
            classify_plan(self._uncertain_plan(), FakeClassifier(None))

    def test_malformed_response_raises(self):
        with pytest.raises(PlanClassifyError, match="malformed"):
            classify_plan(self._uncertain_plan(), FakeClassifier(["not", "a", "dict"]))

    def test_request_error_raises(self):
        class Explodes:
            def structured_json(self, prompt, schema, model=None):
                raise RuntimeError("boom")

        with pytest.raises(PlanClassifyError, match="boom"):
            classify_plan(self._uncertain_plan(), Explodes())


class TestClassifyPartitionActions:
    TABLE = (
        "<body><table><tr><td class='no'>No</td></tr></table>"
        "<p>Real prose sentence, long enough to matter.</p></body>"
    )

    def _partition(self, html, overrides=None):
        soup = bs(html, "html.parser")
        return partition_soup(
            soup, DisplayResolver([]), file_name="x.html", overrides=overrides
        )

    def test_short_cells_are_planned_units(self):
        fp = self._partition(self.TABLE)
        assert "No" in [u.text for u in fp.units]
        assert "trivial" not in fp.skipped
        assert fp.total_chars == sum(u.chars for u in fp.units) + sum(
            fp.skipped.values()
        )

    def test_llm_skip_counts_llm_excluded(self):
        html = (
            "<body><p class='head'>GILGAMESH</p>"
            "<p>Real prose sentence, long enough to matter.</p></body>"
        )
        fp = self._partition(html, overrides={"p.head": "llm-skip"})
        assert fp.skipped["llm-excluded"] == len("GILGAMESH")
        assert fp.skipped["user-excluded"] == 0
        assert all(u.signature != "p.head" for u in fp.units)


class TestClassifyPlanArtifact:
    def test_llm_actions_round_trip_via_plan_json(self, tmp_path):
        import json

        from book_maker.loader.plan import load_plan_overrides

        heads = [_cunit("p.header", "GILGAMESH") for _ in range(3)]
        plan = _cplan([PROSE, *heads])
        path = tmp_path / "book_plan.json"
        plan.save_json(
            str(path), book_path=str(ANIMAL_FARM), llm_actions={"p.header": "llm-skip"}
        )

        data = json.loads(path.read_text())
        assert data["schema_version"] == 3
        by_sig = {r["signature"]: r for r in data["signatures"]}
        assert by_sig["p.header"]["action"] == "llm-skip"
        assert by_sig["p.header"]["decided_by"] == "llm"
        # user-facing default rows stay untouched
        assert by_sig["p"]["action"] == "translate"
        assert "decided_by" not in by_sig["p"]

        assert load_plan_overrides(str(path), str(ANIMAL_FARM)) == {
            "p.header": "llm-skip"
        }

    def test_verdict_for_an_unplanned_signature_fails_loud(self, tmp_path):
        # greedy plans have a row for every candidate, so a verdict about a
        # signature the plan lacks means the classifier answered the wrong
        # question — synthesizing a row would hide that
        plan = _cplan([PROSE])
        with pytest.raises(ValueError, match="absent from the plan"):
            plan.save_json(
                str(tmp_path / "book_plan.json"),
                book_path=str(ANIMAL_FARM),
                llm_actions={"td.gone": "llm-skip"},
            )

    def test_legacy_force_translate_loads_as_plain_translate(self, tmp_path, capsys):
        # schema<=2 JSONs (gilgamesh_plan.json) must keep loading: the action
        # is still valid to parse, it just no longer bypasses anything
        import json

        from book_maker.loader.plan import load_plan_overrides

        plan = _cplan([PROSE])
        path = tmp_path / "book_plan.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM))
        data = json.loads(path.read_text())
        data["signatures"][0]["action"] = "force-translate"
        path.write_text(json.dumps(data))

        assert load_plan_overrides(str(path), str(ANIMAL_FARM)) == {}
        assert "force-translate" in capsys.readouterr().out

    def test_unknown_action_in_plan_json_fails_loud(self, tmp_path):
        # a typo like "skiip" silently treated as translate would quietly
        # undo the user's decision
        import json

        from book_maker.loader.plan import load_plan_overrides

        plan = _cplan([PROSE])
        path = tmp_path / "book_plan.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM))
        data = json.loads(path.read_text())
        data["signatures"][0]["action"] = "skiip"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="invalid action.*skiip"):
            load_plan_overrides(str(path), str(ANIMAL_FARM))

    def test_pending_signatures_written_as_null(self, tmp_path):
        # candidates go out undecided: a null action is a question, not a
        # default, and the planner must answer it
        import json

        heads = [_cunit("p.header", "GILGAMESH") for _ in range(3)]
        plan = _cplan([PROSE, *heads])
        path = tmp_path / "book_plan.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM), pending=["p.header"])

        by_sig = {r["signature"]: r for r in json.loads(path.read_text())["signatures"]}
        assert by_sig["p.header"]["action"] is None
        assert by_sig["p"]["action"] == "translate"

    def test_pending_for_an_unplanned_signature_fails_loud(self, tmp_path):
        plan = _cplan([PROSE])
        with pytest.raises(ValueError, match="absent from the plan"):
            plan.save_json(
                str(tmp_path / "book_plan.json"),
                book_path=str(ANIMAL_FARM),
                pending=["td.gone"],
            )

    def test_undecided_null_refuses_to_load(self, tmp_path):
        # an unanswered question must stop the run, not silently translate
        from book_maker.loader.plan import load_plan_overrides

        heads = [_cunit("p.header", "GILGAMESH") for _ in range(3)]
        plan = _cplan([PROSE, *heads])
        path = tmp_path / "book_plan.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM), pending=["p.header"])

        with pytest.raises(ValueError, match="undecided.*p\\.header"):
            load_plan_overrides(str(path), str(ANIMAL_FARM))

    def test_resolved_null_loads_as_override(self, tmp_path):
        import json

        from book_maker.loader.plan import load_plan_overrides

        heads = [_cunit("p.header", "GILGAMESH") for _ in range(3)]
        plan = _cplan([PROSE, *heads])
        path = tmp_path / "book_plan.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM), pending=["p.header"])
        data = json.loads(path.read_text())
        for row in data["signatures"]:
            if row["signature"] == "p.header":
                row["action"] = "skip"
        path.write_text(json.dumps(data))

        assert load_plan_overrides(str(path), str(ANIMAL_FARM)) == {"p.header": "skip"}

    def test_row_missing_its_action_key_is_still_invalid(self, tmp_path):
        # only an explicit null means "undecided"; a vanished key means the
        # edit damaged the row
        import json

        from book_maker.loader.plan import load_plan_overrides

        plan = _cplan([PROSE])
        path = tmp_path / "book_plan.json"
        plan.save_json(str(path), book_path=str(ANIMAL_FARM))
        data = json.loads(path.read_text())
        del data["signatures"][0]["action"]
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="invalid action"):
            load_plan_overrides(str(path), str(ANIMAL_FARM))


class TestLoaderClassifyPolicy:
    """Failure policy: an explicitly chosen classifier model blocks, the
    default degrades to a printed notice."""

    def _loader(self, tmp_path):
        loader, _ = _make_loader(tmp_path, FakeModel)
        loader.plan_classify = "model"
        return loader

    def _uncertain_plan(self):
        heads = [_cunit("p.header", "GILGAMESH") for _ in range(20)]
        return _cplan([PROSE, *heads])

    def test_default_model_failure_falls_back_with_notice(self, tmp_path, capsys):
        loader = self._loader(tmp_path)  # FakeModel has no structured_json
        assert loader._classify_plan(self._uncertain_plan()) == {}
        assert "plan classification skipped" in capsys.readouterr().out

    def test_explicit_model_failure_blocks(self, tmp_path):
        loader = self._loader(tmp_path)
        loader.plan_classify_model = "clf-x"
        with pytest.raises(SystemExit) as excinfo:
            loader._classify_plan(self._uncertain_plan())
        assert excinfo.value.code == 1

    def test_none_mode_disables_classification(self, tmp_path):
        loader = self._loader(tmp_path)
        loader.plan_classify = "none"
        loader.translate_model = FakeClassifier(
            {"p.header": {"content_type": "running head", "verdict": "skip"}}
        )
        assert loader._classify_plan(self._uncertain_plan()) == {}
        assert loader.translate_model.calls == []

    def test_agent_mode_never_calls_the_model(self, tmp_path):
        loader = self._loader(tmp_path)
        loader.plan_classify = "agent"
        loader.translate_model = FakeClassifier(
            {"p.header": {"content_type": "running head", "verdict": "skip"}}
        )
        assert loader._classify_plan(self._uncertain_plan()) == {}
        assert loader.translate_model.calls == []

    def test_verdicts_are_returned_and_summarized(self, tmp_path, capsys):
        loader = self._loader(tmp_path)
        loader.translate_model = FakeClassifier(
            {"p.header": {"content_type": "running head", "verdict": "skip"}}
        )
        assert loader._classify_plan(self._uncertain_plan()) == {"p.header": "llm-skip"}
        assert "p.header" in capsys.readouterr().out


class TestClassifyLint:
    """Below strict decoding nothing about the response is guaranteed, so
    every field is untrusted and 'unsure' is the safe reading."""

    CANDS = [
        {"signature": "p.header", "units": 9, "chars": 81, "samples": ["GILGAMESH"]},
        {"signature": "td.no", "units": 4, "chars": 8, "samples": ["No"]},
    ]

    def test_missing_signature_is_unsure(self):
        result = {"p.header": {"verdict": "skip"}}
        assert lint_verdicts(result, self.CANDS) == {
            "p.header": "skip",
            "td.no": "unsure",
        }

    def test_out_of_enum_verdict_is_unsure(self):
        result = {
            "p.header": {"verdict": "banana"},
            "td.no": {"verdict": "SKIP"},  # case counts: enum is exact
        }
        assert set(lint_verdicts(result, self.CANDS).values()) == {"unsure"}

    def test_non_dict_entry_is_unsure(self):
        result = {"p.header": "skip", "td.no": None}
        assert set(lint_verdicts(result, self.CANDS).values()) == {"unsure"}

    def test_extra_signatures_are_ignored(self):
        # a hallucinated key must not become an action for a signature the
        # plan never asked about — to_dict would reject it downstream
        result = {
            "p.header": {"verdict": "skip"},
            "td.no": {"verdict": "translate"},
            "div.invented": {"verdict": "skip"},
        }
        assert set(lint_verdicts(result, self.CANDS)) == {"p.header", "td.no"}

    def test_non_dict_response_fails_loud(self):
        with pytest.raises(PlanClassifyError, match="malformed"):
            lint_verdicts(["not", "a", "dict"], self.CANDS)


class TestClassifyPaging:
    def test_candidates_are_paged_and_merged(self):
        units = [_cunit(f"p.h{i}", f"HEAD {i}") for i in range(25) for _ in range(4)]
        plan = _cplan([PROSE, *units])

        class Pager:
            model = "fake"

            def __init__(self):
                self.pages = []

            def structured_json(self, prompt, schema, model=None):
                sigs = schema["schema"]["required"]
                self.pages.append(sigs)
                return {s: {"verdict": "skip"} for s in sigs}

        clf = Pager()
        actions, cands = classify_plan(plan, clf)

        assert len(cands) == 25
        assert [len(p) for p in clf.pages] == [12, 12, 1]
        assert len(actions) == 25
        # every signature appears exactly once across the pages
        flat = [s for page in clf.pages for s in page]
        assert len(set(flat)) == 25

    def test_one_failing_page_fails_the_whole_run(self):
        # a half-classified plan is indistinguishable from a complete one in
        # the JSON, so it must never be written
        units = [_cunit(f"p.h{i}", f"HEAD {i}") for i in range(20) for _ in range(4)]

        class SecondPageExplodes:
            model = "fake"
            calls = 0

            def structured_json(self, prompt, schema, model=None):
                type(self).calls += 1
                if self.calls > 1:
                    raise RuntimeError("boom")
                return {s: {"verdict": "skip"} for s in schema["schema"]["required"]}

        with pytest.raises(PlanClassifyError, match="boom"):
            classify_plan(_cplan([PROSE, *units]), SecondPageExplodes())


class TestAgentPrompt:
    def _prompt(self):
        from book_maker.loader.classify import build_agent_prompt

        return build_agent_prompt(
            "book_plan.json", "book.epub", "python3 make_book.py --book_name book.epub"
        )

    def test_prompt_is_self_contained(self):
        text = self._prompt()
        # a session with no skill installed must learn everything from this
        for needed in [
            "book_plan.json",
            "book.epub",
            "python3 make_book.py --book_name book.epub",
            "samples",
            "book_sha256",
            "unzip -p",
        ]:
            assert needed in text, needed

    def test_prompt_states_the_asymmetry_of_mistakes(self):
        # wrapped prose, so compare on collapsed whitespace
        text = " ".join(self._prompt().split())
        assert "losing content is not" in text

    def test_prompt_demands_a_decision_for_every_null(self):
        text = " ".join(self._prompt().split())
        assert "null" in text
        assert "refuse" in text
        # the same discipline the model schema enforces: name the content
        # before ruling on it
        assert "name what the text is" in text


class TestAgentModeFlow:
    """agent mode writes the plan, prints instructions, and stops. Stopping
    is the feature: translating first would spend the book before anyone
    looked at the questions."""

    def test_first_run_writes_plan_prints_prompt_and_does_not_translate(
        self, tmp_path, capsys
    ):
        import json

        loader, src = _make_loader(tmp_path, FakeModel)
        loader.plan_classify = "agent"

        with pytest.raises(SystemExit) as excinfo:
            loader.make_bilingual_book()
        assert excinfo.value.code == 0

        plan_path = src.parent / (src.stem + "_plan.json")
        assert plan_path.exists()
        out = capsys.readouterr().out
        assert "Paste the block below" in out
        assert str(plan_path) in out
        # nothing was translated, and no book was produced
        assert loader.translate_model.list_calls == []
        assert not (src.parent / (src.stem + "_bilingual.epub")).exists()

        rows = json.loads(plan_path.read_text())["signatures"]
        assert all("samples" in r for r in rows)
        assert any(len(r["samples"]) > 1 for r in rows)
        # the uncertain signatures arrive as open questions, not defaults:
        # a lazy rerun that answers none of them must not translate
        nulls = {r["signature"] for r in rows if r["action"] is None}
        assert nulls == {"blockquote.calibre_7", "p.calibre_15"}

    def test_rerun_refuses_while_nulls_remain(self, tmp_path):
        loader, src = _make_loader(tmp_path, FakeModel)
        loader.plan_classify = "agent"
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

        loader2, _ = _make_loader(tmp_path, FakeModel)
        loader2.plan_classify = "agent"
        with pytest.raises(ValueError, match="undecided"):
            loader2.make_bilingual_book()
        assert loader2.translate_model.list_calls == []

    def test_second_run_translates_using_the_edited_plan(self, tmp_path):
        import json

        loader, src = _make_loader(tmp_path, FakeModel)
        loader.plan_classify = "agent"
        loader.only_filelist = "index_split_004.html"
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

        plan_path = src.parent / (src.stem + "_plan.json")
        data = json.loads(plan_path.read_text())
        for sig in data["signatures"]:
            if sig["signature"] == "blockquote.calibre_17":
                sig["action"] = "skip"
            elif sig["action"] is None:
                # answer the plan's open question (blockquote.calibre_7)
                sig["action"] = "translate"
        plan_path.write_text(json.dumps(data))

        # same command again: the plan is on disk, so it translates
        loader2, _ = _make_loader(tmp_path, FakeModel)
        loader2.plan_classify = "agent"
        loader2.only_filelist = "index_split_004.html"
        loader2.make_bilingual_book()

        assert (src.parent / (src.stem + "_bilingual.epub")).exists()
        sent = [t for call in loader2.translate_model.list_calls for t in call]
        assert sent, "second run must translate"
        assert not any("Beasts of every land and clime" in t for t in sent)

    def test_samples_are_distinct_and_clipped(self, tmp_path):
        import json

        loader, src = _make_loader(tmp_path, FakeModel)
        loader.plan_classify = "agent"
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()

        rows = json.loads((src.parent / (src.stem + "_plan.json")).read_text())[
            "signatures"
        ]
        for row in rows:
            assert len(row["samples"]) == len(set(row["samples"]))
            assert len(row["samples"]) <= 5
            assert all(len(s) <= 81 for s in row["samples"])  # 80 + ellipsis
