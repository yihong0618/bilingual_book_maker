"""`--batch-api` on EPUB books: one walk records, one batch answers.

The EPUB loader collects with a throwaway copy of itself whose translator
answers every request with its own source (`claude_batch`, `stand_in`).
What is asserted is what costs money or a book if it is wrong: the batch
carries exactly the requests a live run sends, on every dispatch path; the
book is the one a live run writes; nothing reaches the disk before the
batch is sent; a rerun polls instead of paying again; a bad reply is the
only thing sent live; an interrupted collection submits nothing.
"""

import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup
from ebooklib import epub

from book_maker.cli import attach_message_batches
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.translator.claude_batch import (
    PARALLEL_SET_ASIDE,
    BatchFailed,
    effective_body,
    request_key,
)
from book_maker.translator.claude_translator import Claude
from test_claude_message_batches import FakeAnthropic, message, translate_content

CHAPTERS = 3
PARAGRAPHS = 4


def write_epub(path):
    book = epub.EpubBook()
    book.set_identifier("batch-epub")
    book.set_title("Batch")
    book.set_language("en")
    items = []
    for chapter in range(CHAPTERS):
        item = epub.EpubHtml(
            title=f"Chapter {chapter}", file_name=f"c{chapter}.xhtml", lang="en"
        )
        body = "".join(
            f"<p>Chapter {chapter} paragraph {n} says something.</p>"
            for n in range(PARAGRAPHS)
        )
        item.content = f"<html><body><h1>Chapter {chapter}</h1>{body}</body></html>"
        book.add_item(item)
        items.append(item)
    book.toc = tuple(items)
    book.spine = items
    epub.write_epub(str(path), book)
    return path


def make_loader(path, fake, mode="default", batch=True, resume=False):
    with patch("book_maker.translator.claude_translator.Anthropic", return_value=fake):
        loader = EPUBBookLoader(
            str(path), Claude, "sk-test", resume, "Simplified Chinese"
        )
    loader.translate_model.set_model_list(["claude-sonnet-5-5"])
    loader.quiet = True
    if mode == "accumulated":
        loader.accumulated_num = 200
    elif mode == "plan":
        loader.plan_mode = True
        loader.plan_classify = "all"
        loader.translate_tags = "auto"
    if batch:
        attach_message_batches(loader)
        loader.translate_model.batch_queue.sleep = lambda seconds: None
    return loader


def book_text(path):
    """Each content document's text, in order: the book a reader gets."""
    with zipfile.ZipFile(path) as archive:
        return [
            BeautifulSoup(archive.read(name), "html.parser").get_text(" ", strip=True)
            for name in sorted(archive.namelist())
            if name.endswith(".xhtml") and "nav" not in name
        ]


def live_keys(fake):
    return [request_key(effective_body(params)) for params in fake.live]


def batched_keys(fake):
    return [request["custom_id"] for batch in fake.created for request in batch]


def bilingual(tmp_path):
    return tmp_path / "book_bilingual.epub"


@pytest.mark.parametrize("mode", ["default", "accumulated", "plan"])
def test_every_path_sends_the_live_runs_requests_in_one_batch(tmp_path, mode):
    live_dir, batch_dir = tmp_path / "live", tmp_path / "batch"
    live_dir.mkdir()
    batch_dir.mkdir()
    # Both sources before any loader exists: the loader patches ebooklib's
    # writer for the whole process, so a book written after it is not the
    # same bytes as one written before.
    write_epub(live_dir / "book.epub")
    write_epub(batch_dir / "book.epub")

    live = FakeAnthropic()
    make_loader(live_dir / "book.epub", live, mode, batch=False).make_bilingual_book()

    fake = FakeAnthropic()
    fake.processing_polls = 0
    make_loader(batch_dir / "book.epub", fake, mode).make_bilingual_book()

    assert len(fake.created) == 1
    assert fake.live == []
    # The same requests, body for body: the replay found every one.
    assert batched_keys(fake) == list(dict.fromkeys(live_keys(live)))
    assert book_text(bilingual(batch_dir)) == book_text(bilingual(live_dir))
    assert "ZH<Chapter 2 paragraph 3 says something.>" in " ".join(
        book_text(bilingual(batch_dir))
    )


