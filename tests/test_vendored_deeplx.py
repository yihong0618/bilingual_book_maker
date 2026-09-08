"""The vendored DeepL free client: the request it makes, and no package.

`PyDeepLX` was archived upstream (202 stars, last release 2024-02) and this
project called one function of it. The copy in `book_maker/vendor/deeplx.py`
keeps its MIT licence and its request shape — the shape is the load-bearing
part, because it imitates DeepL's iOS app and the endpoint answers on that
basis, down to the whitespace after `"method":`.
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from book_maker.vendor import deeplx

ROOT = Path(__file__).resolve().parents[1]


class Recorder:
    """Stands in for `requests.post` and records the call."""

    def __init__(self, payload=None, status_code=200):
        self.payload = (
            payload
            if payload is not None
            else {"result": {"texts": [{"text": "hello", "alternatives": []}]}}
        )
        self.status_code = status_code
        self.call = None

    def __call__(self, url, **kwargs):
        self.call = SimpleNamespace(url=url, **kwargs)
        payload = self.payload
        return SimpleNamespace(
            status_code=self.status_code,
            json=lambda: payload,
            text=json.dumps(payload),
        )


@pytest.fixture
def post(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(deeplx.requests, "post", recorder)
    return recorder


class TestTheRequestShape:
    def test_it_posts_the_jsonrpc_call_to_deepls_web_endpoint(self, post):
        deeplx.translate("你好", "ZH", "EN")
        assert post.call.url == "https://www2.deepl.com/jsonrpc"
        body = json.loads(post.call.data)
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "LMT_handle_texts"
        assert body["params"]["texts"] == [{"text": "你好", "requestAlternatives": 0}]
        assert body["params"]["lang"] == {
            "source_lang_user_selected": "ZH",
            "target_lang": "EN",
        }
        assert body["params"]["splitting"] == "newlines"

    def test_the_headers_imitate_the_ios_app(self, post):
        deeplx.translate("hi")
        assert post.call.headers["User-Agent"] == (
            "DeepL-iOS/2.9.1 iOS 16.3.0 (iPhone13,2)"
        )
        assert post.call.headers["x-app-device"] == "iPhone13,2"
        assert post.call.headers["Content-Type"] == "application/json"

    def test_the_body_is_utf8_bytes(self, post):
        # the header promises json; a non-ASCII paragraph must not be handed
        # to the transport as text for it to guess an encoding for
        deeplx.translate("动物庄园")
        assert isinstance(post.call.data, bytes)
        assert "动物庄园" in post.call.data.decode("utf-8")

    def test_the_method_key_keeps_its_hand_written_spacing(self, post):
        # not cosmetic: the endpoint reads it as a fingerprint of its client.
        # `json.dumps` writes `"method":"`, and upstream rewrites it to one of
        # two spacings chosen by the request id — never leaves it as dumped.
        deeplx.translate("hi")
        raw = post.call.data.decode("utf-8")
        assert '"method":"' not in raw
        assert '"method": "' in raw or '"method" : "' in raw

    def test_the_request_cannot_hang_the_run(self, post):
        # `requests` waits forever by default and `httpx`, which this code
        # used before it was vendored, waits five seconds. An undocumented
        # endpoint that stops answering must not hang a book on one paragraph.
        deeplx.translate("hi")
        assert post.call.timeout == deeplx.TIMEOUT == (5, 5)

    def test_the_translation_is_returned(self, post):
        assert deeplx.translate("hi") == "hello"

    def test_alternatives_come_back_as_a_list(self, monkeypatch):
        recorder = Recorder(
            {"result": {"texts": [{"text": "a", "alternatives": [{"text": "b"}]}]}}
        )
        monkeypatch.setattr(deeplx.requests, "post", recorder)
        assert deeplx.translate("hi", numberAlternative=2) == ["b"]


class TestWhatTheEndpointRefuses:
    def test_a_429_is_the_ip_block_it_names(self, monkeypatch):
        monkeypatch.setattr(deeplx.requests, "post", Recorder(status_code=429))
        with pytest.raises(deeplx.TooManyRequestsException) as blocked:
            deeplx.translate("hi")
        assert "blocked by DeepL" in str(blocked.value)

    def test_any_other_failure_answers_none_as_upstream_did(self, monkeypatch, capsys):
        monkeypatch.setattr(deeplx.requests, "post", Recorder(status_code=503))
        assert deeplx.translate("hi") is None
        assert "503" in capsys.readouterr().out


class TestTheTimestampRule:
    """Upstream's own arithmetic, kept because the endpoint checks it."""

    def test_a_text_with_no_i_gets_a_plain_timestamp(self):
        assert deeplx.get_i_count("abc") == 0

    def test_the_timestamp_is_a_multiple_of_i_count_plus_one(self):
        ts = deeplx.get_timestamp(3)
        assert ts % 4 == 0

    def test_the_request_id_sits_in_the_range_the_client_uses(self):
        assert 8300000_000 <= deeplx.get_random_number() <= 8399998_000


class TestThePackageIsGone:
    def test_the_route_imports_the_vendored_module(self):
        source = (ROOT / "book_maker/translator/deepl_free_translator.py").read_text(
            encoding="utf-8"
        )
        assert "PyDeepLX" not in source

    def test_nothing_imports_it(self):
        for path in (ROOT / "book_maker").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            assert "PyDeepLX" not in names, path

    def test_it_is_not_declared_anywhere(self):
        for name in ("pyproject.toml", "requirements.txt"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "PyDeepLX" not in text and "pydeeplx" not in text.lower(), name

    def test_the_licence_travelled_with_the_code(self):
        source = (ROOT / "book_maker/vendor/deeplx.py").read_text(encoding="utf-8")
        assert "MIT License" in source
        assert "Copyright (c) 2023 OwO Network Limited" in source
        assert "https://github.com/OwO-Network/PyDeepLX" in source
