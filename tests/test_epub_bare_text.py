"""Bare-text translation preserves EPUB markup across execution paths."""

from collections import Counter
from copy import copy
from pathlib import Path
import shutil
import zipfile
import pytest
from bs4 import BeautifulSoup
from ebooklib import epub
from lxml import etree
from book_maker.loader.helper import EPUBBookLoaderHelper
from test_epub_loader_correctness import (
    RecordingModel,
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


# A chapter shaped like a real XHTML file: every one starts with an XML
# declaration, and `find_all(string=True)` returns it, the DOCTYPE, the
# comment and the <style>/<script> code as NavigableString subclasses.
REAL_CHAPTER = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    "<!DOCTYPE html>\n"
    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Doc title</title>'
    "<!-- source comment --><style>p { color: red; }</style>"
    "<script>var x = 1;</script></head>"
    "<body>Loose body text<p>Paragraph <b>bold</b> text</p>"
    "<div>Loose div text<code>raw code</code></div>"
    "<ruby>漢<rt>kan</rt></ruby></body></html>"
)
# Under html.parser a prefixed document's <h:style> holds a plain string.
PREFIXED_CHAPTER = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<h:html xmlns:h="http://www.w3.org/1999/xhtml"><h:head><h:title/>'
    "<h:style>h|p { color: red; }</h:style><h:script>var y = 2;</h:script>"
    "</h:head><h:body>Prefixed loose text</h:body></h:html>"
)
REAL_CHAPTER_TEXTS = [
    "Doc title",
    "Loose body text",
    "Paragraph bold text",
    "Loose div text",
    "漢",
    "Prefixed loose text",
]
XML_DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'
UNTOUCHED = {
    "one.xhtml": [
        XML_DECLARATION,
        "<!DOCTYPE html>",
        "<!-- source comment -->",
        "<style>p { color: red; }</style>",
        "<script>var x = 1;</script>",
        "<code>raw code</code>",
        "<rt>kan</rt>",
    ],
    "two.xhtml": [
        XML_DECLARATION,
        "<h:style>h|p { color: red; }</h:style>",
        "<h:script>var y = 2;</h:script>",
    ],
}


def _real_chapter_loader(tmp_path, monkeypatch, **kwargs):
    chapters = [("one.xhtml", ["Source"]), ("two.xhtml", ["Source"])]
    loader, output = _make_loader(tmp_path, monkeypatch, chapters, **kwargs)
    source = tmp_path / "book.epub"
    _replace_epub_member(source, "EPUB/one.xhtml", REAL_CHAPTER)
    _replace_epub_member(source, "EPUB/two.xhtml", PREFIXED_CHAPTER)
    # the loader read the book before the members were replaced
    loader, output = _make_loader(tmp_path, monkeypatch, chapters, **kwargs)
    loader.allow_navigable_strings = True
    loader.disclose = False
    return loader, output


def _assert_real_chapters_translated(output, calls):
    assert sorted(calls) == sorted(REAL_CHAPTER_TEXTS)
    sent = "\n".join(calls)
    for fragment in ("xml version", "DOCTYPE", "comment", "color", "var ", "kan"):
        assert fragment not in sent
    with zipfile.ZipFile(output) as archive:
        documents = {
            name: archive.read(f"EPUB/{name}").decode("utf-8") for name in UNTOUCHED
        }
    for name, fragments in UNTOUCHED.items():
        for fragment in fragments:
            assert documents[name].count(fragment) == 1, (name, fragment)
    translated = "".join(documents.values())
    for text in REAL_CHAPTER_TEXTS:
        assert translated.count(f"&lt;T&gt;{text}&lt;/T&gt;") == 1, text


@pytest.mark.parametrize("workers", [1, 2])
def test_real_chapters_translate_only_their_bare_text(tmp_path, monkeypatch, workers):
    loader, output = _real_chapter_loader(
        tmp_path, monkeypatch, parallel_workers=workers
    )

    loader.make_bilingual_book()

    _assert_real_chapters_translated(output, loader.translate_model.calls)


class InterruptAfterBareTextModel(RecordingModel):
    def translate(self, text):
        if len(self.calls) == 2:
            raise KeyboardInterrupt
        return super().translate(text)


def test_real_chapters_resume_without_resending_bare_text(tmp_path, monkeypatch):
    loader, _ = _real_chapter_loader(
        tmp_path, monkeypatch, model=InterruptAfterBareTextModel
    )
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()
    first = list(loader.translate_model.calls)
    # the checkpoint holds the paragraph and one bare string
    assert first == ["Paragraph bold text", "Doc title"]

    resumed, output = _real_chapter_loader(tmp_path, monkeypatch, resume=True)
    resumed.make_bilingual_book()

    _assert_real_chapters_translated(output, first + resumed.translate_model.calls)


def test_bare_text_collector_skips_claimed_excluded_and_code_strings(
    tmp_path, monkeypatch
):
    # retranslate's collector has no plan to test it through; it calls the
    # same helper as the plan.
    loader, _ = _make_loader(tmp_path, monkeypatch, [("one.xhtml", ["Source"])])

    def bare_strings(markup, exclude="sup,code"):
        loader.exclude_translate_tags = exclude
        soup = BeautifulSoup(markup, "html.parser")
        nodes = loader._bare_text_nodes(soup, soup.find_all("p"))
        return [str(node).strip() for node in nodes if str(node).strip()]

    assert bare_strings(REAL_CHAPTER) == [
        "Doc title",
        "Loose body text",
        "Loose div text",
        "漢",
    ]
    assert bare_strings(PREFIXED_CHAPTER) == ["Prefixed loose text"]
    assert bare_strings(
        '<h:body xmlns:h="http://www.w3.org/1999/xhtml">Loose'
        "<h:pre>kept as is</h:pre></h:body>",
        exclude="h:pre",
    ) == ["Loose"]
    # MathML symbols stay as they are; <mtext> prose is still translated
    assert bare_strings(
        "<math><mi>speed</mi><mo>×</mo><mn>2</mn><mtext>per hour</mtext></math>"
    ) == ["per hour"]


def test_real_epub_with_bare_text_adds_no_duplicate_requests(tmp_path, monkeypatch):
    book = Path(__file__).resolve().parent.parent / "test_books" / "animal_farm.epub"
    runs = {}
    for flag in (False, True):
        run_dir = tmp_path / str(flag)
        run_dir.mkdir()
        shutil.copy(book, run_dir / "book.epub")
        loader, output = _make_loader(run_dir, monkeypatch, [])
        loader.allow_navigable_strings = flag
        loader.make_bilingual_book()
        assert output.exists()
        runs[flag] = loader.translate_model.calls

    # bare text keeps every paragraph request, adds the headings, verse and
    # titles outside <p>, and never sends a paragraph's text a second time
    paragraph_texts = Counter(runs[False])
    with_bare_text = Counter(runs[True])
    assert with_bare_text >= paragraph_texts
    extra = with_bare_text - paragraph_texts
    assert "Beasts of England, beasts of Ireland," in extra
    assert not set(extra) & set(paragraph_texts)
    assert sum(map(len, runs[True])) < 1.1 * sum(map(len, runs[False]))
