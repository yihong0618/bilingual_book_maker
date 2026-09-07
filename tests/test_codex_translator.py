"""The `codex` translator format: threads, windows, preflight."""

import pytest

from book_maker.session_context import handoff_prompt
from book_maker.codex_client import CodexLoginRequired, CodexTurnFailed, RateLimits
from book_maker.translator import FORMAT_DICT, LLM_FORMATS
from book_maker.translator.codex_translator import DEFAULT_MODEL, Codex


class FakeServer:
    """Stands in for CodexAppServer."""

    def __init__(self, answers=None, limits=None):
        self.answers = list(answers or [])
        self.turns = []
        self.threads = []
        self.closed = False
        self._limits = (
            limits
            if limits is not None
            else RateLimits(
                used_percent=10,
                window_minutes=300,
                resets_at=1788055986,
                plan_type="plus",
                reached_type=None,
            )
        )

    def start(self):
        return self

    def close(self):
        self.closed = True

    def ensure_logged_in(self):
        return self._limits

    def rate_limits(self):
        return self._limits

    def latest_rate_limits(self):
        return self._limits

    def set_limits(self, limits):
        self._limits = limits

    def start_thread(self, model=None, base_instructions=None, cwd=None):
        self.threads.append({"model": model, "base_instructions": base_instructions})
        return f"th-{len(self.threads)}"

    def run_turn(self, thread_id, text, output_schema=None, timeout=None):
        self.turns.append({"thread": thread_id, "text": text, "schema": output_schema})
        return self.answers.pop(0) if self.answers else "译文"


def _codex(answers=None, limits=None, **kwargs):
    server = FakeServer(answers, limits)
    translator = Codex(key="", language="Chinese", server=server, **kwargs)
    translator.server = server
    return translator


class TestRegistration:
    def test_registered_as_a_format(self):
        assert FORMAT_DICT["codex"] is Codex

    def test_counts_as_a_model_bearing_format(self):
        assert "codex" in LLM_FORMATS


class TestPreflight:
    def test_login_failure_is_raised_not_swallowed(self):
        server = FakeServer()
        server.ensure_logged_in = lambda: (_ for _ in ()).throw(
            CodexLoginRequired("run codex login")
        )
        translator = Codex(key="", language="Chinese", server=server)
        with pytest.raises(CodexLoginRequired):
            translator.preflight()

    def test_warns_when_the_window_is_nearly_spent(self, capsys):
        limits = RateLimits(
            used_percent=95,
            window_minutes=300,
            resets_at=1788055986,
            plan_type="plus",
            reached_type=None,
        )
        _codex(limits=limits).preflight()
        # Reported as what is left, not as what is gone.
        assert "5% of your Codex window remains" in capsys.readouterr().out

    def test_quiet_when_there_is_plenty_left(self, capsys):
        _codex().preflight()
        assert "Warning" not in capsys.readouterr().out


