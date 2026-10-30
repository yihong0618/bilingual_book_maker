"""Bilingual cell text must not create additional table columns."""

import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest
from bs4 import BeautifulSoup as bs

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import EPUBBookLoaderHelper
from book_maker.loader.plan import DisplayResolver, partition_soup


def _loader():
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.language_tag = "zh-hans"
    loader.exclude_translate_tags = "sup,code"
    loader.translate_model = type("Model", (), {"TRANSLATION_ERROR_MARKER": None})()
    loader.helper = EPUBBookLoaderHelper(None, None, "", False, language="zh-hans")
    return loader


def _grid(soup):
    return [
        [
            (cell.name, cell.get("colspan", "1"), cell.get("rowspan", "1"))
            for cell in row.find_all(["th", "td"], recursive=False)
        ]
        for row in soup.find_all("tr")
    ]


class OfflineTranslator:
    TRANSLATION_ERROR_MARKER = None

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"译文：{text}"

    def translate_list(self, texts):
        return [self.translate(text) for text in texts]


@pytest.mark.parametrize("plan_mode", [False, True])
def test_epub_fixture_keeps_table_geometry_and_header_references(tmp_path, plan_mode):
    fixture = (
        Path(__file__).resolve().parents[1]
        / "test_books/table_columns/table-columns.epub"
    )
    source = tmp_path / "tables.epub"
    shutil.copyfile(fixture, source)
    loader = EPUBBookLoader(
        str(source),
        OfflineTranslator,
        key="",
        resume=False,
        language="zh-hans",
        disclose=False,
    )
    loader.plan_mode = plan_mode
    loader.plan_classify = "all"
    loader.translate_tags = "auto" if plan_mode else "th,td"
    loader.only_filelist = "content.xhtml"
    loader.quiet = True
    loader.make_bilingual_book()

    def content(path):
        with ZipFile(path) as archive:
            return bs(archive.read("EPUB/content.xhtml"), "xml")

    before = content(source)
    after = content(tmp_path / "tables_bilingual.epub")
    assert _grid(after) == _grid(before)
    source_cells = before.find_all(["th", "td"])
    translated_cells = after.find_all(["th", "td"])
    for original, translated in zip(source_cells, translated_cells):
        assert translated.attrs == original.attrs
        assert original.get_text(strip=True) in translated.get_text(strip=True)
        assert "译文：" in translated.get_text()
    ids = [element["id"] for element in after.find_all(id=True)]
    assert len(ids) == len(set(ids))
    for cell in translated_cells:
        for header in cell.get("headers", "").split():
            assert after.find("th", id=header) is not None


@pytest.mark.parametrize("route", ["helper", "preserving", "plan"])
@pytest.mark.parametrize("cell_name", ["th", "td"])
@pytest.mark.parametrize("single", [False, True])
def test_translating_a_cell_preserves_columns_and_spans(route, cell_name, single):
    soup = bs(
        f'<table><tr><{cell_name} id="metric" colspan="2" rowspan="2" '
        'lang="en" xml:lang="en"><span id="label">Metric</span>'
        f"</{cell_name}><td>Alpha</td></tr><tr><td>Beta</td></tr></table>",
        "html.parser",
    )
    before = _grid(soup)
    loader = _loader()
    cell = soup.find(id="metric")
    style = "color: gray"
    if route == "helper":
        loader.helper.insert_trans(cell, "指标", style, single)
    elif route == "preserving":
        loader._insert_trans_preserving_tags(cell, "指标", style, single)
    else:
        plan = partition_soup(soup, DisplayResolver([]), "table.xhtml")
        unit = next(u for u in plan.units if u.text == "Metric")
        loader._insert_plan_translation(unit, "指标", style, single)

    assert _grid(soup) == before
    assert len(soup.find_all(id="metric")) == 1
    cell = soup.find(id="metric")
    assert "指标" in cell.get_text()
    if single:
        assert "Metric" not in cell.get_text()
    else:
        assert soup.find(id="label").get_text() == "Metric"
        translated = cell.find("span", style=style, recursive=False)
        assert translated is not None
        assert translated.get_text() == "指标"
        assert translated.get("lang") == "zh-hans"
        assert translated.get("xml:lang") == "zh-hans"
        assert not translated.find_all(id=True)
        assert cell.find("br", recursive=False) is not None


@pytest.mark.parametrize("cell_name", ["th", "td"])
def test_plan_cell_with_nested_blocks_keeps_each_translation_in_order(cell_name):
    soup = bs(
        f"<table><tr><{cell_name}>Before<p>Middle</p>After</{cell_name}>"
        "<td>Other</td></tr></table>",
        "html.parser",
    )
    before = _grid(soup)
    loader = _loader()
    plan = partition_soup(soup, DisplayResolver([]), "table.xhtml")
    for unit in plan.units:
        loader._insert_plan_translation(unit, f"译:{unit.text}")
    assert _grid(soup) == before
    assert list(soup.find(cell_name).stripped_strings) == [
        "Before",
        "译:Before",
        "Middle",
        "译:Middle",
        "After",
        "译:After",
    ]


@pytest.mark.parametrize("cell_name", ["th", "td"])
def test_plan_cell_retains_inline_code_and_unique_ids(cell_name):
    soup = bs(
        f'<table><tr><{cell_name}>Run <code id="cmd">ls</code> now'
        f"</{cell_name}><td>Other</td></tr></table>",
        "html.parser",
    )
    loader = _loader()
    plan = partition_soup(
        soup, DisplayResolver([]), "table.xhtml", exclude_tags=("code",)
    )
    unit = next(u for u in plan.units if u.markers)
    token = next(iter(unit.markers))
    before = _grid(soup)
    loader._insert_plan_translation(unit, f"现在运行 {token}")
    assert _grid(soup) == before
    cell = soup.find(cell_name)
    assert [c.get_text() for c in cell.find_all("code")] == ["ls", "ls"]
    assert len(soup.find_all(id="cmd")) == 1
    assert "⟦" not in soup.get_text()


@pytest.mark.parametrize("cell_name", ["th", "td"])
def test_tag_cell_with_protected_code_stays_in_the_same_column(cell_name):
    soup = bs(
        f'<table><tr><{cell_name}>Run <code id="cmd">ls</code>'
        f"</{cell_name}><td>Other</td></tr></table>",
        "html.parser",
    )
    before = _grid(soup)
    _loader()._insert_trans_preserving_tags(soup.find(cell_name), "运行命令")
    assert _grid(soup) == before
    assert soup.find(id="cmd").get_text() == "ls"
    assert "运行命令" in soup.find(cell_name).get_text()
