"""The retry on the legacy single-paragraph path: patient, but not blind.

Owner ruling (260907). This tool's users run flaky providers where a 429 or a
first token can be minutes or hours away, so the retry has **no attempt cap
and no total-time cap**: it waits a failure out for as long as the failure
might clear. What the previous `backoff.on_exception(backoff.expo, Exception)`
got wrong was not the patience — it was retrying the errors that never clear.
A rejected key or a model that does not exist retried forever with doubling
waits, and the run neither finished nor stopped.

So: everything is retried except a known-fatal class, waits grow to a cap and
stay there, and every retry says so — an hours-long tolerance that prints
nothing is indistinguishable from a hang.

`backoff` itself was archived upstream in 2025 with its last release in 2022,
which is why this rides on `tenacity`, already a direct dependency here.
"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from tenacity import stop_never

from book_maker.loader.helper import (
    FATAL_ERROR_NAMES,
    RETRY_WAIT_CAP,
    EPUBBookLoaderHelper,
)

ROOT = Path(__file__).resolve().parents[1]


class AuthenticationError(Exception):
    """Named as the provider SDKs name it — the match is on the class name."""


class RateLimitError(Exception):
    """The one that must be waited out, however long it takes."""


class Flaky:
    """Fails `failures` times, then answers."""

    def __init__(self, failures, error=RateLimitError("slow down")):
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
    """The waits are real minutes; what they are is asserted separately."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


class TestItWaitsOutWhatMightClear:
    def test_it_retries_well_past_three_attempts_and_succeeds(self):
        # the number that used to end the run. Nothing ends it now but an
        # answer or a fatal error.
        model = Flaky(failures=12)
        assert _helper(model).translate_with_backoff("one") == "译one"
        assert model.calls == 13

    def test_a_first_try_success_does_not_retry(self):
        model = Flaky(failures=0)
        assert _helper(model).translate_with_backoff("one") == "译one"
        assert model.calls == 1

    def test_there_is_no_attempt_cap_at_all(self):
        assert EPUBBookLoaderHelper.translate_with_backoff.retry.stop is stop_never


class TestItGivesUpOnlyOnWhatWillNotClear:
    def test_an_auth_error_raises_on_the_first_attempt(self):
        model = Flaky(failures=99, error=AuthenticationError("incorrect api key"))
        with pytest.raises(AuthenticationError, match="incorrect api key"):
            _helper(model).translate_with_backoff("one")
        assert model.calls == 1

    @pytest.mark.parametrize("name", sorted(FATAL_ERROR_NAMES))
    def test_every_fatal_class_stops_immediately(self, name):
        fatal = type(name, (Exception,), {})
        model = Flaky(failures=99, error=fatal("no"))
        with pytest.raises(fatal):
            _helper(model).translate_with_backoff("one")
        assert model.calls == 1

    def test_the_endpoints_own_error_is_what_the_caller_sees(self):
        # not a tenacity wrapper: the endpoint's sentence is the only thing
        # that says what went wrong
        NotFoundError = type("NotFoundError", (Exception,), {})
        model = Flaky(failures=99, error=NotFoundError("no such model: gpt-9"))
        with pytest.raises(NotFoundError, match="no such model: gpt-9"):
            _helper(model).translate_with_backoff("one")

    def test_a_keyboard_interrupt_is_never_swallowed(self):
        # Ctrl-C during an hours-long wait has to stop the run
        class Interrupted:
            def translate(self, text, context_flag=False):
                raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            _helper(Interrupted()).translate_with_backoff("one")


class TestTheWaitsAreBoundedEvenIfTheAttemptsAreNot:
    WAIT = EPUBBookLoaderHelper.translate_with_backoff.retry.wait

    @pytest.mark.parametrize("attempt", [1, 2, 5, 20, 200])
    def test_no_single_wait_exceeds_the_cap(self, attempt):
        assert 0 < self.WAIT(SimpleNamespace(attempt_number=attempt)) <= RETRY_WAIT_CAP

    def test_the_wait_grows_before_it_flattens(self):
        early = self.WAIT(SimpleNamespace(attempt_number=1))
        later = self.WAIT(SimpleNamespace(attempt_number=6))
        assert early < later

    def test_it_flattens_at_the_cap_rather_than_doubling_forever(self):
        assert self.WAIT(SimpleNamespace(attempt_number=40)) == RETRY_WAIT_CAP


def test_every_retry_says_the_class_and_the_next_wait(caplog):
    # an hours-long tolerance that prints nothing looks exactly like a hang
    model = Flaky(failures=2)
    with caplog.at_level(logging.WARNING, logger="book_maker.loader.helper"):
        _helper(model).translate_with_backoff("one")
    lines = [record.getMessage() for record in caplog.records]
    assert len(lines) == 2
    assert "RateLimitError" in lines[0] and "slow down" in lines[0]
    assert "waiting" in lines[0]


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
