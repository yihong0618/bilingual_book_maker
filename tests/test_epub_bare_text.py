"""Bare-text translation preserves EPUB markup across execution paths."""

from copy import copy
import zipfile
import pytest
from bs4 import BeautifulSoup
from ebooklib import epub
from lxml import etree
from book_maker.loader.helper import EPUBBookLoaderHelper
from test_epub_loader_correctness import (
    _make_loader,
    _replace_epub_member,
    _write_epub_with_custom_nav,
)


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_bare_text_translation_handles_fresh_and_resumed_content(
    tmp_path, monkeypatch, single, resume
):
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["Source"])])
    loader.single_translate = single
    loader.resume = resume
    loader.translation_style = "color: gray"
    soup = BeautifulSoup('<body lang="en">Loose source text</body>', "html.parser")
    source = soup.body.string
    if resume:
        loader.p_to_save = ["缓存译文"]

    index = loader._process_paragraph(
        source, copy(source), 0, p_to_save_len=int(resume)
    )

    assert index == 1
    translation = soup.body.find("span", recursive=False)
    assert translation is not None
    assert translation.get_text() == (
        "缓存译文" if resume else "<T>Loose source text</T>"
    )
    assert translation["style"] == "color: gray"
    assert translation["lang"] == "zh-hans"
    assert (source.parent is None) == single
    assert len(loader.translate_model.calls) == (0 if resume else 1)


@pytest.mark.parametrize("resume", [False, True])
def test_grouped_bare_text_uses_the_same_insertion_rules(tmp_path, monkeypatch, resume):
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["Source"])])
    loader.allow_navigable_strings = True
    loader.accumulated_num = 100
    item = loader.origin_book.get_item_with_href("one.xhtml")
    item.content = b'<html><body lang="en">First source<br/>Second source</body></html>'
    plan = loader._build_translation_plan([item], ["p"])[0]
    loader._planned_job_ids = [job.job_id for job in plan.jobs]
    loader.translation_style = "color: gray"
    loader.resume = resume
    if resume:
        loader.p_to_save = ["缓存译文一", "缓存译文二"]

    index, processed = loader._process_combined_paragraph(
        [job.node for job in plan.jobs], 0, 2 if resume else 0
    )

    assert (index, processed) == (2, 2)
    translations = plan.soup.body.find_all("span", recursive=False)
    assert [translation.get_text() for translation in translations] == (
        ["缓存译文一", "缓存译文二"]
        if resume
        else ["<T>First source</T>", "<T>Second source</T>"]
    )
    assert all(translation["style"] == "color: gray" for translation in translations)
    assert len(loader.translate_model.list_calls) == (0 if resume else 1)


@pytest.mark.parametrize("single", [False, True])
def test_bare_navigation_text_stays_inside_its_link(single):
    soup = BeautifulSoup(
        '<nav><ol><li><a href="chapter.xhtml" lang="en">Chapter</a></li></ol></nav>',
        "html.parser",
    )
    helper = EPUBBookLoaderHelper(None, 1, "", False, language="zh-hans")

    translation = helper.insert_trans(soup.a.string, "第一章", single_translate=single)

    assert translation.parent is soup.a
    assert len(soup.find_all("a")) == 1
    assert soup.a["href"] == "chapter.xhtml"
    assert soup.a.get_text() == ("第一章" if single else "Chapter第一章")
    assert translation["lang"] == "zh-hans"