def test_nothing_is_written_before_the_batch_is_sent(tmp_path):
    book = write_epub(tmp_path / "book.epub")
    seen = {}

    class Watching(FakeAnthropic):
        def _batch_create(self, requests):
            seen["files"] = sorted(p.name for p in tmp_path.iterdir())
            raise_refused()

    def raise_refused():
        import httpx
        from anthropic import BadRequestError

        response = httpx.Response(400, request=httpx.Request("POST", "https://x"))
        raise BadRequestError("refused", response=response, body=None)

    fake = Watching()
    loader = make_loader(book, fake)
    with pytest.raises(BatchFailed):
        loader.make_bilingual_book()

    # At the moment of the create: the source, the batch's own state file,
    # and nothing the collection pass translated.
    assert seen["files"] == [".book.temp.claude-batch.json", "book.epub"]
    assert not bilingual(tmp_path).exists()
    assert not (tmp_path / "book_bilingual_temp.epub").exists()
    assert not (tmp_path / ".book.temp.bin").exists()
    assert fake.live == []


def test_a_rerun_polls_the_batch_it_submitted_instead_of_paying_again(tmp_path):
    book = write_epub(tmp_path / "book.epub")
    fake = FakeAnthropic()
    fake.processing_polls = 1
    loader = make_loader(book, fake)

    def interrupted(seconds):
        raise KeyboardInterrupt

    loader.translate_model.batch_queue.sleep = interrupted
    with pytest.raises(KeyboardInterrupt):
        loader.make_bilingual_book()
    assert len(fake.created) == 1
    assert not bilingual(tmp_path).exists()

    rerun = make_loader(book, fake)
    rerun.make_bilingual_book()

    assert len(fake.created) == 1
    assert fake.live == []
    assert bilingual(tmp_path).exists()


class AnswersInBatch(FakeAnthropic):
    """The batch's answer to one request is `bad(params)`; live is perfect."""

    def __init__(self, bad):
        super().__init__()
        self.processing_polls = 0
        self.bad = bad

    def _results(self, batch_id):
        entries = []
        for request in self.batches_store[batch_id]:
            params = request["params"]
            reply = self.bad(params) or self.answer(params)
            entries.append(
                SimpleNamespace(
                    custom_id=request["custom_id"],
                    result=SimpleNamespace(type="succeeded", message=reply),
                )
            )
        return iter(entries)


def content(params):
    return params["messages"][-1]["content"]


def test_a_misaligned_group_is_repaired_live_and_the_rest_replays(tmp_path):
    book = write_epub(tmp_path / "book.epub")

    def bad(params):
        if "Chapter 1 paragraph 0" in content(params):
            return message("ZH<one merged reply>")  # four segments asked for
        return None

    fake = AnswersInBatch(bad)
    loader = make_loader(book, fake, mode="accumulated")
    loader.make_bilingual_book()

    assert len(fake.created) == 1
    # Only chapter 1's paragraphs went live, one at a time.
    assert len(fake.live) == PARAGRAPHS
    assert all("Chapter 1 paragraph" in content(p) for p in fake.live)
    text = " ".join(book_text(bilingual(tmp_path)))
    for chapter in range(CHAPTERS):
        assert f"ZH<Chapter {chapter} paragraph 3 says something.>" in text


def test_an_unusable_batch_reply_is_sent_live_without_a_second_batch(tmp_path):
    book = write_epub(tmp_path / "book.epub")

    def bad(params):
        if "Chapter 2 paragraph 1" in content(params):
            return message("ZH<cut off", stop_reason="max_tokens")
        return None

    fake = AnswersInBatch(bad)
    loader = make_loader(book, fake)
    loader.make_bilingual_book()

    assert len(fake.created) == 1
    assert len(fake.live) == 1
    assert "Chapter 2 paragraph 1" in content(fake.live[0])
    assert "ZH<Chapter 2 paragraph 1 says something.>" in " ".join(
        book_text(bilingual(tmp_path))
    )


