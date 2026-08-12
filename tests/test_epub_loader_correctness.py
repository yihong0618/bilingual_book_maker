import pickle
import threading
import zipfile

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


class ParallelInterruptingModel(RecordingModel):
    first_finished = threading.Event()

    def translate(self, text):
        if text == "chapter A":
            result = super().translate(text)
            type(self).first_finished.set()
            return result
        assert type(self).first_finished.wait(timeout=2)
        raise KeyboardInterrupt


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


def _replace_epub_member(path, member_name, content):
    """Replace one archive member without leaving a duplicate ZIP entry."""
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    content.encode("utf-8")
                    if info.filename == member_name
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)


def _write_epub_with_custom_nav(path):
    book = epub.EpubBook()
    book.set_identifier("nav-correctness-baseline")
    book.set_title("Navigation correctness baseline")
    book.set_language("en")

    chapter = epub.EpubHtml(
        uid="chapter", title="Chapter one", file_name="chapter.xhtml", lang="en"
    )
    chapter.content = "<html><body><p>Chapter body</p></body></html>"
    nav = epub.EpubNav(uid="nav", file_name="toc.xhtml")
    book.add_item(chapter)
    book.add_item(nav)
    book.toc = (epub.Link("chapter.xhtml", "Chapter one", "chapter-link"),)
    book.spine = [nav, chapter]
    epub.write_epub(str(path), book)

    custom_nav = """<?xml version="1.0" encoding="utf-8"?>
    <html xmlns="http://www.w3.org/1999/xhtml"
          xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
      <head><title>Source navigation title</title></head>
      <body data-source-marker="preserve-me">
        <h1>Source navigation heading</h1>
        <nav epub:type="toc" id="toc" role="doc-toc">
          <h2>Source table of contents</h2>
          <ol><li><a href="chapter.xhtml">Chapter one</a></li></ol>
        </nav>
      </body>
    </html>"""
    _replace_epub_member(path, "EPUB/toc.xhtml", custom_nav)


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


def test_plan_mode_preserves_translated_imported_nav_and_manifest(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    _write_epub_with_custom_nav(source)
    loader = EPUBBookLoader(
        str(source),
        RecordingModel,
        key="",
        resume=False,
        language="zh-hans",
    )
    loader.plan_mode = True
    loader.translate_tags = "auto"
    loader.quiet = True

    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/toc.xhtml").decode("utf-8")
        opf = archive.read("EPUB/content.opf").decode("utf-8")
    assert 'data-source-marker="preserve-me"' in nav
    assert 'id="toc"' in nav
    assert 'id="id"' not in nav
    assert "Source navigation heading" in nav
    assert "&lt;T&gt;Source navigation heading&lt;/T&gt;" in nav
    nav_manifest = next(line for line in opf.splitlines() if 'href="toc.xhtml"' in line)
    assert 'properties="nav"' in nav_manifest


def test_empty_nav_is_generated_with_rebuilt_book_title(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "book.epub"
    _write_epub(source, [("chapter.xhtml", ["Chapter body"])])
    loader = EPUBBookLoader(
        str(source),
        RecordingModel,
        key="",
        resume=False,
        language="zh-hans",
    )
    rebuilt = loader._make_new_book(loader.origin_book)
    rebuilt.add_item(epub.EpubNav(uid="nav", file_name="generated-nav.xhtml"))

    output = tmp_path / "generated.epub"
    epub.write_epub(str(output), rebuilt)

    with zipfile.ZipFile(output) as archive:
        nav = archive.read("EPUB/generated-nav.xhtml").decode("utf-8")
    assert rebuilt.title == "Correctness baseline"
    assert rebuilt.language == "en"
    assert "<h2>Correctness baseline</h2>" in nav


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


def test_epub_translation_plan_has_stable_document_order_ids(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["one", "two"]), ("b.xhtml", ["three"])],
    )
    document_items = list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT))

    first = loader._build_translation_plan(document_items, ["p"])
    second = loader._build_translation_plan(document_items, ["p"])
    first_jobs = [job for chapter in first for job in chapter.jobs]
    second_jobs = [job for chapter in second for job in chapter.jobs]

    assert [job.global_index for job in first_jobs] == [0, 1, 2]
    assert [job.document_index for job in first_jobs] == [0, 0, 1]
    assert [job.node_index for job in first_jobs] == [0, 1, 0]
    assert [job.context_group for job in first_jobs] == [
        "a.xhtml",
        "a.xhtml",
        "b.xhtml",
    ]
    assert [job.job_id for job in first_jobs] == [job.job_id for job in second_jobs]
    assert len({job.job_id for job in first_jobs}) == 3


