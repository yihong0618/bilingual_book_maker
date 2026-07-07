import json

import pytest

from book_maker.loader.txt_loader import TXTBookLoader


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


class InterruptingModel(EchoModel):
    def translate(self, text):
        self.calls.append(text)
        if len(self.calls) > 1:
            raise KeyboardInterrupt
        return f"<T>{text}</T>"


def make_loader(path, model=EchoModel, *, resume=False, is_test=False, test_num=5):
    return TXTBookLoader(
        str(path),
        model,
        key="",
        resume=resume,
        language="en",
        is_test=is_test,
        test_num=test_num,
    )


def test_txt_resume_uses_saved_batches_without_duplication(tmp_path):
    book = tmp_path / "book.txt"
    book.write_text("one\ntwo\nthree\n", encoding="utf-8")

    loader = make_loader(book, InterruptingModel)
    loader.batch_size = 1
    with pytest.raises(SystemExit) as exc:
        loader.make_bilingual_book()
    assert exc.value.code == 0

    resumed = make_loader(book, EchoModel, resume=True)
    resumed.batch_size = 1
    resumed.make_bilingual_book()

    content = (tmp_path / "book_bilingual.txt").read_text(encoding="utf-8")
    assert content.count("<T>one</T>") == 1
    assert content.count("<T>two</T>") == 1
    assert content.count("<T>three</T>") == 1
    assert EchoModel.instances[-1].calls == ["two", "three"]


def test_txt_test_num_limits_translated_lines(tmp_path):
    book = tmp_path / "book.txt"
    book.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    loader = make_loader(book, is_test=True, test_num=2)
    loader.batch_size = 10
    loader.make_bilingual_book()

    content = (tmp_path / "book_bilingual.txt").read_text(encoding="utf-8")
    assert "one" in content
    assert "two" in content
    assert "three" not in content
    assert "four" not in content
    assert EchoModel.instances[-1].calls == ["one\ntwo"]


def test_txt_resume_state_preserves_multiline_translations(tmp_path):
    book = tmp_path / "book.txt"
    book.write_text("one\ntwo\n", encoding="utf-8")

    loader = make_loader(book)
    loader.p_to_save = ["<T>one\nsecond translated line</T>"]
    loader._save_progress()

    state = json.loads((tmp_path / ".book.temp.bin").read_text(encoding="utf-8"))
    assert state == ["<T>one\nsecond translated line</T>"]

    resumed = make_loader(book, EchoModel, resume=True)
    assert resumed.p_to_save == ["<T>one\nsecond translated line</T>"]
