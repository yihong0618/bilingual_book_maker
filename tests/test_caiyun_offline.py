"""The caiyun route end to end, with only its HTTP request answered offline.

CI used to run `make_book.py --api_format caiyun` against the live Caiyun
API on every push to main, so the service's reply decided the result (one
answer without `target` failed the run). This drives the same path --
argument parsing, the caiyun translator, the TXT loader's batching, the
bilingual file -- and answers each request from a fixed script, so the
result depends on this code alone. Whether the live service still accepts
the request is the manual `live-smokes` job's question.
"""

import json

import pytest

from book_maker import cli
from book_maker.translator import caiyun_translator

KEY = "caiyun-offline-key"
URL = "https://api.interpreter.caiyunai.com/v1/translator"

BOOK = [
    "1",
    "The first line of chapter one.",
    "A second line.",
    "",
    "",
    "",
    "The seventh line.",
    "The eighth line.",
    "The ninth line, past the sample.",
]

# What the loader must send with --batch_size 3 --test_num 8, and what the
# service answers. The blank batch (lines 4-6) is never sent, and line 9 is
# past the sample. Caiyun drops a leading chapter number from its reply
# (issue #279); the translator puts it back.
EXCHANGES = [
    (
        "1\nThe first line of chapter one.\nA second line.",
        "第一章的第一行。\n第二行。",
    ),
    ("The seventh line.\nThe eighth line.", "第七行。\n第八行。"),
]

EXPECTED_BOOK = "\n".join(
    [
        "1\nThe first line of chapter one.\nA second line.",
        "1\n第一章的第一行。\n第二行。",
        "The seventh line.\nThe eighth line.",
        "第七行。\n第八行。",
    ]
)


class Reply:
    def __init__(self, body):
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False)

    def json(self):
        return self._body


@pytest.fixture
def service(monkeypatch):
    script = list(EXCHANGES)
    sent = []

    def request(method, url, data=None, headers=None, **kwargs):
        assert script, f"a request past the script: {data}"
        source, target = script.pop(0)
        assert method == "POST"
        assert url == URL
        # A bare comparison would print a real token in the diff if one leaked in.
        carried_key = headers.get("x-authorization") == f"token {KEY}"
        assert carried_key, "the request did not carry the test key"
        assert headers["content-type"] == "application/json"
        payload = json.loads(data)
        assert payload == {
            "source": source,
            "trans_type": "auto2zh",
            "request_id": "demo",
            "detect": True,
        }
        sent.append(payload["source"])
        return Reply({"target": target})

    def no_wait(seconds):
        raise AssertionError(f"the translator waited {seconds}s to retry")

    monkeypatch.setattr(caiyun_translator.requests, "request", request)
    monkeypatch.setattr(caiyun_translator.time, "sleep", no_wait)
    yield sent
    assert not script, f"requests never sent: {script}"


def test_a_txt_book_is_translated_batch_by_batch(tmp_path, monkeypatch, service):
    # The CLI reads BBM_API_KEY before the route's own variable: a key
    # exported in the caller's shell must not decide this test.
    monkeypatch.delenv("BBM_API_KEY", raising=False)
    monkeypatch.setenv("BBM_CAIYUN_API_KEY", KEY)
    book = tmp_path / "book.txt"
    book.write_text("\n".join(BOOK), encoding="utf-8")

    cli.main(
        [
            "--book_name",
            str(book),
            "--test",
            "--batch_size",
            "3",
            "--test_num",
            "8",
            "--api_format",
            "caiyun",
        ]
    )

    assert service == [source for source, _ in EXCHANGES]
    written = (tmp_path / "book_bilingual.txt").read_text(encoding="utf-8")
    assert written == EXPECTED_BOOK
