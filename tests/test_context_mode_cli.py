"""`--use_context` grew an optional value; every old command line must still mean what it meant.

The back-compat case is the one that matters: bare `--use_context` has been in
READMEs and shell scripts for years, and it has to keep selecting window mode
with the same paragraph limit as before.
"""

import pytest

from book_maker.cli import parse_args, resolve_context_mode


@pytest.fixture(autouse=True)
def _no_backoff_naps(monkeypatch):
    """Sit out none of the retry waits.

    `TestUnsupportedLoaderWarning` drives the real `main()` to the point of
    the warning it is about and then lets the run fall over on an
    unreachable endpoint. The falling over is patient by design, so the
    test was spending 5s in `tenacity.nap` after its assertion was already
    decided. Every stop is `stop_after_attempt`, so the attempt count and
    the printed lines do not move.
    """
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _seconds: None)


def _parse(*args):
    return parse_args(["--book_name", "book.epub", *args])


class TestUseContextValue:
    def test_absent_means_no_context(self):
        options = _parse()
        assert options.context_mode is None
        assert resolve_context_mode(options) == (False, None)

    def test_bare_flag_still_means_window(self):
        options = _parse("--use_context")
        assert resolve_context_mode(options) == (True, "window")

    def test_explicit_window(self):
        assert resolve_context_mode(_parse("--use_context", "window")) == (
            True,
            "window",
        )

    def test_session_mode(self):
        assert resolve_context_mode(_parse("--use_context", "session")) == (
            True,
            "session",
        )

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(SystemExit):
            _parse("--use_context", "nonsense")

    def test_paragraph_limit_still_parses_alongside(self):
        options = _parse("--use_context", "--context_paragraph_limit", "5")
        assert options.context_paragraph_limit == 5

    def test_flag_does_not_swallow_a_following_option(self):
        options = _parse("--use_context", "--single_translate")
        assert resolve_context_mode(options) == (True, "window")
        assert options.single_translate is True


class TestCompactBudgetFlag:
    def test_defaults_to_unset_so_the_model_decides(self):
        assert _parse("--use_context", "session").context_compact_at is None

    def test_explicit_value_is_kept(self):
        options = _parse("--use_context", "session", "--context-compact-at", "2500")
        assert options.context_compact_at == 2500

    def test_rejects_a_non_integer(self):
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "lots")

    def test_rejects_zero(self):
        """It once meant "size it from the model's own window"; too few
        endpoints answered for that to be a setting anyone could rely on."""
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "0")

    def test_rejects_a_negative_budget(self):
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "-1")

    def test_rejects_a_budget_too_small_to_hold_a_paragraph(self):
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "50")

    def test_min_compact_budget_floor(self, capsys):
        """The floor is 1500 (owner ruling 260913, raised from 500), and it
        is a hard refusal rather than a clamp — the same treatment the old
        floor gave, so a run never quietly uses a budget nobody typed.

        Not a measured cost knee, and the message must not imply one: the
        260913 cost curve was flat from 500 to 3000. The case is what a
        window is for — at 1500 a ~300-token handoff seed is already a fifth
        of it — so the refusal points at window mode, which is what an
        operator wanting less context than this actually wants.
        """
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "1499")
        message = capsys.readouterr().err
        assert "1500" in message
        assert "--use_context" in message

        assert _parse("--context-compact-at", "1500").context_compact_at == 1500


class TestNoContextCompactFlag:
    """`--no-context-compact`: keep the seam, drop the report it pays for."""

    def test_defaults_off(self):
        assert _parse().no_context_compact is False

    def test_can_be_enabled(self):
        assert _parse("--no-context-compact").no_context_compact is True


class TestSessionOnlyWarnings:
    """`--context-compact-at` and `--no-context-compact` both need a context
    window.

    Session mode is one. So is the codex format, whose thread *is* the window
    and which compacts whether or not --use_context was passed — warning there
    would claim a flag was ignored when it was obeyed.
    """

    def _warnings(self, capsys, *args, book):
        """Run the CLI far enough to emit the warning, then let it fail.

        The warning is printed before the book is opened, so a placeholder
        file is enough — whatever it raises afterwards is not the point.
        """
        import sys
        from unittest.mock import patch

        from book_maker.cli import main

        argv = ["make_book.py", "--book_name", book, *args]
        with patch.object(sys, "argv", argv):
            try:
                main()
            except BaseException:
                pass
        return capsys.readouterr().out

    def test_warns_for_no_context_compact_outside_session(self, capsys, tmp_path):
        book = tmp_path / "b.epub"
        book.write_bytes(b"not really an epub")
        out = self._warnings(
            capsys, "--no-context-compact", "--api_format", "google", book=str(book)
        )
        assert "--no-context-compact only applies" in out

    def test_warns_for_a_plain_openai_run(self, capsys, tmp_path):
        book = tmp_path / "b.epub"
        book.write_bytes(b"not really an epub")
        out = self._warnings(
            capsys,
            "--context-compact-at",
            "2500",
            "--api_format",
            "google",
            book=str(book),
        )
        assert "only applies" in out

    def test_does_not_warn_for_codex(self, capsys, tmp_path):
        book = tmp_path / "b.epub"
        book.write_bytes(b"not really an epub")
        out = self._warnings(
            capsys,
            "--context-compact-at",
            "2500",
            "--api_format",
            "codex",
            book=str(book),
        )
        assert "only applies" not in out


class TestUnsupportedLoaderWarning:
    """txt and srt never hand context to the model, so a session budget
    is reported as ignored rather than silently dropped."""

    def _warn(self, capsys, tmp_path, *args):
        import sys
        from unittest.mock import patch

        from book_maker.cli import main

        book = tmp_path / "b.txt"
        book.write_text("hello world\n", encoding="utf-8")
        argv = [
            "make_book.py",
            "--book_name",
            str(book),
            "--key",
            "x",
            "--model",
            "gpt-4o-mini",
            *args,
        ]
        with patch.object(sys, "argv", argv):
            try:
                main()
            except BaseException:
                pass
        return capsys.readouterr().out

    def test_session_on_a_txt_book_is_reported_as_ignored(self, capsys, tmp_path):
        out = self._warn(capsys, tmp_path, "--use_context", "session")
        assert "--use_context session is not supported" in out