class TestTranslation:
    def test_returns_the_turn_text(self):
        t = _codex(["狗叫了。"])
        assert t.translate("The dog barked.") == "狗叫了。"

    def test_starts_one_thread_and_reuses_it(self):
        """A fresh thread costs ~17k tokens of preamble; reuse is the point."""
        t = _codex(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.translate(text)
        assert len(t.server.threads) == 1
        assert {turn["thread"] for turn in t.server.turns} == {"th-1"}

    def test_the_thread_carries_the_translation_instructions(self):
        t = _codex(["一"])
        t.translate("one")
        assert "translat" in t.server.threads[0]["base_instructions"].lower()

    def test_it_defaults_to_a_named_model(self):
        """Naming one keeps the compact budget lookup meaningful."""
        t = _codex(["一"])
        t.translate("one")
        assert t.server.threads[0]["model"] == DEFAULT_MODEL

    def test_the_default_model_resolves_a_usable_budget(self):
        """Naming a default model keeps the budget lookup well defined; the
        budget itself is uniform now."""
        from book_maker.session_context import compact_budget_for

        assert compact_budget_for(DEFAULT_MODEL) >= 500

    def test_an_empty_model_list_falls_back_to_the_default(self):
        t = _codex(["一"])
        t.set_model_list([])
        t.translate("one")
        assert t.server.threads[0]["model"] == DEFAULT_MODEL

    def test_the_model_is_passed_through(self):
        t = _codex(["一"])
        t.set_model_list(["gpt-5.6-sol"])
        t.translate("one")
        assert t.server.threads[0]["model"] == "gpt-5.6-sol"

    def test_translate_does_not_print_the_translation(self, capsys):
        """The loaders display source and translation; printing here showed
        every paragraph twice."""
        t = _codex(["狗叫了。"])
        t.translate("The dog barked.")
        assert "狗叫了。" not in capsys.readouterr().out


class TestWindowing:
    # Each 200-char unit is ~50 estimated tokens, so a 100-token budget trips
    # after the second one — not after every one.
    ANSWERS = ["一", "二", "Summary: they walked."]

    def test_a_new_thread_starts_when_the_budget_is_reached(self, tmp_path):
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=tmp_path / "h.md")
        t.translate("a" * 200)
        assert len(t.server.threads) == 1
        t.translate("b" * 200)
        assert len(t.server.threads) == 2

    def test_the_next_thread_is_seeded_with_the_handoff(self, tmp_path):
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=tmp_path / "h.md")
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert "they walked" in t.server.threads[1]["base_instructions"]

    def test_the_handoff_turn_runs_on_the_thread_being_retired(self, tmp_path):
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=tmp_path / "h.md")
        t.translate("a" * 200)
        t.translate("b" * 200)
        handoff = t.server.turns[-1]
        assert handoff["thread"] == "th-1"
        assert handoff["text"].startswith(handoff_prompt()[:40])

    def test_the_report_is_persisted(self, tmp_path):
        path = tmp_path / "h.md"
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=path)
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert "they walked" in path.read_text(encoding="utf-8")

    def test_no_windowing_without_a_budget_being_hit(self, tmp_path):
        t = _codex(["一", "二"], context_compact_at=100_000)
        t.translate("short")
        t.translate("also short")
        assert len(t.server.threads) == 1


class TestKeyHandling:
    def test_needs_no_api_key(self):
        assert Codex(key="", language="Chinese", server=FakeServer()) is not None

    def test_rotate_key_is_a_no_op(self):
        _codex().rotate_key()  # must not raise: there is no key to rotate