def _navigation_text_loader(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    _write_epub_with_custom_nav(source)
    _replace_epub_member(
        source,
        "EPUB/toc.xhtml",
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops"><head><title/></head>'
        '<body><nav epub:type="toc"><ol><li>'
        '<a href="chapter.xhtml" lang="en">Chapter</a>'
        "</li></ol></nav></body></html>",
    )
    loader, _ = _make_loader(tmp_path, monkeypatch, [], parallel_workers=2)
    loader.allow_navigable_strings = True
    loader.exclude_filelist = "chapter.xhtml"
    loader.translation_style = "color: gray"
    loader.disclose = False
    nav = loader.origin_book.get_item_with_href("toc.xhtml")
    plan = loader._build_translation_plan([nav], ["p"])[0]
    assert len(plan.jobs) == 1
    return loader, plan


@pytest.mark.parametrize("resume", [False, True])
def test_parallel_worker_handles_bare_navigation_text(tmp_path, monkeypatch, resume):
    loader, plan = _navigation_text_loader(tmp_path, monkeypatch)
    loader.resume = resume
    if resume:
        loader.p_to_save = ["缓存译文"]

    result = loader._process_chapter_parallel((plan, int(resume)))

    assert result["success"], result["error"]
    soup = BeautifulSoup(result["processed_content"], "xml")
    assert soup.a.span.get_text() == ("缓存译文" if resume else "<T>Chapter</T>")
    assert soup.a.span["style"] == "color: gray"
    assert soup.a.span["lang"] == "zh-hans"
    assert len(loader.translate_model.calls) == (0 if resume else 1)


def test_recovery_book_replays_bare_navigation_text(tmp_path, monkeypatch):
    loader, _ = _navigation_text_loader(tmp_path, monkeypatch)
    loader.p_to_save = ["缓存译文"]

    loader._save_temp_book()

    with zipfile.ZipFile(tmp_path / "book_bilingual_temp.epub") as archive:
        soup = BeautifulSoup(archive.read("EPUB/toc.xhtml"), "xml")
    assert soup.a.span.get_text() == "缓存译文"
    assert soup.a.span["style"] == "color: gray"
    assert soup.a.span["lang"] == "zh-hans"
    assert loader.translate_model.calls == []


@pytest.mark.parametrize(
    "parent_tag", ["title", "h:title", "textarea", "option", "script", "style", "rp"]
)
@pytest.mark.parametrize("single", [False, True])
def test_bare_text_in_text_only_elements_does_not_gain_markup(parent_tag, single):
    soup = BeautifulSoup(
        f'<{parent_tag} xmlns:h="http://www.w3.org/1999/xhtml">Source</{parent_tag}>',
        "html.parser",
    )
    parent = soup.find(parent_tag)
    helper = EPUBBookLoaderHelper(None, 1, "", False, language="zh-hans")

    helper.insert_trans(parent.string, "Translation", "color: gray", single)

    assert parent.find(True) is None
    assert parent.decode_contents() == (
        "Translation" if single else "SourceTranslation"
    )


@pytest.mark.parametrize("recovery", [False, True])
def test_worker_and_recovery_keep_document_titles_text_only(
    tmp_path, monkeypatch, recovery
):
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["Source"])])
    loader.allow_navigable_strings = True
    loader.disclose = False
    item = loader.origin_book.get_item_with_href("one.xhtml")
    # The upstream writer patch writes raw item.content, so make this
    # fixture's head explicit rather than relying on EbookLib to add it.
    content = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter 1</title>'
        "</head><body><p>Source</p></body></html>"
    )
    _replace_epub_member(tmp_path / "book.epub", "EPUB/one.xhtml", content)
    item.content = content.encode()
    plan = loader._build_translation_plan([item], ["p"])[0]

    if recovery:
        loader.p_to_save = ["Translation" for job in plan.jobs]
        loader._save_temp_book()
        with zipfile.ZipFile(tmp_path / "book_bilingual_temp.epub") as archive:
            content = archive.read("EPUB/one.xhtml")
    else:
        result = loader._process_chapter_parallel((plan, 0))
        assert result["success"], result["error"]
        content = result["processed_content"]
    title = BeautifulSoup(content, "xml").title
    assert title.find(True) is None
    assert "Chapter 1" in title.get_text()


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize(
    "markup,label_name",
    [
        ('<svg xmlns="http://www.w3.org/2000/svg"><text>Source</text></svg>', "text"),
        (
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mtext>Source</mtext></math>',
            "mtext",
        ),
        (
            '<s:svg xmlns:s="http://www.w3.org/2000/svg"><s:text>Source</s:text></s:svg>',
            "text",
        ),
    ],
)
def test_worker_preserves_character_data_in_embedded_xml(
    tmp_path, monkeypatch, markup, label_name, single
):
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["Source"])])
    loader.allow_navigable_strings = True
    loader.single_translate = single
    item = loader.origin_book.get_item_with_href("one.xhtml")
    item.content = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title/></head>'
        f"<body>{markup}</body></html>"
    ).encode()
    plan = loader._build_translation_plan([item], ["p"])[0]
    assert len(plan.jobs) == 1

    result = loader._process_chapter_parallel((plan, 0))

    assert result["success"], result["error"]
    label = BeautifulSoup(result["processed_content"], "xml").find(label_name)
    assert label.find(True) is None
    assert label.get_text() == ("<T>Source</T>" if single else "Source<T>Source</T>")


@pytest.mark.parametrize("single", [False, True])
@pytest.mark.parametrize(
    "markup",
    [
        '<h:html xmlns:h="http://www.w3.org/1999/xhtml"><h:head><h:title/></h:head>'
        "<h:body><h:p>Source</h:p></h:body></h:html>",
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><title/></head><body>'
        '<x:p xmlns:x="http://www.w3.org/1999/xhtml" xmlns="">Source</x:p></body></html>',
    ],
)
def test_worker_bare_text_wrapper_keeps_the_xhtml_namespace(
    tmp_path, monkeypatch, markup, single
):
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["Source"])])
    loader.allow_navigable_strings = True
    loader.single_translate = single
    item = loader.origin_book.get_item_with_href("one.xhtml")
    item.content = markup.encode()
    plan = loader._build_translation_plan([item], ["p"])[0]
    assert len(plan.jobs) == 1

    result = loader._process_chapter_parallel((plan, 0))

    assert result["success"], result["error"]
    root = etree.fromstring(result["processed_content"])
    namespace = "http://www.w3.org/1999/xhtml"
    paragraph = root.find(f".//{{{namespace}}}p")
    translation = paragraph.find(f"{{{namespace}}}span")
    assert translation is not None
    assert translation.text == "<T>Source</T>"
    assert all(etree.QName(element).namespace == namespace for element in root.iter())
