"""`--batch-api`: Anthropic Message Batches under the Markdown loader.

A stand-in Anthropic client, no network. What is asserted is what costs
money or a book if it is wrong: one batch per book, nothing sent live that
the batch answered, a replay that finds every request it recorded, the
repair of a bad reply sent live, a rerun that polls instead of submitting
again, and a create whose outcome is unknown that refuses to repeat itself.
"""

import json
import random
import re
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from anthropic import APIConnectionError, BadRequestError
from anthropic.types import Message

from book_maker.cli import attach_message_batches, message_batch_state_path
from book_maker.loader.md_loader import MarkdownBookLoader
from book_maker.translator.base_translator import BATCH_DELIMITER
from book_maker.translator.claude_batch import (
    BatchFailed,
    BatchPending,
    ClaudeBatchQueue,
    effective_body,
    request_key,
)
from book_maker.translator.claude_translator import Claude

PARAGRAPHS = [f"Paragraph number {n} of the book, in English." for n in range(1, 8)]
BOOK = "# A Chapter\n\n" + "\n\n".join(PARAGRAPHS) + "\n"


def message(text, stop_reason="end_turn", tokens=(10, 5)):
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": text},
            ],
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]},
        }
    )


def translate_content(content):
    """What a perfect model answers: every segment, marked, in order."""
    body = re.search(r"```(.*)```", content, re.DOTALL).group(1)
    return BATCH_DELIMITER.join(
        f"ZH<{part.strip()}>" for part in body.split(BATCH_DELIMITER)
    )


class FakeAnthropic:
    """The parts of the SDK client the translator and the queue touch."""

    def __init__(self, answer=None):
        self.api_key = "sk-test"
        self.answer = answer or (
            lambda params: message(translate_content(params["messages"][-1]["content"]))
        )
        self.live = []
        self.created = []
        self.options = []
        self.retrieved = 0
        self.processing_polls = 1
        self.create_error = None
        self.batches_store = {}
        self.messages = SimpleNamespace(
            create=self._create,
            batches=SimpleNamespace(
                create=self._batch_create,
                retrieve=self._retrieve,
                results=self._results,
            ),
        )

    def with_options(self, **options):
        self.options.append(options)
        return self

    def _create(self, **params):
        self.live.append(params)
        return self.answer(params)

    def _batch_create(self, requests):
        self.created.append(requests)
        if self.create_error is not None:
            raise self.create_error
        batch_id = f"msgbatch_{len(self.created)}"
        self.batches_store[batch_id] = requests
        return SimpleNamespace(id=batch_id, processing_status="in_progress")

    def _retrieve(self, batch_id):
        self.retrieved += 1
        if self.processing_polls > 0:
            self.processing_polls -= 1
            counts = SimpleNamespace(processing=1, succeeded=0, errored=0)
            return SimpleNamespace(
                processing_status="in_progress", request_counts=counts
            )
        counts = SimpleNamespace(processing=0, succeeded=1, errored=0)
        return SimpleNamespace(processing_status="ended", request_counts=counts)

    def _results(self, batch_id):
        entries = [
            SimpleNamespace(
                custom_id=request["custom_id"],
                result=SimpleNamespace(
                    type="succeeded", message=self.answer(request["params"])
                ),
            )
            for request in self.batches_store[batch_id]
        ]
        random.Random(7).shuffle(entries)  # results come back in any order
        return iter(entries)


@pytest.fixture
def fake():
    return FakeAnthropic()


def make_loader(tmp_path, fake, text=BOOK):
    book = tmp_path / "book.md"
    book.write_text(text, encoding="utf-8")
    with patch("book_maker.translator.claude_translator.Anthropic", return_value=fake):
        loader = MarkdownBookLoader(
            str(book),
            Claude,
            key="sk-test",
            resume=False,
            language="Simplified Chinese",
        )
    loader.batch_size = 3
    loader.translate_model.set_model_list(["claude-sonnet-5-5"])
    with patch("book_maker.translator.claude_batch.time.sleep", lambda seconds: None):
        attach_message_batches(loader)
    loader.translate_model.batch_queue.sleep = lambda seconds: None
    return loader


