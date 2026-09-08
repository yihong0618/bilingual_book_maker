"""The retry on the legacy single-paragraph path: it must give up.

This decorator was the only unbounded retry in the codebase —
`backoff.on_exception(backoff.expo, Exception)` with no `max_tries` and no
`max_time`, and no test of any kind. A permanent failure on this path (a
rejected key, a model id that does not exist) retried forever with doubling
waits: the run neither finished nor stopped, and nothing on screen said why.

`backoff` itself was archived upstream in 2025 with its last release in 2022,
so the bound arrives by swapping it for `tenacity`, which six other sites here
already use at `stop_after_attempt(3)`.
"""

import ast
from pathlib import Path

import pytest

from book_maker.loader.helper import EPUBBookLoaderHelper

ROOT = Path(__file__).resolve().parents[1]


class Flaky:
    """Fails `failures` times, then answers."""

    def __init__(self, failures, error=RuntimeError("endpoint said no")):
        self.failures = failures
        self.error = error
        self.calls = 0

    def translate(self, text, context_flag=False):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return f"译{text}"


def _helper(model):
    return EPUBBookLoaderHelper(model, 1, "", False)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The waits are real seconds; the bound is what is under test."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


def test_a_transient_failure_is_retried_and_then_succeeds():
    model = Flaky(failures=2)
    assert _helper(model).translate_with_backoff("one") == "译one"
    assert model.calls == 3


def test_a_permanent_failure_gives_up_at_the_third_attempt():
    # the whole point: it stops. Unbounded, this call never returned.
    model = Flaky(failures=99)
    with pytest.raises(RuntimeError):
        _helper(model).translate_with_backoff("one")
    assert model.calls == 3


def test_the_endpoints_own_error_is_what_the_caller_sees():
    # `reraise`: a tenacity RetryError in its place would hide the sentence
    # the endpoint sent, which is the only thing that says what went wrong
    model = Flaky(failures=99, error=ValueError("no such model: gpt-9"))
    with pytest.raises(ValueError, match="no such model: gpt-9"):
        _helper(model).translate_with_backoff("one")


def test_a_first_try_success_does_not_retry():
    model = Flaky(failures=0)
    assert _helper(model).translate_with_backoff("one") == "译one"
    assert model.calls == 1


class TestTheArchivedPackageIsGone:
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
            assert "backoff" not in names, path

    def test_it_is_not_declared_anywhere(self):
        for name in ("pyproject.toml", "requirements.txt"):
            assert "backoff" not in (ROOT / name).read_text(encoding="utf-8"), name
