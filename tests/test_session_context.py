"""Append-only session history, compact budgets, and the handoff report.

The invariant these tests defend: in session mode the request prefix must stay
byte-identical between requests, or the endpoint's prompt cache misses and the
whole history is re-billed at 1x — which costs more than the window mode this
feature replaces.
"""

import json

import pytest

from book_maker.session_context import (
    DEFAULT_COMPACT_BUDGET,
    HandoffReport,
    SessionHistory,
    compact_budget_for,
    estimate_tokens,
    handoff_prompt,
)


class TestEstimateTokens:
    def test_latin_is_about_chars_over_four(self):
        assert estimate_tokens("a" * 400) == pytest.approx(100, rel=0.05)

    def test_cjk_is_denser_than_latin(self):
        assert estimate_tokens("温" * 100) > estimate_tokens("a" * 100)

    def test_cjk_is_about_chars_over_one_point_seven(self):
        assert estimate_tokens("温" * 170) == pytest.approx(100, rel=0.05)

    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0

    def test_mixed_script_counts_both(self):
        mixed = estimate_tokens("a" * 400 + "温" * 170)
        assert mixed == pytest.approx(200, rel=0.05)


class TestCompactBudget:
    """One budget for every model.

    The per-model table this replaces optimised for cost, which at current
    prices is the wrong objective: the whole context bill for a novel is
    cents, while a shorter window means more handoff seams and a seam is
    where names drift.
    """

    def test_every_model_gets_the_same_budget(self):
        for model in ("gpt-5.6-luna", "deepseek-v4-flash-0731", "glm-5.3"):
            assert compact_budget_for(model) == DEFAULT_COMPACT_BUDGET

    def test_an_unknown_model_gets_it_too(self):
        assert compact_budget_for("some-new-model") == DEFAULT_COMPACT_BUDGET

    def test_none_model_gets_it_too(self):
        assert compact_budget_for(None) == DEFAULT_COMPACT_BUDGET

    def test_the_budget_can_hold_a_useful_window(self):
        """Below ~500 a window cannot hold a paragraph and its translation, so
        every unit would trigger a paid handoff."""
        assert DEFAULT_COMPACT_BUDGET >= 500


class TestSessionHistory:
    def test_starts_empty(self):
        assert SessionHistory().messages() == []

    def test_append_adds_a_user_assistant_pair(self):
        h = SessionHistory()
        h.append("source", "translated")
        assert [m["role"] for m in h.messages()] == ["user", "assistant"]
        assert h.messages()[0]["content"] == "source"
        assert h.messages()[1]["content"] == "translated"

    def test_history_is_append_only_and_prefix_stable(self):
        """The whole feature rests on this: earlier messages never change."""
        h = SessionHistory()
        h.append("one", "1")
        before = json.dumps(h.messages())
        h.append("two", "2")
        after = json.dumps(h.messages())
        assert after.startswith(before[:-1])

    def test_estimated_tokens_grows_with_content(self):
        h = SessionHistory()
        empty = h.estimated_tokens()
        h.append("a" * 400, "b" * 400)
        assert h.estimated_tokens() > empty + 100

    def test_should_compact_only_past_budget(self):
        h = SessionHistory()
        h.append("a" * 400, "b" * 400)
        assert not h.should_compact(10_000)
        assert h.should_compact(50)

    def test_reset_with_seed_clears_history_and_seeds_next_window(self):
        h = SessionHistory()
        h.append("one", "1")
        h.reset(seed="story so far")
        assert len(h.messages()) == 1
        assert "story so far" in h.messages()[0]["content"]
        assert h.messages()[0]["role"] == "user"

    def test_reset_without_seed_is_empty(self):
        h = SessionHistory()
        h.append("one", "1")
        h.reset(seed="")
        assert h.messages() == []

    def test_windows_counts_compactions(self):
        h = SessionHistory()
        assert h.windows == 1
        h.reset(seed="x")
        assert h.windows == 2

    def test_should_compact_measures_incremental_content_not_seed(self):
        """A large seed must not immediately trigger compaction on the first translation."""
        h = SessionHistory()
        # Seed alone is 500 tokens, budget is 100
        h.reset(seed="a" * 2000)
        assert h.content_tokens() == 0
        # Should not compact even though estimated_tokens (500) > budget (100)
        assert not h.should_compact(100)

        # Adding 60 tokens of translations: content_tokens = 60 < 100
        h.append("a" * 120, "b" * 120)
        assert not h.should_compact(100)

        # Adding another 60 tokens: content_tokens = 120 >= 100
        h.append("a" * 120, "b" * 120)
        assert h.should_compact(100)


class TestHandoffPrompt:
    """Assert the prompt's *structure*, not its wording.

    Wording belongs to whoever is tuning translation quality and gets
    rewritten often; pinning phrases would make every revision look like a
    broken test. What must not silently change is which sections are asked
    for — each one costs output tokens and is only worth asking when
    something downstream consumes it.
    """

    def test_summary_alone_when_the_style_is_fixed(self):
        prompt = handoff_prompt(with_style=False)
        assert "1." in prompt and "summary" in prompt.lower()
        assert "2." not in prompt

    def test_style_is_requested_by_default(self):
        """Only a user-supplied style turns it off."""
        assert "style" in handoff_prompt().lower()

    def test_style_is_asked_for_when_the_user_has_not_fixed_one(self):
        prompt = handoff_prompt(with_style=True)
        assert "2." in prompt and "style" in prompt.lower()

    def test_a_user_style_is_not_asked_for(self):
        """It is already known, so asking wastes output tokens and invites
        the model to drift from it."""
        prompt = handoff_prompt(with_style=False)
        assert "style" not in prompt.lower()

    def test_the_style_request_is_scoped_to_deviations(self):
        """Unscoped, the section comes back restating defaults the model
        would follow anyway, which costs a line and says nothing."""
        assert "different from general translation" in handoff_prompt()

    def test_sections_are_numbered_from_one_without_gaps(self):
        prompt = handoff_prompt(with_style=True)
        assert prompt.index("1.") < prompt.index("2.")
        assert "3." not in prompt


class TestHandoffReport:
    def test_persists_and_reloads(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="so far").append_to(path)
        assert "so far" in path.read_text(encoding="utf-8")

    def test_appends_without_clobbering(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="first").append_to(path)
        HandoffReport(window=2, summary="second").append_to(path)
        body = path.read_text(encoding="utf-8")
        assert "first" in body and "second" in body

    def test_seed_text_carries_the_summary(self):
        seed = HandoffReport(window=1, summary="so far").seed_text()
        assert "so far" in seed

    def test_latest_seed_reads_back_the_last_window(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="first").append_to(path)
        HandoffReport(window=2, summary="second").append_to(path)
        assert "second" in HandoffReport.latest_seed(path)

    def test_latest_seed_of_missing_file_is_empty(self, tmp_path):
        assert HandoffReport.latest_seed(tmp_path / "nope.md") == ""

    def test_seed_text_carries_glossary_lines(self):
        seed = HandoffReport(window=1, summary="summary", glossary_lines="Boxer → 拳击手").seed_text()
        assert "Boxer → 拳击手" in seed