def output(tmp_path):
    return (tmp_path / "book_bilingual.md").read_text(encoding="utf-8")


def test_one_batch_answers_the_whole_book_and_nothing_is_sent_live(tmp_path, fake):
    loader = make_loader(tmp_path, fake)
    loader.make_bilingual_book()

    assert len(fake.created) == 1, "one book, one batch"
    assert fake.live == [], "a request the batch answered was also sent live"
    book = output(tmp_path)
    for paragraph in PARAGRAPHS:
        assert paragraph in book
        assert f"ZH<{paragraph}>" in book
    # The create went out once, with the SDK's own retries off.
    assert {"max_retries": 0, "timeout": 600} in fake.options
    state = json.loads(message_batch_state_path(loader).read_text(encoding="utf-8"))
    assert state["submitting"] is None
    assert [batch["id"] for batch in state["batches"]] == ["msgbatch_1"]


def test_each_batch_request_is_the_body_the_live_call_would_send(tmp_path, fake):
    loader = make_loader(tmp_path, fake)
    loader.make_bilingual_book()
    for request in fake.created[0]:
        params = request["params"]
        assert request["custom_id"] == request_key(params)
        assert params["model"] == "claude-sonnet-5-5"
        assert params["max_tokens"] == 4096
        assert "extra_body" not in params


def test_a_misaligned_reply_is_repaired_live_and_the_rest_replayed(tmp_path):
    def answer(params):
        content = params["messages"][-1]["content"]
        if "Paragraph number 1 " in content and BATCH_DELIMITER in content:
            return message("one block where three were asked for")
        return message(translate_content(content))

    fake = FakeAnthropic(answer)
    loader = make_loader(tmp_path, fake)
    loader.make_bilingual_book()

    assert len(fake.created) == 1
    # Only the misaligned group went live, one paragraph at a time.
    assert fake.live, "the bad reply was accepted"
    assert all(BATCH_DELIMITER not in p["messages"][-1]["content"] for p in fake.live)
    assert len(fake.live) <= 3
    book = output(tmp_path)
    assert "one block where three" not in book
    for paragraph in PARAGRAPHS:
        assert f"ZH<{paragraph}>" in book


def test_a_reply_cut_off_at_max_tokens_is_sent_again_live(tmp_path):
    batched = []

    def answer(params):
        content = params["messages"][-1]["content"]
        text = translate_content(content)
        if "Paragraph number 4 " in content and not batched:
            batched.append(True)
            return message(text, stop_reason="max_tokens")
        return message(text)

    fake = FakeAnthropic(answer)
    loader = make_loader(tmp_path, fake)
    loader.make_bilingual_book()
    assert len(fake.created) == 1
    assert len(fake.live) == 1
    assert "Paragraph number 4 " in fake.live[0]["messages"][-1]["content"]
    assert "ZH<Paragraph number 4 of the book, in English.>" in output(tmp_path)


def test_a_rerun_after_the_batch_ended_buys_nothing(tmp_path, fake):
    make_loader(tmp_path, fake).make_bilingual_book()
    first = output(tmp_path)

    again = FakeAnthropic()
    make_loader(tmp_path, again).make_bilingual_book()
    assert again.created == [] and again.live == []
    assert output(tmp_path) == first


def test_a_rerun_polls_the_batch_it_submitted_instead_of_submitting_again(
    tmp_path, fake
):
    loader = make_loader(tmp_path, fake)

    def interrupted(self, client, batch):
        raise KeyboardInterrupt

    with patch.object(ClaudeBatchQueue, "_wait", interrupted):
        with pytest.raises(SystemExit):
            loader.make_bilingual_book()
    assert len(fake.created) == 1

    # The same account, a new process: the batch is still there.
    resumed = FakeAnthropic()
    resumed.batches_store = fake.batches_store
    make_loader(tmp_path, resumed).make_bilingual_book()
    assert resumed.created == [], "a resumed run submitted the book again"
    assert resumed.live == []
    assert resumed.retrieved >= 1
    for paragraph in PARAGRAPHS:
        assert f"ZH<{paragraph}>" in output(tmp_path)


