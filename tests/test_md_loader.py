import json
import threading
import time

import pytest

from book_maker.loader.md_loader import MarkdownBookLoader


class EchoModel:
    instances = []

    def __init__(
        self,
        key,
        language,
        api_base=None,
        temperature=1.0,
        source_lang="auto",
        **kwargs,
    ):
        self.calls = []
        EchoModel.instances.append(self)

    def translate(self, text):
        self.calls.append(text)
        return f"<T>{text}</T>"


class ListModel(EchoModel):
    def __init__(
        self,
        key,
        language,
        api_base=None,
        temperature=1.0,
        source_lang="auto",
        **kwargs,
    ):
        super().__init__(key, language, api_base, temperature, source_lang, **kwargs)
        self.list_calls = []

    def translate(self, text):
        raise AssertionError("Markdown batches should use translate_list")

    def translate_list(self, texts):
        self.list_calls.append(list(texts))
        return [f"<T>{text}</T>" for text in texts]


class ContextListModel(ListModel):
    def __init__(
        self,
        key,
        language,
        api_base=None,
        temperature=1.0,
        source_lang="auto",
        context_flag=False,
        context_paragraph_limit=0,
        **kwargs,
    ):
        super().__init__(key, language, api_base, temperature, source_lang, **kwargs)
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        self.contexts_at_call = []

    def translate_list(self, texts):
        self.contexts_at_call.append(list(self.context_list))
        return super().translate_list(texts)


class SlowListModel(ListModel):
    def __init__(
        self,
        key,
        language,
        api_base=None,
        temperature=1.0,
        source_lang="auto",
        context_flag=False,
        context_paragraph_limit=0,
        **kwargs,
    ):
        super().__init__(key, language, api_base, temperature, source_lang, **kwargs)
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def translate_list(self, texts):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.03)
            return super().translate_list(texts)
        finally:
            with self.lock:
                self.active -= 1


class InterruptingListModel(ListModel):
    def translate_list(self, texts):
        self.list_calls.append(list(texts))
        if texts == ["Two."]:
            time.sleep(0.03)
            raise KeyboardInterrupt
        if texts == ["Three."]:
            time.sleep(0.06)
        return [f"<T>{text}</T>" for text in texts]


class MultilineModel(EchoModel):
    def translate(self, text):
        self.calls.append(text)
        return f"<T>{text}\nsecond translated line</T>"


class InterruptingModel(EchoModel):
    def translate(self, text):
        self.calls.append(text)
        if len(self.calls) > 1:
            raise KeyboardInterrupt
        return f"<T>{text}</T>"


def make_loader(
    path,
    model=EchoModel,
    *,
    resume=False,
    is_test=False,
    test_num=5,
    context_flag=False,
    parallel_workers=1,
):
    return MarkdownBookLoader(
        str(path),
        model,
        key="",
        resume=resume,
        language="en",
        is_test=is_test,
        test_num=test_num,
        context_flag=context_flag,
        parallel_workers=parallel_workers,
    )


