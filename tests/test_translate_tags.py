"""Unit tests for enhanced --translate-tags CSS selector support and --scan mode."""

import pytest
from copy import copy
from bs4 import BeautifulSoup as bs
from bs4.element import NavigableString, Tag
from unittest.mock import MagicMock, patch


def make_loader():
    """Create a minimal EPUBBookLoader-like object for testing."""
    from book_maker.loader.epub_loader import EPUBBookLoader

    # We can't easily instantiate the full loader (needs epub file),
    # so we test the methods directly by creating a mock-like object
    loader = object.__new__(EPUBBookLoader)
    loader.translate_tags = "p"
    loader.exclude_translate_tags = "sup,code"
    loader.allow_navigable_strings = False
    return loader


class TestParseTranslateTags:
    def test_simple_tag(self):
        loader = make_loader()
        loader.translate_tags = "p"
        loader._parse_translate_tags()
        assert loader._css_selector == "p"

    def test_multiple_tags(self):
        loader = make_loader()
        loader.translate_tags = "p,blockquote"
        loader._parse_translate_tags()
        assert loader._css_selector == "p, blockquote"

    def test_css_selector_with_class(self):
        loader = make_loader()
        loader.translate_tags = "p,div.Para"
        loader._parse_translate_tags()
        assert loader._css_selector == "p, div.Para"

    def test_multiple_selectors_with_classes(self):
        loader = make_loader()
        loader.translate_tags = "p,div.Para,div.SimplePara"
        loader._parse_translate_tags()
        assert loader._css_selector == "p, div.Para, div.SimplePara"

    def test_whitespace_handling(self):
        loader = make_loader()
        loader.translate_tags = " p , div.Para "
        loader._parse_translate_tags()
        assert loader._css_selector == "p, div.Para"


class TestFindTranslatableParagraphs:
    def test_simple_p_only(self):
        """With default 'p' selector, only <p> tags are found."""
        loader = make_loader()
        loader.translate_tags = "p"
        loader._parse_translate_tags()

        html = "<div><p>Hello world</p><div class='Para'>Missed text</div></div>"
        soup = bs(html, "html.parser")
        result = loader._find_translatable_paragraphs(soup)

        texts = [p.get_text().strip() for p in result]
        assert "Hello world" in texts
        assert "Missed text" not in texts

    def test_div_para_type_a(self):
        """Type A: div.Para without nested <p> is found when selector includes div.Para."""
        loader = make_loader()
        loader.translate_tags = "p,div.Para"
        loader._parse_translate_tags()

        html = '<div><p>Normal paragraph</p><div class="Para">This is a semantic paragraph</div></div>'
        soup = bs(html, "html.parser")
        result = loader._find_translatable_paragraphs(soup)

        texts = [p.get_text().strip() for p in result]
        assert "Normal paragraph" in texts
        assert "This is a semantic paragraph" in texts

    def test_div_para_type_b(self):
        """Type B: div.Para with nested <p> - outer direct text extracted as new <p>."""
        loader = make_loader()
        loader.translate_tags = "p,div.Para"
        loader._parse_translate_tags()

        html = """
        <div class="Para" id="Par5">
          The definition is as follows:
          <blockquote>
            <p>A program is in SSA form if each variable is assigned exactly once.</p>
          </blockquote>
        </div>
        """
        soup = bs(html, "html.parser")
        result = loader._find_translatable_paragraphs(soup)

        texts = [p.get_text().strip() for p in result]
        # The inner <p> should be found
        assert any("A program is in SSA form" in t for t in texts)
        # The outer direct text should be extracted as a new <p>
        assert any("The definition is as follows:" in t for t in texts)

    def test_type_b_no_duplicate(self):
        """Type B: inner <p> text should NOT appear in the extracted outer text."""
        loader = make_loader()
        loader.translate_tags = "p,div.Para"
        loader._parse_translate_tags()

        html = """
        <div class="Para">
          Outer text here.
          <blockquote><p>Inner paragraph text.</p></blockquote>
        </div>
        """
        soup = bs(html, "html.parser")
        result = loader._find_translatable_paragraphs(soup)

        # Find the extracted node
        extracted = [p for p in result if p.get("class") == ["_bbm_extracted"]]
        assert len(extracted) == 1
        assert "Inner paragraph text" not in extracted[0].get_text()
        assert "Outer text here" in extracted[0].get_text()

    def test_backward_compat_no_extra(self):
        """When using just 'p', behavior is identical to original."""
        loader = make_loader()
        loader.translate_tags = "p"
        loader._parse_translate_tags()

        html = "<div><p>One</p><p>Two</p><div class='Para'>Three</div></div>"
        soup = bs(html, "html.parser")
        result = loader._find_translatable_paragraphs(soup)

        texts = [p.get_text().strip() for p in result]
        assert texts == ["One", "Two"]

    def test_dom_order(self):
        """Results should be in DOM order."""
        loader = make_loader()
        loader.translate_tags = "p,div.Para"
        loader._parse_translate_tags()

        html = '<div><div class="Para">First</div><p>Second</p><div class="Para">Third</div></div>'
        soup = bs(html, "html.parser")
        result = loader._find_translatable_paragraphs(soup)

        texts = [p.get_text().strip() for p in result]
        assert texts == ["First", "Second", "Third"]