def test_a_create_whose_outcome_is_unknown_is_never_repeated(tmp_path, fake):
    fake.create_error = APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")
    )
    loader = make_loader(tmp_path, fake)
    with pytest.raises(BatchFailed) as unknown:
        loader.make_bilingual_book()
    assert "outcome is unknown" in str(unknown.value)
    assert len(fake.created) == 1
    state = json.loads(message_batch_state_path(loader).read_text(encoding="utf-8"))
    assert state["submitting"]["keys"]

    again = FakeAnthropic()
    with pytest.raises(BatchFailed):
        make_loader(tmp_path, again).make_bilingual_book()
    assert again.created == [] and again.live == []


def test_a_refused_create_is_final_and_leaves_no_marker(tmp_path, fake):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages/batches")
    fake.create_error = BadRequestError(
        "bad", response=httpx.Response(400, request=request), body=None
    )
    loader = make_loader(tmp_path, fake)
    with pytest.raises(BatchFailed) as refused:
        loader.make_bilingual_book()
    assert "refused" in str(refused.value)
    state = json.loads(message_batch_state_path(loader).read_text(encoding="utf-8"))
    assert state["submitting"] is None and state["batches"] == []


def test_usage_is_counted_once_when_the_batch_is_read(tmp_path, fake):
    loader = make_loader(tmp_path, fake)
    loader.make_bilingual_book()
    first = loader.translate_model.usage.summary()
    assert first

    again = make_loader(tmp_path, FakeAnthropic())
    again.make_bilingual_book()
    assert not again.translate_model.usage.summary(), "a replay was billed again"


def test_a_state_file_from_another_endpoint_is_refused(tmp_path):
    path = tmp_path / "state.claude-batch.json"
    ClaudeBatchQueue(path, endpoint="https://a.example", model="m")._save()
    with pytest.raises(BatchFailed):
        ClaudeBatchQueue(path, endpoint="https://b.example", model="m")


def test_the_body_is_the_sdk_merge_and_its_key_ignores_key_order():
    params = {
        "model": "m",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "x"}],
        "extra_body": {"output_config": {"effort": "low"}},
        "system": None,
    }
    body = effective_body(params)
    assert body == {
        "model": "m",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "x"}],
        "output_config": {"effort": "low"},
    }
    reordered = dict(reversed(list(body.items())))
    assert request_key(reordered) == request_key(body)
    assert re.fullmatch(r"[0-9a-f]{64}", request_key(body))


def test_the_collection_pass_records_and_never_sends(tmp_path):
    queue = ClaudeBatchQueue(tmp_path / "s.json", endpoint="e", model="m")
    with pytest.raises(BatchPending):
        queue.request({"model": "m", "messages": [], "max_tokens": 1})
    assert len(queue.pending) == 1
    # BatchPending is not an Exception: no `except Exception` can swallow it.
    assert not issubclass(BatchPending, Exception)


def test_parallel_workers_do_not_replay_the_batch_concurrently(
    tmp_path, fake, monkeypatch
):
    """The shared translator swaps its prompt per request: a worker entering
    mid-swap builds a body the batch never saw and pays for it live."""

    class NoPool:
        def __init__(self, *args, **kwargs):
            raise AssertionError("a batch replay ran on worker threads")

    monkeypatch.setattr("book_maker.loader.md_loader.ThreadPoolExecutor", NoPool)
    loader = make_loader(tmp_path, fake)
    loader.parallel_workers = 2
    loader.make_bilingual_book()
    assert len(fake.created) == 1 and fake.live == []
