import threading

import pytest
from ebooklib import ITEM_DOCUMENT, epub

from book_maker.loader.epub_loader import EPUBBookLoader


class RecordingModel:
    instances = []
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"

    def __init__(
        self,
        key,
        language,
        api_base=None,
        context_flag=False,
        context_paragraph_limit=0,
        temperature=1.0,
        source_lang="auto",
        **kwargs,
    ):
        self.calls = []
        self.list_calls = []
        self.contexts_at_call = []
        self.context_flag = context_flag
        self.context_paragraph_limit = context_paragraph_limit or 3
        self.context_list = []
        self.context_translated_list = []
        self._fatal_error_detected = False
        type(self).instances.append(self)

    def translate(self, text):
        self.calls.append(text)
        self.contexts_at_call.append(list(self.context_list))
        translated = f"<T>{text}</T>"
        if self.context_flag:
            self.context_list.append(text)
            self.context_translated_list.append(translated)
            self.context_list = self.context_list[-self.context_paragraph_limit :]
            self.context_translated_list = self.context_translated_list[
                -self.context_paragraph_limit :
            ]
        return translated

    def translate_list(self, texts):
        plain_texts = [str(text) for text in texts]
        self.list_calls.append(plain_texts)
        return [self.translate(text) for text in plain_texts]


class InterruptingModel(RecordingModel):
    def translate(self, text):
        if self.calls:
            raise KeyboardInterrupt
        return super().translate(text)


class OrderedCompletionModel(RecordingModel):
    """Make chapter B finish before chapter A after A obtained index zero."""

    a_started = threading.Event()
    b_finished = threading.Event()

    def translate(self, text):
        self.calls.append(text)
        if text == "chapter A":
            type(self).a_started.set()
            assert type(self).b_finished.wait(timeout=2)
        elif text == "chapter B":
            assert type(self).a_started.wait(timeout=2)
            type(self).b_finished.set()
        return f"<T>{text}</T>"


class FailingModel(RecordingModel):
    def translate(self, text):
        raise RuntimeError(f"cannot translate {text}")


def _write_epub(path, chapters):
    book = epub.EpubBook()
    book.set_identifier("correctness-baseline")
    book.set_title("Correctness baseline")
    book.set_language("en")

    items = []
    for index, (file_name, paragraphs) in enumerate(chapters):
        item = epub.EpubHtml(
            title=f"Chapter {index + 1}", file_name=file_name, lang="en"
        )
        body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
        item.content = f"<html><body>{body}</body></html>"
        book.add_item(item)
        items.append(item)

    book.toc = tuple(items)
    book.spine = items
    epub.write_epub(str(path), book)


def _make_loader(
    tmp_path,
    monkeypatch,
    chapters,
    model=RecordingModel,
    *,
    resume=False,
    parallel_workers=1,
    context_flag=False,
    is_test=False,
    test_num=5,
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    if not source.exists():
        _write_epub(source, chapters)

    loader = EPUBBookLoader(
        str(source),
        model,
        key="",
        resume=resume,
        language="zh-hans",
        context_flag=context_flag,
        context_paragraph_limit=3,
        parallel_workers=parallel_workers,
        is_test=is_test,
        test_num=test_num,
    )
    return loader, source.with_name("book_bilingual.epub")


def _document_texts(path):
    book = epub.read_epub(str(path))
    return {
        item.file_name: item.get_body_content().decode("utf-8")
        for item in book.get_items_of_type(ITEM_DOCUMENT)
    }


def test_epub_sequential_bilingual_output_preserves_source(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["one", "two"])]
    )

    loader.make_bilingual_book()

    content = _document_texts(output)["one.xhtml"]
    assert content.index("one") < content.index("&lt;T&gt;one&lt;/T&gt;")
    assert content.index("two") < content.index("&lt;T&gt;two&lt;/T&gt;")


def test_epub_sequential_single_translate_removes_source(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path, monkeypatch, [("one.xhtml", ["source only"])]
    )
    loader.single_translate = True

    loader.make_bilingual_book()

    content = _document_texts(output)["one.xhtml"]
    assert content.count("<p>") == 1
    assert "&lt;T&gt;source only&lt;/T&gt;" in content


def test_epub_sequential_honors_excluded_files(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("keep.xhtml", ["translate me"]), ("skip.xhtml", ["do not translate"])],
    )
    loader.exclude_filelist = "skip.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]
    documents = _document_texts(output)
    assert "&lt;T&gt;translate me&lt;/T&gt;" in documents["keep.xhtml"]
    assert "&lt;T&gt;do not translate&lt;/T&gt;" not in documents["skip.xhtml"]


def test_epub_sequential_honors_only_filelist(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("keep.xhtml", ["translate me"]), ("skip.xhtml", ["do not translate"])],
    )
    loader.only_filelist = "keep.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]


def test_epub_sequential_test_num_limits_requests(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three"])],
        is_test=True,
        test_num=2,
    )

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["one", "two"]


def test_epub_sequential_context_follows_document_order(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three"])],
        context_flag=True,
    )

    loader.make_bilingual_book()

    assert loader.translate_model.contexts_at_call == [[], ["one"], ["one", "two"]]