def test_markdown_loader_writes_source_translation_pairs(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("First paragraph.\n\nSecond paragraph.\n\nThird paragraph.\n")

    loader = make_loader(book)
    loader.batch_size = 1
    loader.make_bilingual_book()

    content = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert content.count("<T>") == 3
    assert "First paragraph.\n<T>First paragraph.</T>" in content
    assert "Second paragraph.\n<T>Second paragraph.</T>" in content
    assert "Third paragraph.\n<T>Third paragraph.</T>" in content


def test_markdown_resume_uses_saved_batches_without_duplication(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("One.\n\nTwo.\n\nThree.\n")

    loader = make_loader(book, InterruptingModel)
    loader.batch_size = 1
    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code == 0
    assert len(InterruptingModel.instances[-1].calls) == 2

    resume_loader = make_loader(book, EchoModel, resume=True)
    resume_loader.batch_size = 1
    resume_loader.make_bilingual_book()

    content = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert content.count("<T>One.</T>") == 1
    assert content.count("<T>Two.</T>") == 1
    assert content.count("<T>Three.</T>") == 1
    assert EchoModel.instances[-1].calls == ["Two.", "Three."]


def test_markdown_test_num_limits_translated_paragraphs(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("One.\n\nTwo.\n\nThree.\n\nFour.\n")

    loader = make_loader(book, is_test=True, test_num=2)
    loader.batch_size = 10
    loader.make_bilingual_book()

    content = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert "One." in content
    assert "Two." in content
    assert "Three." not in content
    assert "Four." not in content
    assert EchoModel.instances[-1].calls == ["One.", "Two."]


def test_markdown_resume_state_preserves_multiline_translations(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("One.\n\nTwo.\n")

    loader = make_loader(book, MultilineModel)
    loader.batch_size = 1
    with pytest.raises(SystemExit) as exc:
        loader.translate_model.translate = lambda text: (_ for _ in ()).throw(
            KeyboardInterrupt
        )
        loader.p_to_save = ["<T>One.\nsecond translated line</T>"]
        loader.make_bilingual_book()
    assert exc.value.code == 0

    state = json.loads((tmp_path / ".book.temp.bin").read_text(encoding="utf-8"))
    assert state == [["<T>One.\nsecond translated line</T>"]]

    resumed = make_loader(book, EchoModel, resume=True)
    assert resumed.p_to_save == [["<T>One.\nsecond translated line</T>"]]


def test_markdown_structure_blocks_are_not_sent_to_translator(tmp_path):
    book = tmp_path / "book.md"
    book.write_text(
        "\n".join(
            [
                "---",
                "title: Keep Me",
                "---",
                "",
                "# Heading",
                "",
                "Paragraph with `inline_code()` and [a link](https://example.com/path).",
                "",
                "```python",
                "# not a heading",
                "",
                "print('keep')",
                "```",
                "",
                "| Name | Value |",
                "| ---- | ----- |",
                "| A    | 1     |",
                "",
                "![Alt text](image.png)",
                "",
                "{4}------------------------------------------------",
                "",
                "Final paragraph.",
            ]
        )
    )

    loader = make_loader(book)
    loader.batch_size = 1
    loader.make_bilingual_book()

    calls = EchoModel.instances[-1].calls
    assert calls[0] == "# Heading"
    assert calls[2] == "Final paragraph."
    assert "inline_code()" not in calls[1]
    assert "https://example.com/path" not in calls[1]

    output = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert "---\ntitle: Keep Me\n---" in output
    assert "```python\n# not a heading\n\nprint('keep')\n```" in output
    assert "| Name | Value |\n| ---- | ----- |\n| A    | 1     |" in output
    assert "![Alt text](image.png)" in output
    assert "`inline_code()`" in output
    assert "https://example.com/path" in output


def test_markdown_uses_translate_list_and_interleaves_per_block(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# Heading\n\nFirst paragraph.\n\nSecond paragraph.\n")

    loader = make_loader(book, ListModel)
    loader.batch_size = 10
    loader.make_bilingual_book()

    model = ListModel.instances[-1]
    assert model.list_calls == [["# Heading", "First paragraph.", "Second paragraph."]]

    content = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert "# Heading\n<T># Heading</T>" in content
    assert "First paragraph.\n<T>First paragraph.</T>" in content
    assert "Second paragraph.\n<T>Second paragraph.</T>" in content


def test_markdown_chunking_keeps_heading_with_body_and_honors_char_budget(tmp_path):
    book = tmp_path / "book.md"
    book.write_text(
        "# First\n\nBody one.\n\nBody two is longer.\n\n# Second\n\nBody three.\n"
    )

    loader = make_loader(book, ListModel)
    loader.batch_size = 1
    loader.md_chunk_char_budget = 25
    loader.make_bilingual_book()

    model = ListModel.instances[-1]
    assert model.list_calls == [
        ["# First", "Body one."],
        ["Body two is longer."],
        ["# Second", "Body three."],
    ]
    assert all(sum(len(item) for item in call) <= 25 for call in model.list_calls)


def test_markdown_use_context_injects_heading_breadcrumb_without_emitting_it(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# Main\n\nIntro.\n\n## Detail\n\nBody.\n")

    loader = make_loader(book, ContextListModel, context_flag=True)
    loader.batch_size = 10
    loader.make_bilingual_book()

    model = ContextListModel.instances[-1]
    assert any("Markdown section context: Main" in "\n".join(c) for c in model.contexts_at_call)
    assert any(
        "Markdown section context: Main > Detail" in "\n".join(c)
        for c in model.contexts_at_call
    )

    output = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert "Markdown section context:" not in output


def test_markdown_parallel_workers_preserve_sequential_output_order(tmp_path):
    book = tmp_path / "book.md"
    book.write_text(
        "\n\n".join(
            [
                "# One",
                "First body.",
                "# Two",
                "Second body.",
                "# Three",
                "Third body.",
            ]
        )
    )

    sequential = make_loader(book, ListModel, parallel_workers=1)
    sequential.batch_size = 1
    sequential.make_bilingual_book()
    sequential_output = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")

    parallel = make_loader(book, SlowListModel, parallel_workers=4)
    parallel.batch_size = 1
    parallel.make_bilingual_book()
    parallel_output = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")

    assert parallel_output == sequential_output
    assert SlowListModel.instances[-1].max_active > 1


def test_markdown_parallel_keeps_pass_through_blocks_untouched(tmp_path):
    book = tmp_path / "book.md"
    book.write_text(
        "\n".join(
            [
                "---",
                "title: Keep",
                "---",
                "",
                "# Heading",
                "",
                "Body.",
                "",
                "```",
                "code",
                "```",
                "",
                "| A | B |",
                "|---|---|",
                "| 1 | 2 |",
                "",
                "# Next",
                "",
                "More body.",
            ]
        )
    )

    loader = make_loader(book, SlowListModel, parallel_workers=4)
    loader.batch_size = 1
    loader.make_bilingual_book()

    output = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert "---\ntitle: Keep\n---" in output
    assert "```\ncode\n```" in output
    assert "| A | B |\n|---|---|\n| 1 | 2 |" in output
    assert SlowListModel.instances[-1].max_active > 1


def test_markdown_use_context_forces_parallel_workers_to_sequential(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("# One\n\nFirst body.\n\n# Two\n\nSecond body.\n")

    loader = make_loader(
        book,
        SlowListModel,
        context_flag=True,
        parallel_workers=4,
    )
    loader.batch_size = 1
    loader.make_bilingual_book()

    assert SlowListModel.instances[-1].max_active == 1


def test_markdown_parallel_interrupt_persists_contiguous_prefix_and_resumes(tmp_path):
    book = tmp_path / "book.md"
    book.write_text("One.\n\nTwo.\n\nThree.\n")

    loader = make_loader(book, InterruptingListModel, parallel_workers=3)
    loader.batch_size = 1
    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code == 0

    state = json.loads((tmp_path / ".book.temp.bin").read_text(encoding="utf-8"))
    assert state == [["<T>One.</T>"]]

    resume_loader = make_loader(book, ListModel, resume=True, parallel_workers=3)
    resume_loader.batch_size = 1
    resume_loader.make_bilingual_book()

    content = (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")
    assert content.count("<T>One.</T>") == 1
    assert content.count("<T>Two.</T>") == 1
    assert content.count("<T>Three.</T>") == 1
