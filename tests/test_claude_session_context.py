"""`--use_context` on the anthropic route: the window that kept nothing, and session mode.

Two things were silently dead here. `--use_context session` was never read by
this class at all, so the mode the plan skill defaults to degraded to window
mode without a word; and window mode itself kept zero pairs, because the CLI
passes `--context_paragraph_limit 0` and `save_context` appended a pair and
popped it again on the same call. Both are covered below, along with the
session-mode invariant that pays for the mode — the prefix of request N+1 must
be request N's messages verbatim, or the endpoint's cache misses and session
mode costs more than the mode it replaces.

Every request here is a stub. There is no anthropic key in this environment
and none of these tests may acquire one.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from book_maker.session_context import handoff_prompt
from book_maker.translator.claude_translator import Claude, _strip_outer_fence

# A phrase unique to the compact turn, taken from the real prompt so the tests
# cannot drift from it.
HANDOFF_MARKER = handoff_prompt()[:40]


def _message(text, cache_read=0):
    """What `client.messages.create` answers with."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(
            input_tokens=100,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=0,
        ),
    )


def _translator(replies=None, cache_read=0, **kwargs):
    """A Claude wired to a scripted client. No network, real __init__."""
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    with patch("book_maker.translator.claude_translator.Anthropic"):
        t = Claude("k", "Chinese", **kwargs)

    sent = []
    answers = iter(replies or [])

    def create(**call):
        sent.append(call)
        try:
            reply = next(answers)
        except StopIteration:
            reply = "译文"
        return _message(reply, cache_read)

    t.client = SimpleNamespace(
        messages=SimpleNamespace(create=Mock(side_effect=create)),
    )
    t.sent = sent
    return t


def _prefix(call):
    """Everything but the final (fresh) user message."""
    return call["messages"][:-1]


class TestWindowModeKeepsSomething:
    """`--context_paragraph_limit` defaults to 0, and 0 used to mean nothing."""

    def test_a_zero_limit_keeps_the_default_three_pairs(self):
        t = _translator(context_mode="window", context_paragraph_limit=0)
        for text in ("one", "two", "three", "four"):
            t.save_context(text, "译文")
        assert len(t.context_list) == 3
        assert t.context_list == ["two", "three", "four"]

    def test_an_explicit_limit_is_still_honoured(self):
        t = _translator(context_mode="window", context_paragraph_limit=2)
        for text in ("one", "two", "three", "four"):
            t.save_context(text, "译文")
        assert t.context_list == ["three", "four"]

    def test_the_kept_pair_reaches_the_next_request(self):
        t = _translator(["一"], context_mode="window", context_paragraph_limit=0)
        t.translate("one")
        t.translate("two")
        contents = [m["content"] for m in t.sent[1]["messages"]]
        assert any("one" in c for c in contents)
        assert "一" in contents


class TestSessionHistoryGrows:
    def test_session_mode_is_off_unless_it_is_asked_for(self):
        assert _translator(context_mode="window").session is None
        assert _translator(context_flag=False).session is None

    def test_four_saves_leave_four_pairs(self):
        t = _translator()
        for i in range(4):
            t.save_context(f"unit {i}", f"译文 {i}")
        assert len(t.session.messages()) == 8

    def test_the_next_request_carries_the_prior_pairs(self):
        t = _translator(["一", "二"])
        t.translate("one")
        t.translate("two")
        contents = [m["content"] for m in t.sent[1]["messages"]]
        assert any("one" in c for c in contents)
        assert "一" in contents

    def test_history_accumulates_beyond_the_window_limit(self):
        """Window mode caps at context_paragraph_limit pairs; session does not."""
        t = _translator(["1", "2", "3", "4", "5"], context_paragraph_limit=2)
        for i in range(5):
            t.translate(f"unit {i}")
        assert sum("unit" in m["content"] for m in t.sent[-1]["messages"]) >= 4