def test_epub_sequential_accumulated_num_batches_in_document_order(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("book_maker.loader.epub_loader.num_tokens_from_text", len)
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three"])],
    )
    loader.accumulated_num = 100

    loader.make_bilingual_book()

    assert loader.translate_model.list_calls == [["one", "two", "three"]]
    content = _document_texts(output)["one.xhtml"]
    for text in ("one", "two", "three"):
        assert content.count(f"&lt;T&gt;{text}&lt;/T&gt;") == 1


def test_epub_sequential_sentence_mode_preserves_sentence_order(tmp_path, monkeypatch):
    loader, output = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["First sentence. Second sentence."])],
    )
    loader.sentence_mode = True

    loader.make_bilingual_book()

    assert loader.translate_model.list_calls == [
        ["First sentence.", "Second sentence."]
    ]
    content = _document_texts(output)["one.xhtml"]
    assert content.index("First sentence.") < content.index("Second sentence.")
    assert content.index("&lt;T&gt;First sentence.&lt;/T&gt;") < content.index(
        "&lt;T&gt;Second sentence.&lt;/T&gt;"
    )


def test_epub_sequential_interrupt_and_resume_uses_completed_prefix(
    tmp_path, monkeypatch
):
    chapters = [("one.xhtml", ["one", "two", "three"])]
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters, model=InterruptingModel)

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code == 0
    assert loader.p_to_save == ["<T>one</T>"]

    resumed, output = _make_loader(
        tmp_path, monkeypatch, chapters, model=RecordingModel, resume=True
    )
    resumed.make_bilingual_book()

    assert resumed.translate_model.calls == ["two", "three"]
    content = _document_texts(output)["one.xhtml"]
    for text in ("one", "two", "three"):
        assert content.count(f"&lt;T&gt;{text}&lt;/T&gt;") == 1


@pytest.mark.xfail(
    strict=True,
    reason="block_size translation sends text from inline excluded tags to the model",
)
def test_epub_sequential_batch_excludes_inline_code_content(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["before <code>secret()</code> after"])],
    )

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["before  after"]


@pytest.mark.xfail(
    strict=True,
    reason="sequential translation errors currently terminate with a success exit code",
)
def test_epub_sequential_translation_failure_exits_nonzero(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one"])],
        model=FailingModel,
    )

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code != 0


@pytest.mark.xfail(
    strict=True,
    reason="parallel resume cache is appended in completion order, not document order",
)
def test_epub_parallel_checkpoint_order_is_deterministic(tmp_path, monkeypatch):
    OrderedCompletionModel.a_started.clear()
    OrderedCompletionModel.b_finished.clear()
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])],
        model=OrderedCompletionModel,
        parallel_workers=2,
    )

    loader.make_bilingual_book()

    assert loader.p_to_save == ["<T>chapter A</T>", "<T>chapter B</T>"]


@pytest.mark.xfail(
    strict=True,
    reason="parallel EPUB processing bypasses process_item file filtering",
)
def test_epub_parallel_honors_excluded_files(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("keep.xhtml", ["translate me"]), ("skip.xhtml", ["do not translate"])],
        parallel_workers=2,
    )
    loader.exclude_filelist = "skip.xhtml"

    loader.make_bilingual_book()

    assert loader.translate_model.calls == ["translate me"]


@pytest.mark.xfail(
    strict=True,
    reason="parallel EPUB processing does not enforce the global test_num limit",
)
def test_epub_parallel_test_num_limits_requests(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one", "two"]), ("b.xhtml", ["three", "four"])],
        parallel_workers=2,
        is_test=True,
        test_num=1,
    )

    loader.make_bilingual_book()

    assert len(loader.translate_model.calls) == 1


@pytest.mark.xfail(
    strict=True,
    reason="EPUB chapters currently share the same mutable translator instance",
)
def test_epub_parallel_creates_an_isolated_translator_per_chapter(
    tmp_path, monkeypatch
):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one"]), ("b.xhtml", ["two"])],
        parallel_workers=2,
        context_flag=True,
    )

    first = loader._create_chapter_translator()
    second = loader._create_chapter_translator()

    assert first is not loader.translate_model
    assert second is not loader.translate_model
    assert first is not second


@pytest.mark.xfail(
    strict=True,
    reason="parallel chapter errors are swallowed and the original chapter is emitted",
)
def test_epub_parallel_propagates_chapter_failures(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one"]), ("b.xhtml", ["two"])],
        model=FailingModel,
        parallel_workers=2,
    )

    with pytest.raises(RuntimeError, match="cannot translate"):
        loader.make_bilingual_book()


@pytest.mark.xfail(
    strict=True,
    reason="parallel EPUB processing bypasses block_size batch translation",
)
def test_epub_parallel_honors_block_size(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one", "two"]), ("b.xhtml", ["three", "four"])],
        parallel_workers=2,
    )
    loader.block_size = 2

    loader.make_bilingual_book()

    assert sorted(loader.translate_model.list_calls) == [
        ["one", "two"],
        ["three", "four"],
    ]
    assert sorted(loader.translate_model.calls) == ["four", "one", "three", "two"]