class TestExtractParagraph:
    def test_div_removes_nested_divs(self):
        """For div-type paragraphs, nested div children are removed."""
        loader = make_loader()

        html = """
        <div class="Para">
          Some text here.
          <div class="Equation"><img alt="x = y + 1"/></div>
          More text.
        </div>
        """
        soup = bs(html, "html.parser")
        div_para = soup.find("div", class_="Para")
        result = loader._extract_paragraph(copy(div_para))

        text = result.get_text().strip()
        assert "Some text here" in text
        assert "More text" in text
        # Equation div should be removed
        assert result.find("div", class_="Equation") is None

    def test_p_tag_unchanged(self):
        """For <p> tags, no div removal happens."""
        loader = make_loader()

        html = "<p>Hello <em>world</em></p>"
        soup = bs(html, "html.parser")
        p = soup.find("p")
        result = loader._extract_paragraph(copy(p))

        assert result.get_text().strip() == "Hello world"


class TestFilterNestList:
    def test_basic_filter(self):
        """Paragraphs with nested translatable children are filtered out."""
        loader = make_loader()

        html = "<div><p>Outer <p>Inner</p></p><p>Simple</p></div>"
        soup = bs(html, "html.parser")
        p_list = soup.find_all("p")
        result = loader.filter_nest_list(p_list, "p")

        texts = [p.get_text().strip() for p in result]
        # Only "Inner" and "Simple" should remain (outer filtered)
        assert "Simple" in texts

    def test_extracted_p_not_filtered(self):
        """Extracted _bbm_extracted <p> nodes have no nesting, should not be filtered."""
        loader = make_loader()

        html = '<p class="_bbm_extracted">Extracted text</p><p>Normal</p>'
        soup = bs(html, "html.parser")
        p_list = soup.find_all("p")
        result = loader.filter_nest_list(p_list, "p")

        texts = [p.get_text().strip() for p in result]
        assert "Extracted text" in texts
        assert "Normal" in texts

    def test_css_selector_precision(self):
        """filter_nest_list with CSS selector 'p, div.Para' should NOT filter
        a div.Para that contains div.Equation (non-matching nested div)."""
        loader = make_loader()

        html = """
        <div class="Para">
          Some text.
          <div class="Equation">x = 1</div>
        </div>
        """
        soup = bs(html, "html.parser")
        div_para = soup.find_all("div", class_="Para")
        result = loader.filter_nest_list(div_para, "p, div.Para")

        # div.Equation does NOT match "p, div.Para", so div.Para should NOT be filtered
        assert len(result) == 1
        assert "Some text" in result[0].get_text()