class TestPrefixStability:
    def test_each_request_replays_the_previous_one_exactly(self):
        """Storing anything but the text actually sent leaves the newest pair
        out of the cached prefix, so every request re-reads a paragraph at
        full input price for the life of the run."""
        t = _translator(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.translate(text)
        for earlier, later in zip(t.sent, t.sent[1:]):
            sent_before = earlier["messages"]
            assert later["messages"][: len(sent_before)] == sent_before

    def test_the_system_prompt_is_byte_identical_across_requests(self):
        t = _translator(["一", "二"], prompt_sys_msg="be terse")
        t.translate("one")
        t.translate("two")
        assert t.sent[0]["system"] == t.sent[1]["system"] == "be terse"


class TestPromptCaching:
    """Anthropic caches nothing unless asked, and unasked session mode is
    strictly worse than the window mode it replaces."""

    def test_a_session_request_asks_for_a_cache_breakpoint(self):
        t = _translator()
        t.translate("one")
        assert t.sent[0]["cache_control"] == {"type": "ephemeral"}

    def test_window_mode_asks_for_none(self):
        t = _translator(context_mode="window")
        t.translate("one")
        assert "cache_control" not in t.sent[0]

    def test_cache_reads_are_metered_not_judged(self):
        # the guard that warned after ten uncached requests is gone; the
        # loader pins in/out/cached on the bar and the operator decides
        t = _translator(["一"] * 3, cache_read=64)
        for i in range(3):
            t.translate(f"unit {i}")
        assert t.usage.requests == 3 and t.usage.cached == 192
        assert t.usage_postfix()["cached"] == "192"

    def test_window_mode_is_metered_the_same_way(self):
        t = _translator(["一"] * 3, context_mode="window")
        for i in range(3):
            t.translate(f"unit {i}")
        assert t.usage.requests == 3 and t.usage.cached == 0


class TestCompact:
    def test_it_compacts_when_the_budget_is_reached(self, tmp_path):
        t = _translator(
            ["译文", "Summary: they walked."],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.translate("a" * 200)
        assert len(t.sent) == 2  # the translation, then the handoff turn
        assert HANDOFF_MARKER in t.sent[1]["messages"][-1]["content"]

    def test_the_handoff_turn_carries_the_history_it_condenses(self, tmp_path):
        t = _translator(
            ["译文", "Summary."],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.translate("a" * 200)
        assert any("a" * 200 in m["content"] for m in _prefix(t.sent[1]))

    def test_the_next_window_is_seeded_with_the_report(self, tmp_path):
        t = _translator(
            ["译文", "Summary: they walked.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.translate("a" * 200)
        t.translate("b" * 200)
        seeded = [m["content"] for m in t.sent[-1]["messages"]]
        assert any("they walked" in c for c in seeded)

    def test_the_history_shrinks_after_a_compact(self, tmp_path):
        t = _translator(
            ["译文", "Summary.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.translate("a" * 400)
        long_prefix = len(_prefix(t.sent[-1]))
        t.translate("b" * 400)
        assert len(_prefix(t.sent[-1])) <= long_prefix + 1

    def test_the_report_is_persisted(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        t = _translator(
            ["译文", "Summary: they walked."], context_compact_at=10, handoff_path=path
        )
        t.translate("a" * 200)
        assert "they walked" in path.read_text(encoding="utf-8")

    def test_window_mode_never_compacts(self, tmp_path):
        t = _translator(["译文"] * 4, context_mode="window", context_compact_at=10)
        t.translate("a" * 400)
        t.translate("b" * 400)
        assert len(t.sent) == 2


class TestCompactResilience:
    """A compact failure must not throw away the book's accumulated context."""

    # Each unit below is ~200 estimated tokens, so the 600-token budget trips
    # after three of them and the window stays well inside the "give up, it
    # will only keep failing" size guard.
    BUDGET = 600
    UNIT = "x" * 800

    def _failing(self, tmp_path, failures):
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=tmp_path / "h.md",
        )
        real = t.client.messages.create
        state = {"left": failures}

        def create(**call):
            if HANDOFF_MARKER in call["messages"][-1]["content"] and state["left"] > 0:
                state["left"] -= 1
                raise RuntimeError("boom")
            return real(**call)

        t.client.messages.create = Mock(side_effect=create)
        return t

    def test_a_transient_failure_keeps_the_history(self, tmp_path):
        t = self._failing(tmp_path, failures=1)
        for _ in range(3):
            t.translate(self.UNIT)
        assert t.session.messages(), "history was discarded on one failed compact"

    def test_it_retries_the_compact_on_the_next_unit(self, tmp_path):
        t = self._failing(tmp_path, failures=1)
        for _ in range(4):
            t.translate(self.UNIT)
        assert (tmp_path / "h.md").exists(), "the retry never produced a report"

    def test_it_gives_up_loudly_rather_than_growing_forever(self, tmp_path, capsys):
        t = self._failing(tmp_path, failures=99)
        for _ in range(10):
            t.translate(self.UNIT)
        assert "handoff report failed" in capsys.readouterr().out.lower()
        assert t.session.estimated_tokens() <= 2 * self.BUDGET

    def test_a_failed_handoff_write_does_not_fail_the_translation(self, tmp_path):
        # A directory where the handoff file should be: writing it must fail.
        path = tmp_path / "h.md"
        path.mkdir()
        t = _translator(["译文", "Summary."], context_compact_at=10, handoff_path=path)
        assert t.translate("a" * 200) == "译文"


class _Refused(Exception):
    """An endpoint turning a request down outright, the way a 400 does."""

    status_code = 400


class _RateLimited(Exception):
    """The failure the compact retry was built for: it clears on its own."""

    status_code = 429


class TestEmptyHandoffReport:
    """A 200 carrying no text is a failed compact, not a successful one.

    Nothing raises, so this used to reset the window and seed it with the
    empty string — the accumulated context discarded, and nothing handed on
    in its place. An empty or tool-only content list from a gateway is all it
    takes.
    """

    BUDGET = 300
    UNIT = "x" * 800

    def _mute(self, tmp_path, path=None):
        """A translator whose compact turn answers with no text blocks."""
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=path or (tmp_path / "h.md"),
        )
        real = t.client.messages.create

        def create(**call):
            if HANDOFF_MARKER in call["messages"][-1]["content"]:
                return SimpleNamespace(content=[])
            return real(**call)

        t.client.messages.create = Mock(side_effect=create)
        return t

    def test_it_keeps_the_window(self, tmp_path):
        # Not merely "the history is non-empty": the discarded window was
        # reseeded with the report's own boilerplate, which is non-empty and
        # carries nothing of the book. The units themselves have to be there.
        t = self._mute(tmp_path)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        kept = [m["content"] for m in t.session.messages()]
        assert sum(self.UNIT in c for c in kept) == 2

    def test_it_counts_as_a_failure(self, tmp_path):
        t = self._mute(tmp_path)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        assert t._compact_failures == 1

    def test_it_says_so(self, tmp_path, capsys):
        t = self._mute(tmp_path)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        assert "handoff report failed" in capsys.readouterr().out.lower()

    def test_it_is_not_printed_as_a_report(self, tmp_path, capsys):
        t = self._mute(tmp_path)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        assert "handoff report, window" not in capsys.readouterr().out

    def test_it_writes_no_handoff_file(self, tmp_path):
        path = tmp_path / "h.md"
        t = self._mute(tmp_path, path=path)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        assert not path.exists()

    def test_a_whitespace_only_report_is_no_better(self, tmp_path):
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=tmp_path / "h.md",
        )
        real = t.client.messages.create

        def create(**call):
            if HANDOFF_MARKER in call["messages"][-1]["content"]:
                return _message("   \n  ")
            return real(**call)

        t.client.messages.create = Mock(side_effect=create)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        kept = [m["content"] for m in t.session.messages()]
        assert sum(self.UNIT in c for c in kept) == 2

    def test_it_still_gives_up_rather_than_growing_forever(self, tmp_path):
        t = self._mute(tmp_path)
        for _ in range(8):
            t.translate(self.UNIT)
        assert t.session.estimated_tokens() <= 2 * self.BUDGET


class TestOversizedHistoryDoesNotWedge:
    """A compact refused for its size must not be retried — it wedges the run.

    The retry is deferred to the next paragraph, and that paragraph's request
    carries the same history plus the paragraph, so on an endpoint whose limit
    the history has already passed it is refused first. COMPACT_ATTEMPTS was
    never reached, the `2 * budget` guard never tripped, and the run could not
    advance at all.
    """

    BUDGET = 300
    UNIT = "x" * 800
    # Characters this endpoint accepts in one request. Wide enough for two
    # units and their history, too narrow for that history plus the compact
    # turn's own prompt.
    LIMIT = 2000

    def _endpoint(self, tmp_path, error):
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=tmp_path / "h.md",
        )
        real = t.client.messages.create

        def create(**call):
            if sum(len(m["content"]) for m in call["messages"]) > self.LIMIT:
                raise error
            return real(**call)

        t.client.messages.create = Mock(side_effect=create)
        return t

    def test_a_refused_compact_starts_the_next_window(self, tmp_path):
        t = self._endpoint(tmp_path, _Refused("prompt is too long"))
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        assert t.session.messages() == []

    def test_the_run_still_advances(self, tmp_path):
        t = self._endpoint(tmp_path, _Refused("prompt is too long"))
        assert [t.translate(self.UNIT) for _ in range(6)] == ["译文"] * 6

    def test_the_message_alone_is_enough_to_recognise_it(self, tmp_path):
        """A gateway need not raise the SDK's error type to be understood."""
        boom = RuntimeError("prompt is too long: 210000 tokens > 200000 maximum")
        t = self._endpoint(tmp_path, boom)
        assert [t.translate(self.UNIT) for _ in range(6)] == ["译文"] * 6

    def test_a_rate_limited_compact_still_keeps_its_history(self, tmp_path):
        """The retry is for the weather, and a 429 is weather."""
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=tmp_path / "h.md",
        )
        real = t.client.messages.create

        def create(**call):
            if HANDOFF_MARKER in call["messages"][-1]["content"]:
                raise _RateLimited("rate limited")
            return real(**call)

        t.client.messages.create = Mock(side_effect=create)
        t.translate(self.UNIT)
        t.translate(self.UNIT)
        assert t.session.messages(), "a rate limit cost the accumulated context"


class TestCompactIsVisible:
    def _run(self, tmp_path, report, capsys):
        t = _translator(
            ["译文", report], context_compact_at=10, handoff_path=tmp_path / "h.md"
        )
        t.translate("a" * 200)
        return capsys.readouterr().out

    def test_the_summary_is_printed(self, tmp_path, capsys):
        assert "They walked to the barn." in self._run(
            tmp_path, "They walked to the barn.", capsys
        )

    def test_the_window_number_is_printed(self, tmp_path, capsys):
        assert "window 1" in self._run(tmp_path, "Summary.", capsys).lower()

    def test_square_brackets_survive_rich_markup(self, tmp_path, capsys):
        """Reports really do contain things like [PGA]; rich would eat them."""
        assert "[PGA]" in self._run(tmp_path, "Based on the [PGA] edition.", capsys)

    def test_quiet_suppresses_the_report(self, tmp_path, capsys):
        t = _translator(
            ["译文", "They walked to the barn."],
            context_compact_at=10,
            handoff_path=tmp_path / "h.md",
        )
        t.quiet = True
        t.translate("a" * 200)
        assert "They walked to the barn." not in capsys.readouterr().out

    def test_quiet_still_writes_the_file(self, tmp_path):
        path = tmp_path / "h.md"
        t = _translator(
            ["译文", "They walked to the barn."],
            context_compact_at=10,
            handoff_path=path,
        )
        t.quiet = True
        t.translate("a" * 200)
        assert "They walked to the barn." in path.read_text(encoding="utf-8")


class TestAsyncPathIsRefused:
    def test_session_mode_refuses_before_any_request(self):
        """The async path threads an immutable per-call context, so the
        session would never be appended to and every request would bill
        uncached. It must fail before a single message is sent."""
        import asyncio

        from book_maker.translator.base_translator import AsyncTranslationUnsupported

        t = _translator()
        with pytest.raises(AsyncTranslationUnsupported):
            asyncio.run(t.translate_async("text"))
        assert t.client.messages.create.call_count == 0


class TestLoaderWiring:
    """The loader has always passed these; this class used to swallow them.

    `--use_context session --model claude` reached the translator as a
    `**kwargs` entry nobody read, so the mode the plan skill defaults to ran
    as window mode without a word about it.
    """

    def _loader(self, tmp_path, **kwargs):
        from pathlib import Path

        from book_maker.loader.epub_loader import EPUBBookLoader

        book = (
            Path(__file__).resolve().parent.parent / "test_books" / "animal_farm.epub"
        )
        target = tmp_path / book.name
        target.write_bytes(book.read_bytes())
        with patch("book_maker.translator.claude_translator.Anthropic"):
            return EPUBBookLoader(
                str(target),
                Claude,
                "k",
                False,
                language="Chinese",
                context_flag=True,
                **kwargs,
            )

    def test_session_mode_reaches_the_translator(self, tmp_path):
        loader = self._loader(tmp_path, context_mode="session")
        assert loader.translate_model.session is not None

    def test_the_handoff_file_lands_beside_the_book(self, tmp_path):
        loader = self._loader(tmp_path, context_mode="session")
        assert (
            loader.translate_model.handoff_path == tmp_path / "animal_farm_handoff.md"
        )

    def test_the_compact_budget_reaches_the_translator(self, tmp_path):
        loader = self._loader(tmp_path, context_mode="session", context_compact_at=2500)
        assert loader.translate_model._session_budget() == 2500

    def test_bare_use_context_still_means_window_mode(self, tmp_path):
        loader = self._loader(tmp_path, context_mode="window")
        assert loader.translate_model.session is None


class TestCompactionDisabled:
    """`--no-context-compact` rolls the window over with no summary at all."""

    def _disabled(self, path):
        return _translator(
            ["译文", "译文"],
            context_compact_at=10,
            no_context_compact=True,
            handoff_path=path,
        )

    def test_it_rolls_over_without_a_handoff_turn(self, tmp_path):
        t = self._disabled(tmp_path / "h.md")
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert len(t.sent) == 2, "a handoff report was bought after all"

    def test_the_next_window_starts_empty(self, tmp_path):
        t = self._disabled(tmp_path / "h.md")
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert _prefix(t.sent[1]) == []

    def test_it_writes_no_handoff_file(self, tmp_path):
        path = tmp_path / "h.md"
        t = self._disabled(path)
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert not path.exists()

    def test_without_the_flag_the_report_is_still_bought(self, tmp_path):
        t = _translator(
            ["译文", "Summary."], context_compact_at=10, handoff_path=tmp_path / "h.md"
        )
        t.translate("a" * 200)
        assert HANDOFF_MARKER in t.sent[-1]["messages"][-1]["content"]


class TestReplyBlocks:
    """A reply is a list of blocks; only the text ones are the translation.

    A model with extended thinking puts a `thinking` block before its text
    (OpenRouter's `anthropic/claude-opus-5` does so by default), so
    `content[0]` is not the translation. Seen live 260903: the anthropic
    route crashed on the first paragraph with "'ThinkingBlock' object has no
    attribute 'text'".
    """

    @staticmethod
    def _answering(*blocks):
        t = _translator(context_flag=False)
        reply = _message("unused")
        reply.content = list(blocks)
        t.client.messages.create = Mock(return_value=reply)
        return t

    def test_a_thinking_block_before_the_text_is_skipped(self):
        t = self._answering(
            SimpleNamespace(type="thinking", thinking="…"),
            SimpleNamespace(type="text", text="译文"),
        )
        assert t.translate("text") == "译文"

    def test_two_text_blocks_are_joined(self):
        t = self._answering(
            SimpleNamespace(type="text", text="译"),
            SimpleNamespace(type="text", text="文"),
        )
        assert t.translate("text") == "译文"

    def test_a_reply_without_text_fails_loudly(self):
        t = self._answering(SimpleNamespace(type="thinking", thinking="…"))
        with pytest.raises(ValueError, match="no text block.*thinking"):
            t.translate("text")


class TestRequestExtras:
    """`--extra_body` / `--extra_headers` on the anthropic route.

    `thinking: {"type": "disabled"}` is the field this route exists to carry:
    thinking buys nothing on a paragraph and costs tokens, but sending it
    unconditionally would bet on every gateway and every future model
    default, so it stays the operator's to pass.
    """

    def _translator(self):
        return Claude("sk-test", "Chinese")

    def test_headers_go_on_the_client(self):
        t = self._translator()
        t.set_request_extras(extra_headers={"X-Title": "bbm"})

        assert t.client.default_headers["X-Title"] == "bbm"

    def test_the_translate_call_sends_the_body(self):
        t = self._translator()
        t.set_request_extras(extra_body={"thinking": {"type": "disabled"}})
        with patch.object(t, "client") as client:
            client.messages.create.return_value = SimpleNamespace(
                content=[SimpleNamespace(type="text", text="译文")], usage=None
            )
            t.translate("hello")

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}

    def test_a_run_with_no_extras_sends_none(self):
        # the request an anthropic run made before the flag existed
        t = self._translator()
        with patch.object(t, "client") as client:
            client.messages.create.return_value = SimpleNamespace(
                content=[SimpleNamespace(type="text", text="译文")], usage=None
            )
            t.translate("hello")

        assert client.messages.create.call_args.kwargs["extra_body"] is None

    def test_the_route_answers_that_it_takes_them(self):
        assert Claude.SUPPORTS_REQUEST_EXTRAS is True


class TestOuterFenceStripping:
    """The route's own prompt teaches the model to fence its reply.

    DEFAULT_PROMPT hands the source "within triple backticks", and models
    mirror the delimiter back around the translation. Nothing removed it, so a
    default anthropic run wrote the fences into the book — every paragraph
    arriving as `<p class="calibre_">```动物庄园```</p>` (seen live 260905,
    plain and `--use_context session` alike). The prompt is upstream's
    contract, so the repair is on the reply; only a symmetric outer wrap goes.
    """

    def test_a_fenced_reply_is_unwrapped(self):
        assert _strip_outer_fence("```\n动物庄园\n```") == "动物庄园"

    def test_an_inline_wrap_is_unwrapped(self):
        # what the live run actually produced
        assert (
            _strip_outer_fence("```动物庄园：一个童话故事```")
            == "动物庄园：一个童话故事"
        )

    def test_a_language_tag_is_dropped_with_the_fence(self):
        assert _strip_outer_fence("```zh\n动物庄园\n```") == "动物庄园"

    def test_surrounding_whitespace_does_not_hide_the_wrap(self):
        assert _strip_outer_fence("\n  ```\n动物庄园\n```  \n") == "动物庄园"

    def test_an_unfenced_reply_comes_back_byte_identical(self):
        for reply in ("动物庄园", "  动物庄园\n", "第一行\n第二行", ""):
            assert _strip_outer_fence(reply) == reply

    def test_backticks_inside_the_translation_survive(self):
        assert _strip_outer_fence("```\n用 `code` 表示\n```") == "用 `code` 表示"
        assert _strip_outer_fence("用 ``code`` 表示") == "用 ``code`` 表示"

    def test_multi_line_content_is_preserved_exactly(self):
        body = "第一行\n\n  第二行\n第三行"
        assert _strip_outer_fence(f"```\n{body}\n```") == body

    def test_an_opening_fence_alone_is_left_alone(self):
        """Half a wrap is a damaged reply; repairing half of it hides that."""
        assert _strip_outer_fence("```\n动物庄园") == "```\n动物庄园"
        assert _strip_outer_fence("动物庄园\n```") == "动物庄园\n```"
        assert _strip_outer_fence("```") == "```"

    def test_an_inline_wrap_keeps_the_first_word(self):
        """The info string is only a tag when the fence owns its line."""
        assert _strip_outer_fence("```Animal Farm```") == "Animal Farm"

    def test_a_reply_with_inner_fences_is_left_alone(self):
        """Two code blocks, not one wrapped translation."""
        reply = "```\n甲\n```\n\n乙\n\n```\n丙\n```"
        assert _strip_outer_fence(reply) == reply

    def test_a_wrap_around_nothing_is_left_alone(self):
        """Better a visibly empty reply than a manufactured empty translation."""
        assert _strip_outer_fence("```\n```") == "```\n```"


class TestFencedRepliesNeverReachTheBook:
    """The same reply, through every path a translation takes on this route."""

    FENCED = "```\n动物庄园\n```"

    def test_the_plain_path_unwraps_it(self):
        t = _translator([self.FENCED], context_flag=False)
        assert t.translate("Animal Farm") == "动物庄园"

    def test_the_session_path_unwraps_it(self):
        t = _translator([self.FENCED])
        assert t.translate("Animal Farm") == "动物庄园"

    def test_the_window_path_unwraps_it(self):
        t = _translator([self.FENCED], context_mode="window")
        assert t.translate("Animal Farm") == "动物庄园"

    def test_the_session_history_keeps_the_clean_text(self):
        """A fenced pair in the prefix teaches the next reply to fence too."""
        t = _translator([self.FENCED, "第二段"])
        t.translate("Animal Farm")
        t.translate("Chapter one")
        replayed = [m["content"] for m in t.sent[1]["messages"] if m["role"] != "user"]
        assert replayed == ["动物庄园"]

    def test_the_window_history_keeps_the_clean_text(self):
        t = _translator([self.FENCED, "第二段"], context_mode="window")
        t.translate("Animal Farm")
        assert t.context_translated_list == ["动物庄园"]

    def test_a_wrap_split_across_two_text_blocks_is_unwrapped(self):
        """Blocks are joined first, so the strip sees the whole reply."""
        t = _translator(context_flag=False)
        reply = _message("unused")
        reply.content = [
            SimpleNamespace(type="text", text="```\n"),
            SimpleNamespace(type="text", text="动物庄园\n```"),
        ]
        t.client.messages.create = Mock(return_value=reply)
        assert t.translate("Animal Farm") == "动物庄园"

    def test_a_fenced_batch_reply_leaves_no_fence_in_any_segment(self):
        t = _translator(["```\n动物庄园\n\n@@\n\n第一章\n```"], context_flag=False)
        assert t.translate_list(["Animal Farm", "Chapter One"]) == [
            "动物庄园",
            "第一章",
        ]