def test_parallel_workers_are_set_aside_with_a_line(tmp_path, capsys):
    book = write_epub(tmp_path / "book.epub")
    fake = FakeAnthropic()
    fake.processing_polls = 0
    loader = make_loader(book, fake)
    loader.set_parallel_workers(3)
    loader.make_bilingual_book()

    out = " ".join(capsys.readouterr().out.split())
    assert " ".join(PARALLEL_SET_ASIDE.split()) in out
    assert len(fake.created) == 1
    assert fake.live == []
    assert len(batched_keys(fake)) == CHAPTERS * PARAGRAPHS


def test_a_resumed_book_batches_only_what_the_checkpoint_lacks(tmp_path):
    book = write_epub(tmp_path / "book.epub")
    calls = []

    def interrupting(params):
        calls.append(params)
        if len(calls) == 6:
            raise KeyboardInterrupt
        return message(translate_content(content(params)))

    first = FakeAnthropic(answer=interrupting)
    with pytest.raises(SystemExit):
        make_loader(book, first, batch=False).make_bilingual_book()
    assert (tmp_path / ".book.temp.bin").exists()

    fake = FakeAnthropic()
    fake.processing_polls = 0
    make_loader(book, fake, resume=True).make_bilingual_book()

    # Five were translated before the interrupt; the batch carries the rest.
    assert len(batched_keys(fake)) == CHAPTERS * PARAGRAPHS - 5
    assert fake.live == []
    text = " ".join(book_text(bilingual(tmp_path)))
    assert "ZH<Chapter 0 paragraph 0 says something.>" in text
    assert "ZH<Chapter 2 paragraph 3 says something.>" in text


def test_an_interrupted_collection_submits_nothing_and_leaves_the_loader_alone(
    tmp_path,
):
    book = write_epub(tmp_path / "book.epub")
    fake = FakeAnthropic()
    loader = make_loader(book, fake)
    queue = loader.translate_model.batch_queue
    before = [item.content for item in loader.origin_book.get_items()]
    recorded = queue.collect

    def collect(params):
        if len(queue.pending) == 3:
            raise KeyboardInterrupt
        recorded(params)

    queue.collect = collect
    with pytest.raises(SystemExit) as exit_:
        loader.make_bilingual_book()

    assert exit_.value.code == 130
    assert queue.stand_in is False
    assert fake.created == [] and fake.live == []
    assert [item.content for item in loader.origin_book.get_items()] == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["book.epub"]


def test_a_plan_the_walk_gave_up_is_given_up_by_the_real_pass_too(
    tmp_path, monkeypatch
):
    """An automatic plan that fails once and would succeed on a second try:
    the batch was collected in tag mode, so the real pass must translate in
    tag mode, not build the plan again and send its requests live."""
    live_dir, batch_dir = tmp_path / "live", tmp_path / "batch"
    live_dir.mkdir()
    batch_dir.mkdir()
    write_epub(live_dir / "book.epub")
    write_epub(batch_dir / "book.epub")

    live = FakeAnthropic()
    make_loader(live_dir / "book.epub", live, batch=False).make_bilingual_book()

    attempts = []
    prepare = EPUBBookLoader._prepare_translation_plan

    def fails_once(self):
        attempts.append(self._dry_run)
        if len(attempts) == 1:
            raise RuntimeError("the classifier did not answer")
        return prepare(self)

    monkeypatch.setattr(EPUBBookLoader, "_prepare_translation_plan", fails_once)
    fake = FakeAnthropic()
    fake.processing_polls = 0
    loader = make_loader(batch_dir / "book.epub", fake, mode="plan")
    loader.plan_auto = True
    loader.make_bilingual_book()

    assert attempts == [True]
    assert fake.live == []
    assert batched_keys(fake) == list(dict.fromkeys(live_keys(live)))
    assert book_text(bilingual(batch_dir)) == book_text(bilingual(live_dir))