def test_epub_translation_plan_assigns_stable_batch_indexes(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one", "two", "three", "four", "five"])],
    )
    loader.block_size = 2
    document_items = list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT))

    [chapter] = loader._build_translation_plan(document_items, ["p"])

    assert [job.batch_index for job in chapter.jobs] == [0, 0, 1, 1, 2]


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


def test_epub_parallel_rejects_legacy_completion_order_checkpoint(
    tmp_path, monkeypatch
):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])],
        parallel_workers=2,
    )
    with open(loader.bin_path, "wb") as checkpoint:
        pickle.dump(["<T>chapter B</T>", "<T>chapter A</T>"], checkpoint)

    with pytest.raises(ValueError, match="Legacy EPUB resume checkpoints"):
        _make_loader(
            tmp_path,
            monkeypatch,
            [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])],
            resume=True,
            parallel_workers=2,
        )


def test_epub_parallel_saves_when_committed_prefix_crosses_interval(
    tmp_path, monkeypatch
):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("one.xhtml", ["one"])],
        parallel_workers=2,
    )
    saved_lengths = []
    monkeypatch.setattr(
        loader, "_save_progress", lambda: saved_lengths.append(len(loader.p_to_save))
    )

    for index in range(1, 22):
        loader._record_translation_result(index, str(index))
    assert saved_lengths == []

    loader._record_translation_result(0, "0")

    assert len(loader.p_to_save) == 22
    assert saved_lengths == [22]


def test_epub_resume_rejects_changed_source_job_identity(tmp_path, monkeypatch):
    chapters = [("one.xhtml", ["one", "two"])]
    loader, _ = _make_loader(tmp_path, monkeypatch, chapters)
    document_items = list(loader.origin_book.get_items_of_type(ITEM_DOCUMENT))
    plans = loader._build_translation_plan(document_items, ["p"])
    loader._planned_job_ids = [job.job_id for plan in plans for job in plan.jobs]
    loader.p_to_save = ["<T>one</T>"]
    loader._save_progress()

    _write_epub(tmp_path / "book.epub", [("one.xhtml", ["changed", "two"])])
    resumed, _ = _make_loader(tmp_path, monkeypatch, chapters, resume=True)

    with pytest.raises(ValueError, match="EPUB or translation filters changed"):
        resumed.make_bilingual_book()


def test_epub_temp_book_replays_the_filtered_translation_plan(tmp_path, monkeypatch):
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        [("skip.xhtml", ["skip me"]), ("keep.xhtml", ["translate me"])],
        parallel_workers=2,
    )
    loader.exclude_filelist = "skip.xhtml"
    loader.p_to_save = ["<T>translate me</T>"]

    loader._save_temp_book()

    documents = _document_texts(tmp_path / "book_bilingual_temp.epub")
    assert "&lt;T&gt;translate me&lt;/T&gt;" not in documents["skip.xhtml"]
    assert "&lt;T&gt;translate me&lt;/T&gt;" in documents["keep.xhtml"]


def test_epub_parallel_interrupt_resumes_from_contiguous_prefix(tmp_path, monkeypatch):
    ParallelInterruptingModel.first_finished.clear()
    chapters = [("a.xhtml", ["chapter A"]), ("b.xhtml", ["chapter B"])]
    loader, _ = _make_loader(
        tmp_path,
        monkeypatch,
        chapters,
        model=ParallelInterruptingModel,
        parallel_workers=2,
    )

    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code == 0
    assert loader.p_to_save == ["<T>chapter A</T>"]

    resumed, output = _make_loader(
        tmp_path,
        monkeypatch,
        chapters,
        model=RecordingModel,
        resume=True,
        parallel_workers=2,
    )
    resumed.make_bilingual_book()

    assert resumed.translate_model.calls == ["chapter B"]
    documents = _document_texts(output)
    assert documents["a.xhtml"].count("&lt;T&gt;chapter A&lt;/T&gt;") == 1
    assert documents["b.xhtml"].count("&lt;T&gt;chapter B&lt;/T&gt;") == 1


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
    reason="parallel EPUB processing still bypasses block_size batch translation",
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