class TestConcurrency:
    """Parallel workers share one Codex instance and therefore one thread.

    `_clone_translator_for_context` only clones translators that carry
    `context_flag`, which this one does not: a codex thread *is* the context,
    so there are no per-worker buffers to reset. Turns must therefore
    serialize, or chapters interleave into one thread and the window
    accounting races.
    """

    def test_concurrent_translations_are_serialized(self):
        import threading

        t = _codex()
        overlaps = []
        active = []

        original = t.server.run_turn

        def slow_turn(thread_id, text, output_schema=None, timeout=None):
            active.append(text)
            if len(active) > 1:
                overlaps.append(tuple(active))
            threading.Event().wait(0.02)
            active.remove(text)
            return original(thread_id, text, output_schema, timeout)

        t.server.run_turn = slow_turn
        threads = [
            threading.Thread(
                target=t.translate, args=(f"unit {i}",), kwargs={"needprint": False}
            )
            for i in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert overlaps == []
        assert len(t.server.turns) == 4

    def test_window_accounting_survives_concurrent_calls(self):
        import threading

        t = _codex(context_compact_at=100_000)
        threads = [
            threading.Thread(
                target=t.translate, args=("a" * 200,), kwargs={"needprint": False}
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # 8 units of ~50 source tokens each, none lost to a lost update.
        assert t._window_tokens >= 8 * 50


class TestQuotaReporting:
    """The window is reported as what remains, and only when it moves."""

    def _limits(self, used, **kw):
        from book_maker.codex_client import RateLimits

        return RateLimits(
            used_percent=used,
            window_minutes=300,
            resets_at=kw.pop("resets_at", 1788055986),
            plan_type="plus",
            reached_type=kw.pop("reached_type", None),
            **kw,
        )

    def test_remaining_is_printed_when_it_changes(self, capsys):
        t = _codex(["一", "二"], limits=self._limits(10))
        t.translate("one", needprint=False)
        capsys.readouterr()
        t.server.set_limits(self._limits(25))
        t.translate("two", needprint=False)
        assert "75% of the window remaining" in capsys.readouterr().out

    def test_an_unchanged_figure_is_not_reprinted(self, capsys):
        t = _codex(["一", "二"], limits=self._limits(10))
        t.translate("one", needprint=False)
        capsys.readouterr()
        t.translate("two", needprint=False)
        assert "remaining" not in capsys.readouterr().out


class TestQuotaExhaustion:
    """Spent quota waits for the reset instead of ending the run."""

    def _limits(self, used=100, reached="rate_limit_reached", resets_at=10_000):
        from book_maker.codex_client import RateLimits

        return RateLimits(
            used_percent=used,
            window_minutes=300,
            resets_at=resets_at,
            plan_type="plus",
            reached_type=reached,
        )

    def _codex_at(self, now, limits, answers=None, fail_times=0):
        slept = []
        t = _codex(
            answers or ["译文"], limits=limits, sleeper=slept.append, clock=lambda: now
        )
        state = {"left": fail_times}
        real = t.server.run_turn

        def run_turn(thread_id, text, output_schema=None, timeout=None):
            if state["left"] > 0:
                state["left"] -= 1
                raise CodexTurnFailed("rate limit")
            return real(thread_id, text, output_schema, timeout)

        t.server.run_turn = run_turn
        t.slept = slept
        return t

    def test_it_waits_past_the_reset_then_continues(self):
        """The real sequence: the turn fails and the quota update saying why
        arrives with it; waiting clears it and the retry goes through."""
        slept = []
        t = _codex(
            ["译文"],
            limits=self._limits(used=40, reached=None),
            sleeper=slept.append,
            clock=lambda: 9_000,
        )
        real = t.server.run_turn
        state = {"failed": False}

        def run_turn(thread_id, text, output_schema=None, timeout=None):
            if not state["failed"]:
                state["failed"] = True
                t.server.set_limits(self._limits())  # the push that explains it
                raise CodexTurnFailed("rate limit reached")
            return real(thread_id, text, output_schema, timeout)

        t.server.run_turn = run_turn
        original = t._wait_out_reset

        def wait(limits):  # a real reset clears the window
            done = original(limits)
            t.server.set_limits(self._limits(used=0, reached=None))
            return done

        t._wait_out_reset = wait

        assert t.translate("text", needprint=False) == "译文"
        assert slept == [10_000 - 9_000 + 60]

    def test_the_wait_message_says_when_it_resumes(self, capsys):
        t = self._codex_at(now=9_000, limits=self._limits())
        t._wait_out_reset(t.server.latest_rate_limits())
        out = capsys.readouterr().out.lower()
        assert "waiting" in out and "reset" in out

    def test_credit_depletion_is_not_waited_out(self):
        """Credits do not come back on a timer; waiting would hang forever."""
        t = self._codex_at(
            now=9_000,
            limits=self._limits(reached="workspace_owner_credits_depleted"),
            fail_times=1,
        )
        with pytest.raises(CodexTurnFailed):
            t.translate("text", needprint=False)
        assert t.slept == []

    def test_an_absurdly_distant_reset_is_not_waited_out(self):
        # it ends the run instead — saying when the allowance comes back,
        # which is the fact needed to decide when to rerun
        from book_maker.codex_client import CodexQuotaExhausted

        t = self._codex_at(
            now=0, limits=self._limits(resets_at=60 * 60 * 24 * 7), fail_times=1
        )
        with pytest.raises(CodexQuotaExhausted) as stop:
            t.translate("text", needprint=False)
        assert "does not reset until" in str(stop.value)
        assert t.slept == []

    def test_a_failure_with_quota_left_is_not_treated_as_exhaustion(self):
        """A turn fails for many reasons; only the quota says it is the quota."""
        t = self._codex_at(
            now=0, limits=self._limits(used=10, reached=None), fail_times=1
        )
        with pytest.raises(CodexTurnFailed):
            t.translate("text", needprint=False)
        assert t.slept == []

    def test_it_gives_up_rather_than_waiting_forever(self):
        t = self._codex_at(now=9_000, limits=self._limits(), fail_times=99)
        with pytest.raises(CodexTurnFailed):
            t.translate("text", needprint=False)
        assert len(t.slept) <= 3


class TestUserPrompt:
    """`--prompt` adds to the thread instructions; it does not replace them."""

    def test_the_user_system_message_is_appended_not_substituted(self):
        t = _codex(["一"], prompt_sys_msg="Render dialogue as spoken British English.")
        t.translate("one", needprint=False)
        instructions = t.server.threads[0]["base_instructions"]
        assert "Render dialogue as spoken British English." in instructions
        # The base instructions are what keep a turn from acting like an agent.
        assert "translation engine" in instructions

    def test_the_user_message_comes_after_ours(self):
        t = _codex(["一"], prompt_sys_msg="MY RULE")
        t.translate("one", needprint=False)
        instructions = t.server.threads[0]["base_instructions"]
        assert instructions.index("translation engine") < instructions.index("MY RULE")

    def test_a_user_template_is_applied_to_the_turn(self):
        t = _codex(["一"], prompt_template="Translate to {language}: {text}")
        t.translate("one", needprint=False)
        assert t.server.turns[0]["text"] == "Translate to Chinese: one"

    def test_without_a_template_the_turn_carries_bare_source(self):
        """The thread already says to translate; repeating it per paragraph
        would pay for the same instruction over and over."""
        t = _codex(["一"])
        t.translate("one", needprint=False)
        assert t.server.turns[0]["text"] == "one"


class TestFixedStyle:
    def test_a_fixed_style_reaches_the_thread(self):
        t = _codex(["一"], style_note="plain modern prose")
        t.translate("one", needprint=False)
        assert "plain modern prose" in t.server.threads[0]["base_instructions"]

    def test_a_fixed_style_is_not_asked_for_at_handoff(self, tmp_path):
        t = _codex(
            ["一", "二", "Summary."],
            context_compact_at=100,
            style_note="plain modern prose",
            handoff_path=tmp_path / "h.md",
        )
        t.translate("a" * 200, needprint=False)
        t.translate("b" * 200, needprint=False)
        assert "style" not in t.server.turns[-1]["text"].lower()

    def test_the_fixed_style_is_written_into_the_report(self, tmp_path):
        path = tmp_path / "h.md"
        t = _codex(
            ["一", "二", "Summary."],
            context_compact_at=100,
            style_note="plain modern prose",
            handoff_path=path,
        )
        t.translate("a" * 200, needprint=False)
        t.translate("b" * 200, needprint=False)
        assert "plain modern prose" in path.read_text(encoding="utf-8")

    def test_without_a_fixed_style_the_handoff_still_asks_for_one(self, tmp_path):
        t = _codex(
            ["一", "二", "Summary."],
            context_compact_at=100,
            handoff_path=tmp_path / "h.md",
        )
        t.translate("a" * 200, needprint=False)
        t.translate("b" * 200, needprint=False)
        assert "style" in t.server.turns[-1]["text"].lower()


class TestModelAlias:
    """`--model codex` is the deprecated spelling: the legacy shim rewrites it
    to `--api_format codex` before the parser, so the sidecar is always named,
    never inferred from a model id."""

    def test_codex_is_not_inferred_from_a_model_name(self):
        from book_maker.cli import infer_api_format

        assert infer_api_format(None, "codex") == "openai"

    def test_a_base_url_still_decides_its_own_format(self):
        from book_maker.cli import infer_api_format

        assert infer_api_format("https://api.openai.com/v1", "codex") == "openai"

    def test_the_alias_is_not_sent_as_a_model_id(self):
        t = _codex(["一"])
        t.set_model_list(["codex"])
        t.translate("one", needprint=False)
        assert t.server.threads[0]["model"] == DEFAULT_MODEL

    def test_a_real_model_id_still_wins(self):
        t = _codex(["一"])
        t.set_model_list(["gpt-5.6-sol"])
        t.translate("one", needprint=False)
        assert t.server.threads[0]["model"] == "gpt-5.6-sol"

    def test_other_models_are_unaffected(self):
        from book_maker.cli import infer_api_format

        assert infer_api_format(None, "gpt-5-mini") == "openai"
        assert infer_api_format(None, "claude-sonnet-4-6") == "anthropic"


class TestQuestionThread:
    """`_chat_completion` is what plan classification runs on.

    A fresh Codex thread costs ~16.9k input tokens of preamble, and the
    classifier asks many questions: pages of 12 signatures, rung retries when
    a reply will not parse, and bisection of a partly-answered page. One
    thread per question would bill more preamble for classifying a book than
    for translating it.
    """

    def test_one_thread_serves_every_question(self):
        t = _codex(["a", "b", "c"])
        for _ in range(3):
            t._chat_completion("classify this")
        assert len(t.server.threads) == 1
        assert len({turn["thread"] for turn in t.server.turns}) == 1

    def test_questions_stay_off_the_translation_thread(self):
        """A classification question in the translation thread would pollute
        the context every later unit inherits."""
        t = _codex(["译文", "answer"])
        t.translate("one", needprint=False)
        t._chat_completion("classify this")
        threads = {turn["thread"] for turn in t.server.turns}
        assert len(threads) == 2
        assert len(t.server.threads) == 2

    def test_a_second_model_gets_its_own_thread(self):
        """--plan-classify-model can name a model the book is not translated
        with; a thread is bound to one model, so it cannot be shared."""
        t = _codex(["a", "b", "c"])
        t._chat_completion("q", model="gpt-5.6-sol")
        t._chat_completion("q", model="gpt-5.6-sol")
        t._chat_completion("q", model="gpt-5.5")
        assert [th["model"] for th in t.server.threads] == ["gpt-5.6-sol", "gpt-5.5"]

    def test_a_compact_does_not_discard_the_question_thread(self):
        """Compacting replaces the translation thread. The question thread is
        unrelated and re-paying its preamble would be pure waste."""
        t = _codex(["a"])
        t._chat_completion("q")
        before = len(t.server.threads)
        t._thread_id = None  # what a compact does to the translation thread
        t._chat_completion("q")
        assert len(t.server.threads) == before

    def test_a_question_sits_out_a_spent_quota(self):
        """Classification runs before the first paragraph, so a user near
        their limit would otherwise fail at the very start — and plan mode
        has no degrade-to-defaults path, so that failure stops the run."""
        spent = RateLimits(
            used_percent=100,
            window_minutes=300,
            resets_at=10_000,
            plan_type="plus",
            reached_type="rate_limit_reached",
        )
        slept = []
        t = _codex(["answer"], limits=spent, sleeper=slept.append, clock=lambda: 9_000)

        # The window rolls over while we wait, as a real reset does.
        def refresh():
            t.server.set_limits(
                RateLimits(used_percent=0, window_minutes=300, plan_type="plus")
            )
            return t.server._limits

        t.server.rate_limits = refresh
        assert t._chat_completion("classify this") == "answer"
        assert slept, "a spent quota was not waited out"

    def test_a_dropped_thread_is_evicted_and_the_question_retried(self):
        """Reuse costs the disposability a fresh thread had for free. The
        caller cannot survive a dead thread — _ask_page turns it into
        PlanClassifyFatal and stops classification — so recovery lives here."""
        t = _codex(["answer"])
        t._chat_completion("first")  # opens and caches the thread
        dead = t._question_threads[t.model]
        real = t.server.run_turn

        def run_turn(thread_id, text, output_schema=None, timeout=None):
            if thread_id == dead:
                raise CodexTurnFailed("thread not found")
            return real(thread_id, text, output_schema, timeout)

        t.server.run_turn = run_turn
        t.server.answers = ["recovered"]
        assert t._chat_completion("second") == "recovered"
        assert t._question_threads[t.model] != dead
        assert len(t.server.threads) == 2

    def test_a_second_failure_is_not_retried_forever(self):
        """One retry, not a loop: a thread that dies twice is not a dropped
        thread, and classification should fail loudly rather than spend."""
        t = _codex(["answer"])

        def run_turn(thread_id, text, output_schema=None, timeout=None):
            raise CodexTurnFailed("thread not found")

        t.server.run_turn = run_turn
        with pytest.raises(CodexTurnFailed):
            t._chat_completion("q")
        assert len(t.server.threads) == 2


class TestClassifierThread:
    """`classify_session` is how plan mode classifies on this route.

    There is no capability probe here at all — the sidecar is not an
    endpoint the OpenAI-shaped probe can grade — so the schema classifier
    has nothing to work with and the session entry does the asking. The
    thread is the append-only history, which is exactly the shape that
    entry wants: the trunk is paid for once, as the thread's instructions.
    """

    def test_the_trunk_becomes_the_threads_instructions(self):
        from book_maker.loader.classify.session import trunk_with_inline_example

        t = _codex(["skip,translate,unsure"])
        session = t.classify_session()
        session.start("TRUNK")
        assert t.server.threads == []  # opened on the first turn, not before
        assert session.ask("units 1-3") == "skip,translate,unsure"
        assert t.server.threads == [
            {
                "model": t.model,
                "base_instructions": trunk_with_inline_example("TRUNK"),
            }
        ]
        # the turn carries the signatures and nothing else: repeating the
        # instructions is the one cost an append-only thread exists to avoid
        assert [turn["text"] for turn in t.server.turns] == ["units 1-3"]

    def test_the_instructions_carry_the_demonstrated_exchange_inline(self):
        # a thread has no room for a reply nobody made, so the pair the
        # openai-shaped route seeds as messages degrades to text here — it is
        # never silently dropped
        from book_maker.loader.classify.session import (
            EXAMPLE_REPLY,
            build_example_turn,
        )

        t = _codex(["skip,translate,unsure"])
        session = t.classify_session()
        session.start("TRUNK")
        session.ask("units 1-3")

        instructions = t.server.threads[0]["base_instructions"]
        assert instructions.startswith("TRUNK")
        assert build_example_turn() in instructions
        assert EXAMPLE_REPLY in instructions

    def test_a_restart_opens_a_new_thread_carrying_the_trunk(self):
        from book_maker.loader.classify.session import trunk_with_inline_example

        t = _codex(["skip", "translate"])
        session = t.classify_session()
        session.start("TRUNK")
        session.ask("units 1-3")
        session.start("TRUNK")  # what crossing the budget does
        session.ask("units 4-6")
        assert [th["base_instructions"] for th in t.server.threads] == [
            trunk_with_inline_example("TRUNK"),
            trunk_with_inline_example("TRUNK"),
        ]
        assert len({turn["thread"] for turn in t.server.turns}) == 2

    def test_it_is_neither_the_translation_thread_nor_the_question_thread(self):
        t = _codex(["译文", "answer", "skip"])
        t.translate("one", needprint=False)
        t._chat_completion("a question")
        session = t.classify_session()
        session.start("TRUNK")
        session.ask("units 1-3")
        assert len({turn["thread"] for turn in t.server.turns}) == 3

    def test_a_named_classify_model_gets_its_own_thread(self):
        t = _codex(["skip"])
        session = t.classify_session(model="gpt-5.6-sol")
        session.start("TRUNK")
        session.ask("units 1-3")
        assert t.server.threads[0]["model"] == "gpt-5.6-sol"

    def test_a_dropped_thread_is_reopened_with_the_trunk(self):
        # the sidecar can lose a thread; the trunk is re-sent as the new
        # thread's instructions, and the verdicts already given do not matter
        # to the ones still to come
        t = _codex(["first"])
        session = t.classify_session()
        session.start("TRUNK")
        session.ask("units 1-3")
        dead = t.server.threads[-1]
        real = t.server.run_turn

        def run_turn(thread_id, text, output_schema=None, timeout=None):
            if thread_id == "th-1":
                raise CodexTurnFailed("thread not found")
            return real(thread_id, text, output_schema, timeout)

        t.server.run_turn = run_turn
        t.server.answers = ["recovered"]
        assert session.ask("units 4-6") == "recovered"
        assert len(t.server.threads) == 2
        assert t.server.threads[-1] == dead  # same model, same trunk


class TestQuiet:
    """--quiet is what every paid run of the plan workflow uses; it must
    reach this format's own echoes, not just the loader's."""

    def test_the_per_unit_quota_line_is_silenced(self, capsys):
        t = _codex(["一", "二"])
        t.quiet = True
        t.translate("one")
        t.server.set_limits(RateLimits(used_percent=42, resets_at=1788055986))
        t.translate("two")
        assert "of the window remaining" not in capsys.readouterr().out

    def test_the_per_unit_quota_line_still_prints_when_not_quiet(self, capsys):
        t = _codex(["一", "二"])
        t.translate("one")
        t.server.set_limits(RateLimits(used_percent=42, resets_at=1788055986))
        t.translate("two")
        assert "of the window remaining" in capsys.readouterr().out

    def test_preflight_says_nothing_about_a_healthy_window(self, capsys):
        t = _codex()
        t.quiet = True
        t.preflight()
        assert capsys.readouterr().out == ""

    def test_preflight_still_warns_about_a_spent_one(self, capsys):
        limits = RateLimits(
            used_percent=95,
            window_minutes=300,
            resets_at=1788055986,
            plan_type="plus",
            reached_type=None,
        )
        t = _codex(limits=limits)
        t.quiet = True
        t.preflight()
        assert "5% of your Codex window remains" in capsys.readouterr().out


class TestWeeklyLimit:
    """A 5-hour window is waited out; a weekly one cannot be. What used to
    happen then was a raw `turn failed` traceback with no reset time."""

    def _spent(self, resets_at):
        return RateLimits(
            used_percent=100,
            resets_at=resets_at,
            reached_type="rate_limit_reached",
            plan_type="plus",
        )

    def test_a_reset_too_far_off_stops_the_run_and_names_the_time(self):
        from book_maker.codex_client import CodexQuotaExhausted

        now = 1788055986
        t = _codex(["一"], limits=self._spent(now + 5 * 24 * 3600))
        t._now = lambda: now
        with pytest.raises(CodexQuotaExhausted) as stop:
            t.translate("one")
        assert "does not reset until" in str(stop.value)
        assert "--resume" in str(stop.value)

    def test_the_stop_is_reported_without_a_traceback(self):
        from book_maker.codex_client import CodexQuotaExhausted

        # the loader prints the message instead of a traceback for anything
        # that says its message is the whole explanation
        assert CodexQuotaExhausted("x").user_facing is True

    def test_a_reset_within_reach_is_still_waited_out(self):
        now = 1788055986
        t = _codex(["一"], limits=self._spent(now + 600))
        t._now = lambda: now
        slept = []
        t._sleep = slept.append
        assert t.translate("one") == "一"
        assert slept and slept[0] > 0


class TestQuotaMessageDoesNotOverpromise:
    def test_it_does_not_claim_progress_was_saved(self):
        # an epub run with --accumulated_num > 1 skips checkpointing, so the
        # claim was not the translator's to make; the loader prints what it
        # actually saved
        from book_maker.codex_client import CodexQuotaExhausted

        now = 1788055986
        t = _codex(
            ["一"],
            limits=RateLimits(
                used_percent=100,
                resets_at=now + 5 * 24 * 3600,
                reached_type="rate_limit_reached",
            ),
        )
        t._now = lambda: now
        with pytest.raises(CodexQuotaExhausted) as stop:
            t.translate("one")
        message = str(stop.value)
        assert "Progress is saved" not in message
        assert "--resume" in message
        assert "does not reset until" in message


class TestCompactionDisabled:
    """`--no-context-compact`: open a fresh thread, never buy a report."""

    def test_it_starts_a_new_thread_without_a_handoff_turn(self, tmp_path):
        t = _codex(
            ["译文"] * 40,
            context_compact_at=100,
            no_context_compact=True,
            handoff_path=tmp_path / "h.md",
        )
        for _ in range(4):
            t.translate("x" * 400)
        assert len(t.server.threads) > 1, "the thread was never rolled over"
        assert not any(
            turn["text"].startswith(handoff_prompt()[:40]) for turn in t.server.turns
        ), "a handoff report was requested with compaction disabled"
        assert not (tmp_path / "h.md").exists()

    def test_the_next_thread_is_seeded_with_nothing(self):
        t = _codex(["译文"] * 40, context_compact_at=100, no_context_compact=True)
        for _ in range(4):
            t.translate("x" * 400)
        assert all(
            not (thread["base_instructions"] or "").count("handoff")
            for thread in t.server.threads
        )

    def test_without_the_flag_the_thread_is_still_compacted(self):
        t = _codex(["译文"] * 40, context_compact_at=100)
        for _ in range(4):
            t.translate("x" * 400)
        assert any(
            turn["text"].startswith(handoff_prompt()[:40]) for turn in t.server.turns
        ), "a budget must still buy the handoff report"
