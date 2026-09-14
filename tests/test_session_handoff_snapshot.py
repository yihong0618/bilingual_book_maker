"""The 260913 handoff redesign: a bounded seed, and a snapshot on disk.

What these defend, in one sentence each.

* A window that is seeded with an unbounded report is a death loop — the seed
  alone reaches the budget, so every unit compacts, and the run pays for a
  handoff report per paragraph while translating almost nothing. Three layers
  bound it and only the last one, client-side truncation, cannot be ignored
  by the model; the structural invariant (cap < floor) is what makes the loop
  impossible rather than unlikely.
* `--context-compact-at` bounds the TOTAL window, seed included. Owner-pinned,
  because operators set it to a model's input limit.
* `<book>_handoff.md` is one overwritten snapshot, so its size is the size of
  the current handoff however long the book is, and a resumed run can read it
  back.
* The compact reply is untrusted. Cheap models answer it with anything at all,
  and nothing they can answer with may corrupt memory, the snapshot, or the
  window.

Design: docs/260913-feat-SESSION_HANDOFF_SNAPSHOT_SEED_BOUNDS.md.
"""

import os
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from openai import BadRequestError

from book_maker.cli import MIN_COMPACT_BUDGET
from book_maker.glossary import Glossary
from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.session_context import (
    GLOSSARY_MAX_PER_COMPACT,
    SEED_CAP_TOKENS,
    SEED_MAX_TOKENS,
    HandoffReport,
    SessionHistory,
    WindowText,
    estimate_tokens,
    handoff_prompt,
    parse_handoff_glossary,
    parse_snapshot,
    seed_cap,
    split_handoff_sections,
    trim_handoff_prose,
)
from book_maker.translator.capabilities import REASONING_ALLOWANCE_TOKENS
from book_maker.translator.chatgptapi_translator import ChatGPTAPI


def _completion(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))
        ],
        usage=None,
    )


def _translator(replies=None, **kwargs):
    """A ChatGPTAPI in session mode wired to a scripted client. No network."""
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    kwargs.setdefault("context_compact_at", 1500)
    t = ChatGPTAPI(key="k", language="Chinese", **kwargs)
    t.model = "test-model"
    t.capabilities.record("test-model", "unsupported")

    sent = []
    answers = iter(replies or [])

    def create(**call):
        sent.append(call)
        try:
            content = next(answers)
        except StopIteration:
            content = "译文"
        return _completion(content)

    t.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=Mock(side_effect=create), parse=Mock(side_effect=create)
            )
        )
    )
    t.sent = sent
    return t


# The largest a seed may measure. The cap bounds the *report*; the line
# introducing it is a fixed ~40 tokens of ours on top (see `seed_text`), and
# it is measured here rather than guessed so a reworded preamble cannot
# quietly loosen every cap assertion below. The +1 is the estimator itself:
# it rounds once over the whole string, so the parts can sum a token short.
# Measured over a one-character summary, because a seed with no summary at
# all is the empty string: since the 260913 ruling the preamble introduces a
# report or is not sent.
PREAMBLE_TOKENS = estimate_tokens(HandoffReport(window=1, summary="x").seed_text())
MAX_SEED_TOKENS = SEED_CAP_TOKENS + PREAMBLE_TOKENS + 1


def _bad_request(message):
    response = SimpleNamespace(
        status_code=400, headers={}, request=None, json=lambda: {}
    )
    return BadRequestError(message, response=response, body=None)


# --------------------------------------------------------- the budget's reach


class TestTheBudgetBoundsTheWholeWindow:
    def test_compact_budget_bounds_the_total_window(self):
        """Owner ruling 260913, pinned here so it cannot drift back.

        `--context-compact-at` is sometimes set to a model's maximum input
        size, so everything the request will carry has to count against it —
        the handoff seed that opens the window included. Counting only the
        content added since the reset (upstream PR #569's fix) silently
        overshoots that limit by the size of the seed, and the overshoot
        grows with every window.

        The death loop #569 was chasing is real; the answer is to bound the
        seed (below), not to stop counting it.
        """
        history = SessionHistory()
        seed = "handoff report " * 20
        history.reset(seed=seed)
        assert history.estimated_tokens() == estimate_tokens(seed)
        # a budget just under the seed is already reached, with no content
        assert history.should_compact(estimate_tokens(seed))

    def test_the_give_up_guard_is_on_the_same_number(self):
        """The compact failure path gives up past 2x the budget, and reads
        the same total-window number — a guard measured against content only
        would never fire on a window that is mostly seed."""
        history = SessionHistory()
        history.reset(seed="x " * 400)
        assert history.estimated_tokens() > 0
        assert not history.should_compact(0)  # 0 is "no budget", not "always"


class TestTheGiveUpGuardWaitsForASecondFailure:
    """A window past 2x its budget is not on its own evidence of trouble.

    At the floor budget a single grouped exchange runs ~2400 estimated
    tokens, so one unit can clear 2 x 1500 through granularity alone. The
    size arm firing on the first failure therefore threw a healthy window
    away whenever one compact request happened to be flaky — the precise
    thing the retry exists to prevent. The arm still fires; it just waits
    for the failure to repeat, which costs one paragraph and proves it.
    """

    BUDGET = MIN_COMPACT_BUDGET

    def _overshot(self):
        t = _translator(context_compact_at=self.BUDGET)
        t.session.reset(seed="")
        t.session.append("x " * 4 * self.BUDGET, "y " * 4 * self.BUDGET)
        assert t.session.estimated_tokens() > 2 * self.BUDGET
        return t

    def test_the_first_failure_at_an_overshot_window_retries(self):
        t = self._overshot()
        before = t.session.messages()
        t._compact_failed("no report", self.BUDGET)
        assert t.session.messages() == before  # window kept
        assert t._compact_failures == 1

    def test_the_second_failure_gives_up(self):
        t = self._overshot()
        t._compact_failed("no report", self.BUDGET)
        t._compact_failed("no report", self.BUDGET)
        assert t.session.messages() == []  # window thrown away, seeded empty
        assert t._compact_failures == 0

    def test_a_definitive_refusal_still_gives_up_on_the_first(self):
        """The anthropic route's "this history is too long" is the endpoint
        saying retrying cannot work. That is evidence; a size estimate is
        not, and only the estimate was made patient."""
        t = self._overshot()
        t._compact_failed("history too long", self.BUDGET, force_give_up=True)
        assert t.session.messages() == []

    def test_a_window_inside_its_budget_still_retries_twice(self):
        """The patience is not unbounded — COMPACT_ATTEMPTS still ends it."""
        t = _translator(context_compact_at=self.BUDGET)
        t.session.reset(seed="")
        t.session.append("x " * 20, "y " * 20)
        for _ in range(t.COMPACT_ATTEMPTS - 1):
            t._compact_failed("no report", self.BUDGET)
            assert t.session.messages() != []
        t._compact_failed("no report", self.BUDGET)
        assert t.session.messages() == []


# ------------------------------------------------------------- the seed bound


class TestTheSeedIsBounded:
    def test_seed_is_truncated_head_first_to_cap(self, tmp_path, capsys):
        """Layer (c): the client cut, which assumes the other two were
        ignored. Head first because a report front-loads what matters, and at
        a line boundary so a window is never seeded with half a sentence.

        The cut reaches the summary and only the summary — a style is a
        standing instruction, and it travels on the system channel and into
        the handoff file rather than through here, so there is nothing in the
        seed that truncation must not touch.
        """
        summary = "\n".join(f"line {n} " + "word " * 30 for n in range(200))
        report = HandoffReport(
            window=2, summary=summary, style_note="Clipped, no adverbs."
        )
        seed = report.seed_text(SEED_CAP_TOKENS)

        assert estimate_tokens(seed) <= MAX_SEED_TOKENS
        assert "Clipped, no adverbs." not in seed
        # the head of the summary is there and the tail is gone
        assert "line 0 " in seed
        assert "line 199 " not in seed
        # cut at a line boundary: every line that is there is a whole one
        kept = [line for line in seed.split("\n") if line.startswith("line ")]
        assert kept and all(line.rstrip().endswith("word") for line in kept)
        assert "seed cap" in capsys.readouterr().out

        # the style note still reaches the file, verbatim and uncut
        path = tmp_path / "book_handoff.md"
        report.write_snapshot(path)
        assert "Clipped, no adverbs." in path.read_text(encoding="utf-8")

    def test_a_report_inside_the_cap_is_untouched(self, capsys):
        report = HandoffReport(window=2, summary="They walked to the farm.")
        assert "They walked to the farm." in report.seed_text(SEED_CAP_TOKENS)
        assert "seed cap" not in capsys.readouterr().out

    def test_the_cap_is_never_more_than_half_the_window(self):
        assert seed_cap(600) == 300
        assert seed_cap(100_000) == SEED_CAP_TOKENS

    def test_no_death_loop_at_the_floor(self):
        """The structural claim, not a number: a window opened at the
        smallest budget the CLI accepts still has room for a unit after its
        seed, so a compaction can never be immediately followed by another.

        This is what makes the loop impossible rather than unlikely, and it
        is an invariant between two constants that are owned by different
        rulings — which is exactly the pair that drifts apart unwatched.
        """
        assert SEED_CAP_TOKENS < MIN_COMPACT_BUDGET
        room = MIN_COMPACT_BUDGET - seed_cap(MIN_COMPACT_BUDGET)
        # a paragraph and its translation, comfortably
        assert room >= 500

        history = SessionHistory()
        history.reset(seed="x" * (4 * seed_cap(MIN_COMPACT_BUDGET)))
        assert not history.should_compact(MIN_COMPACT_BUDGET)

    def test_the_truncation_counter_is_readable(self):
        """Kept as state for the follow-on health guard: one long report is
        a long report, three in a row is a model that cannot hold to the
        size it was asked for."""
        from book_maker.session_context import seed_truncations

        seed_truncations.reset()
        long_report = HandoffReport(window=1, summary="word " * 4000)
        long_report.seed_text(SEED_CAP_TOKENS)
        long_report.seed_text(SEED_CAP_TOKENS)
        assert seed_truncations.consecutive == 2
        HandoffReport(window=2, summary="short").seed_text(SEED_CAP_TOKENS)
        assert seed_truncations.consecutive == 0
        seed_truncations.reset()


class TestTheCutAlwaysLeavesSomethingToRead:
    """Codex review 260913, three findings that share a cause: the cut was
    reasoned about in lines and characters, and neither is what the cap
    counts."""

    def test_one_overlong_cjk_line_is_cut_to_the_cap(self):
        """The fallback converted the cap to characters with the *latin*
        ratio, so a CJK line kept 2048 characters — about 1205 estimated
        tokens against a 512 cap, which is the seed the cap exists to
        prevent."""
        seed = HandoffReport(window=2, summary="拿" * 3000).seed_text(SEED_CAP_TOKENS)
        assert estimate_tokens(seed) <= MAX_SEED_TOKENS
        assert "拿" in seed

    def test_a_heading_over_one_long_paragraph_still_says_something(self):
        """`## Summary` fits, the paragraph under it does not, and the
        fallback did not fire because something *had* been kept — so the next
        window was seeded with a heading and nothing under it."""
        paragraph = "They walked to the farm. " * 400
        report = HandoffReport(window=2, summary=f"## Summary\n\n{paragraph}")
        seed = report.seed_text(SEED_CAP_TOKENS)
        assert estimate_tokens(seed) <= MAX_SEED_TOKENS
        assert "They walked to the farm." in seed

    @pytest.mark.parametrize(
        "text",
        (
            "## Summary\n" + "拿" * 3000,
            "## Summary\n\n" + "They walked to the farm. " * 400,
            "拿" * 3000,
            "## Summary\n\n### Style\n",
            "\n".join(f"line {n} " + "word " * 30 for n in range(200)),
            "x",
            "",
        ),
    )
    @pytest.mark.parametrize("cap", (512, 100, 7, 1))
    def test_the_result_never_exceeds_the_cap(self, text, cap):
        """The postcondition, stated as one: whatever the shape of the
        report and whatever the cap, what comes back fits.

        Codex review 260913 found two ways it did not, both from measuring
        parts separately — each rounded on its own, and the newline joining
        them counted by neither. `"## Summary\\n" + 3000 CJK characters`
        came back at 513 against a cap of 512. The size is settled on the
        final joined string now, which is the only string that matters.
        """
        from book_maker.session_context import truncate_seed

        assert estimate_tokens(truncate_seed(text, cap)) <= cap

    def test_a_whole_report_of_headings_is_not_a_seed(self):
        """Nothing substantive at all: there is no first real line to fall
        back to, and the seed is whatever fits rather than an exception."""
        report = HandoffReport(window=2, summary="## Summary\n\n### Style\n")
        assert report.seed_text(SEED_CAP_TOKENS)


class TestWhatCountsAsASectionLabel:
    """What the cosmetic trim treats as furniture, and only that.

    This used to be an input to whether a compact succeeded, and the codex
    review found what that costs: a list number alone read as a label, so a
    model that numbers the beats of its summary wrote a report made entirely
    of "labels", which was then refused on every route — a paid compaction
    per window and finally an unseeded reset. Since the owner's 260913 ruling
    nothing is refused for its shape, so being wrong here costs a stray line
    in a capped seed. It is still kept narrow, because the summary is what
    the next window reads."""

    @pytest.mark.parametrize(
        "line, is_label",
        (
            ("1. Napoleon seizes power", False),
            ("2. The animals rebuild the windmill", False),
            ("3. **Established renderings**:", True),
            ("2) Style:", True),
            ("**Established renderings**", True),
            ("## Summary", True),
            ("They walked to the farm.", False),
        ),
    )
    def test_a_number_alone_is_not_decoration(self, line, is_label):
        from book_maker.session_context import _is_block_label

        assert _is_block_label(line) is is_label

    def test_a_numbered_summary_survives_the_trim_whole(self):
        report = (
            "1. Napoleon seizes power\n"
            "2. The animals rebuild the windmill\n"
            "3. Boxer is sold to the knacker\n"
        )
        assert trim_handoff_prose(report) == report.strip()

    def test_a_numbered_summary_survives_a_compaction(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        report = "1. Napoleon seizes power\n2. The windmill is rebuilt\n"
        t = _translator(["译文", report], handoff_path=path)
        t.get_translation("a" * 6000)
        assert t.session.windows == 2
        assert "Napoleon seizes power" in path.read_text(encoding="utf-8")


class TestTheCompactRequestAsksForALength:
    def test_the_prompt_states_a_target_size(self):
        assert f"{300} tokens" in handoff_prompt()

    def test_compact_request_max_tokens_follows_capability(self):
        """Layer (b), through the capability ledger: ask with the cap, and
        when the endpoint refuses the field, learn that once rather than
        paying for a refused request on every window.

        The two spellings are not interchangeable — the gpt-5 and o-series
        families reject `max_tokens` and name `max_completion_tokens` — so
        the refusal walks down the list before giving the cap up.
        """
        t = _translator()
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {
            "max_tokens": SEED_MAX_TOKENS
        }

        calls = []

        def create(**call):
            calls.append(call)
            if "max_tokens" in call:
                raise _bad_request("Unsupported parameter: 'max_tokens'")
            return _completion("Summary: they walked.")

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        t._compact_session()

        # asked with the cap, refused, asked again with the other spelling —
        # which carries the reasoning allowance on top, see the next test
        assert "max_tokens" in calls[0]
        assert (
            calls[1].get("max_completion_tokens")
            == SEED_MAX_TOKENS + REASONING_ALLOWANCE_TOKENS
        )
        # and remembered: the next compact does not re-learn it
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {
            "max_completion_tokens": SEED_MAX_TOKENS + REASONING_ALLOWANCE_TOKENS
        }

    def test_the_reasoning_spelling_gets_room_to_think(self):
        """Live smoke 260913: every session cell on a reasoning model lost
        its first compaction. `max_completion_tokens` budgets the
        hidden reasoning *and* the reply, so a cap sized for the report alone
        was spent thinking and the request came back empty — billed, no
        report, the window kept and retried a paragraph later.

        The allowance cannot loosen the seed bound: the client truncates
        whatever arrives at SEED_CAP_TOKENS, so a larger completion cap costs
        at most some completion tokens on one request per window.
        """
        t = _translator()
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {
            "max_tokens": SEED_MAX_TOKENS  # non-reasoning endpoints: no thinking
        }
        t.capabilities.note_compact_cap_rejected("test-model")
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {
            "max_completion_tokens": SEED_MAX_TOKENS + REASONING_ALLOWANCE_TOKENS
        }
        assert REASONING_ALLOWANCE_TOKENS > SEED_CAP_TOKENS

    @pytest.mark.parametrize(
        "message, verdict",
        (
            (
                "Unsupported parameter: 'max_tokens' is not supported with "
                "this model. Use 'max_completion_tokens' instead.",
                "max_tokens",
            ),
            ("Unknown parameter: 'max_completion_tokens'.", "max_tokens"),
            ("max_tokens is too large: 500 > limit", "other"),
            (
                "This model's maximum context length is 8192 tokens; "
                "max_tokens exceeds what is left",
                "other",
            ),
            # codex review 260913, the residual: a *value* complaint worded
            # with the same "not supported" the refusals use. The endpoint
            # plainly understands the field, so the cap stays.
            (
                "Unsupported value: max_tokens=0 is not supported; must be "
                "greater than zero",
                "other",
            ),
            ("Invalid value for 'max_tokens': must be at least 1", "other"),
        ),
    )
    def test_only_a_refused_parameter_gives_the_cap_up(self, message, verdict):
        """Codex review 260913: any 400 naming the field counted as a
        refusal of it, so an unrelated size complaint stripped the cap for
        the rest of the run. A complaint about the *value* confirms the
        endpoint understands the field."""
        from book_maker.translator.capabilities import classify_bad_request

        assert classify_bad_request(_bad_request(message)) == verdict

    def test_a_size_complaint_does_not_demote_the_spelling(self):
        t = _translator()

        def create(**call):
            raise _bad_request("max_tokens is too large: 500 > limit")

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        t._compact_session()  # the failure path, not the demotion path
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {
            "max_tokens": SEED_MAX_TOKENS
        }

    def test_an_endpoint_that_takes_no_cap_is_asked_without_one(self):
        t = _translator()
        for _ in range(2):
            t.capabilities.note_compact_cap_rejected("test-model")
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {}

        calls = []

        def create(**call):
            calls.append(call)
            return _completion("Summary: they walked.")

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        t._compact_session()
        assert "max_tokens" not in calls[0]
        assert "max_completion_tokens" not in calls[0]


# ------------------------------------------------------------- the snapshot


class TestTheHandoffFileIsASnapshot:
    def test_handoff_file_is_a_snapshot_not_a_log(self, tmp_path):
        """Three compactions, one section, and a size that does not grow
        with the window count. The append log this replaces wrote the whole
        established vocabulary again on every compaction, so the file grew
        with the square of the book's length."""
        path = tmp_path / "book_handoff.md"
        glossary = "Boxer → 拳击手\nClover → 三叶草\n"
        sizes = []
        for window in (1, 2, 3):
            HandoffReport(
                window=window,
                summary=f"Window {window} happened.",
                glossary_lines=glossary,
            ).write_snapshot(path)
            sizes.append(len(path.read_bytes()))

        body = path.read_text(encoding="utf-8")
        assert body.count("<!-- bbm:summary -->") == 1
        assert body.count("## Established renderings") == 1
        assert "Window 3 happened." in body
        assert "Window 1 happened." not in body
        # the only difference between snapshots is the window number
        assert max(sizes) - min(sizes) < 10

    def test_the_snapshot_carries_no_orphan_heading(self, tmp_path):
        """Models mirror the compact prompt's own numbering back into the
        report. Six of six cells in the 260913 eval emitted `3. **Established
        renderings**:` above their block, and it survived the strip — so
        every handoff file carried it, introducing nothing, above the
        canonical section."""
        report_text = (
            "They walked to the farm.\n\n"
            "3. **Established renderings**:\n\n"
            "<renderings>\nBoxer → 拳击手\n</renderings>\n"
        )
        parsed = parse_handoff_glossary(report_text, target_language="Chinese")
        path = tmp_path / "book_handoff.md"
        HandoffReport(
            window=1,
            summary=trim_handoff_prose(report_text),
            glossary_lines=parsed.glossary.to_lines(),
        ).write_snapshot(path)

        body = path.read_text(encoding="utf-8")
        assert "3. **Established renderings**" not in body
        assert body.count("Established renderings") == 1
        assert "They walked to the farm." in body

    def test_snapshot_write_is_atomic(self, tmp_path, monkeypatch):
        """A crash between writing the temp file and moving it into place
        must leave the previous snapshot standing. The alternative — writing
        the file in place — loses a good handoff to a full disk."""
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="The good one.").write_snapshot(path)

        def explode(*_args, **_kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(os, "replace", explode)
        with pytest.raises(OSError):
            HandoffReport(window=2, summary="The half-written one.").write_snapshot(
                path
            )

        assert "The good one." in path.read_text(encoding="utf-8")
        assert parse_snapshot(path).summary == "The good one."
        assert not (tmp_path / "book_handoff.md.tmp").exists()

    def test_old_append_log_handoff_is_ignored_with_warning(self, tmp_path, capsys):
        """A file written by the pre-260913 tool is a log of every window.
        Its last section is a report with no way to tell it from a truncated
        tail, so it is refused rather than parsed — once, out loud, and
        without raising."""
        path = tmp_path / "book_handoff.md"
        path.write_text(
            "\n## Window 1 — 2026-09-01 10:00:00Z\n\nThey walked.\n"
            "\n## Window 2 — 2026-09-01 11:00:00Z\n\nThey rested.\n",
            encoding="utf-8",
        )
        assert parse_snapshot(path) is None
        out = capsys.readouterr().out
        assert "older version" in out
        assert out.count("book_handoff.md") == 1

    def test_a_file_that_is_not_a_snapshot_at_all_is_ignored(self, tmp_path, capsys):
        path = tmp_path / "book_handoff.md"
        path.write_text("notes I typed myself\n", encoding="utf-8")
        assert parse_snapshot(path) is None
        assert "not a handoff snapshot" in capsys.readouterr().out

    def test_an_empty_file_is_ignored_quietly(self, tmp_path, capsys):
        path = tmp_path / "book_handoff.md"
        path.write_text("", encoding="utf-8")
        assert parse_snapshot(path) is None
        assert capsys.readouterr().out == ""

    def test_a_file_in_another_encoding_is_ignored_not_raised(self, tmp_path, capsys):
        """Codex review 260913: the read caught OSError only, so a
        hand-edited file saved as latin-1 raised UnicodeDecodeError out of
        the resume path and ended the run. The operator believes this file is
        being read, so it is a line rather than silence."""
        path = tmp_path / "book_handoff.md"
        path.write_bytes("résumé of the window\n".encode("latin-1"))
        assert parse_snapshot(path) is None
        assert "not valid UTF-8" in capsys.readouterr().out


# ---------------------------------------------------------------- the harvest


class TestTheHarvestIsBounded:
    def test_compact_harvest_is_capped_per_report(self, tmp_path, capsys):
        """The model is never shown what it established — a term reaches its
        context only when it matches the unit being translated — so it cannot
        deduplicate against the record and can re-emit renderings every
        compact. The cap is on the report, not on the total."""
        over = GLOSSARY_MAX_PER_COMPACT + 8
        lines = "\n".join(f"Name{n} → 名字{n}" for n in range(over))
        report_text = f"They walked.\n\n<renderings>\n{lines}\n</renderings>\n"

        # the unit names every one of them, so grounding keeps them all and
        # the cap is the only thing doing any work here
        unit = " ".join(f"Name{n}" for n in range(over)) + " " + "a" * 6000

        t = _translator(["译文", report_text], glossary_auto=True)
        t.get_translation(unit)

        assert len(t.learned) == GLOSSARY_MAX_PER_COMPACT
        # the head, because a report front-loads what matters
        assert t.learned.lookup("Name0") is not None
        assert t.learned.lookup(f"Name{over - 1}") is None
        # one line about it, not one per dropped entry
        out = capsys.readouterr().out
        assert out.count("line(s) ignored") == 1

    def test_a_replacement_inside_the_cap_still_replaces(self, tmp_path):
        first = "They walked.\n\n<renderings>\nBoxer → 拳击手\n</renderings>\n"
        second = "They rested.\n\n<renderings>\nBoxer → 鲍克瑟\n</renderings>\n"
        t = _translator(["译文", first, "译文", second], glossary_auto=True)
        t.get_translation("Boxer pulled. " + "a" * 6000)
        t.get_translation("Boxer rested. " + "b" * 6000)
        assert t.learned.lookup("Boxer").translation == "鲍克瑟"

    def test_compact_prompt_states_the_per_report_cap(self):
        """The prompt says how many renderings one report may carry, and
        asks for new or changed ones only. It never carries a list of what
        is established — that list is what used to ride in the seed, and
        putting it back in the prompt would be the same unbounded growth by
        another door."""
        prompt = handoff_prompt(with_glossary=True)
        assert f"At most {GLOSSARY_MAX_PER_COMPACT}" in prompt
        assert "new or has changed" in prompt
        assert "already reported" in prompt
        # the prompt is a constant: the same text on window 1 and window 40
        assert handoff_prompt(with_glossary=True) == prompt

    def test_reversed_pair_is_flipped_at_harvest(self):
        """Seen live from deepseek beside correct pairs, and from luna as a
        whole 16-of-16 block under the correct instruction. Normalised, not
        dropped (owner ruling 260913): both halves are there, so the pair is
        stored the right way round — and only then can it ever match, since
        matching runs against source text."""
        text = (
            "They walked.\n\n<renderings>\n"
            "利维坦 → Leviathan\n"
            "Boxer → 拳击手\n"
            "</renderings>\n"
        )
        parsed = parse_handoff_glossary(text, target_language="Chinese")
        assert parsed.flipped == 1
        assert parsed.glossary.lookup("Leviathan").translation == "利维坦"
        assert parsed.glossary.lookup("利维坦") is None
        # and it now fires on English source text, which is the point
        assert [e.term for e in parsed.glossary.matches("the Leviathan rose")] == [
            "Leviathan"
        ]

    def test_a_same_script_run_is_left_alone(self):
        """en->fr: the reversal is invisible, and guessing would invert an
        operator's whole glossary."""
        text = "Ils marchent.\n\n<renderings>\nfarm → ferme\n</renderings>\n"
        parsed = parse_handoff_glossary(text, target_language="French")
        assert parsed.flipped == 0
        assert parsed.glossary.lookup("farm").translation == "ferme"

    def test_a_chinese_to_english_run_is_not_read_as_reversed(self):
        text = "They walked.\n\n<renderings>\n利维坦 → Leviathan\n</renderings>\n"
        parsed = parse_handoff_glossary(text, target_language="english")
        assert parsed.flipped == 0
        assert parsed.glossary.lookup("利维坦").translation == "Leviathan"

    @pytest.mark.parametrize(
        "line, why",
        (
            ("Balaene → Balaene", "an identity pair instructs nothing"),
            ("Rebellion → 起义／反叛（依语境）", "a choice of renderings, not one"),
            ("Beasts of England → 英格兰兽／英伦兽歌", "the same with no qualifier"),
            ("whale → 鲸 / 鲸鱼", "the ascii spelling of the same thing"),
            ("shore → 海岸（视语境）", "a hedge rather than a rendering"),
        ),
    )
    def test_a_pair_that_makes_no_single_substitution_is_dropped(self, line, why):
        """The block is a verbatim-substitution instruction: there is nothing
        downstream that could choose between alternatives, and a term
        rendered as itself says nothing at all. All measured live, 260913."""
        text = f"They walked.\n\n<renderings>\n{line}\nBoxer → 拳击手\n</renderings>\n"
        parsed = parse_handoff_glossary(text, target_language="Chinese")
        assert len(parsed.glossary) == 1, why
        assert parsed.glossary.lookup("Boxer") is not None
        assert parsed.dropped == 1

    @pytest.mark.parametrize(
        "line, term",
        (
            ("WHO → Organisation mondiale de la santé (OMS)", "WHO"),
            ("Manor Farm → 庄园农场（曼诺农场）", "Manor Farm"),
        ),
    )
    def test_a_parenthesised_name_is_not_a_hedge(self, line, term):
        """Codex review 260913: any trailing parenthetical was read as a
        qualifier, which threw away renderings that carry their own
        abbreviation — exactly the names most worth keeping unified. The
        parenthetical has to say it is context-dependent to count."""
        text = f"They walked.\n\n<renderings>\n{line}\n</renderings>\n"
        parsed = parse_handoff_glossary(text, target_language="French")
        assert parsed.glossary.lookup(term) is not None
        assert parsed.dropped == 0


class TestAHarvestedPairMustBeInTheWindow:
    """Owner ruling 260913: grounding replaced the format heuristics.

    Once the reply is no longer judged for shape, this is what stands
    between a hallucinated pair and the glossary — and the glossary is the
    one place junk is expensive, because a stored term is injected into
    every later request whose text matches it. A pair the model observed has
    an end in the window: it read the term in a source, or it wrote the
    rendering into a translation. A pair it invented has neither.
    """

    WINDOW = WindowText.of(
        ["Boxer pulled the cart to the farm.", "Clover watched."],
        ["拳击手把车拉到农场。", "克拉弗看着。"],
    )

    def _parsed(self, lines, **kwargs):
        text = f"They walked.\n\n<renderings>\n{lines}\n</renderings>\n"
        return parse_handoff_glossary(text, window=self.WINDOW, **kwargs)

    def test_a_pair_with_neither_end_in_the_window_is_dropped(self):
        parsed = self._parsed("Napoleon → 拿破仑")
        assert parsed.glossary.lookup("Napoleon") is None
        assert parsed.ungrounded == 1

    def test_a_term_in_the_sources_is_kept(self):
        parsed = self._parsed("Boxer → 拳击手")
        assert parsed.glossary.lookup("Boxer").translation == "拳击手"
        assert parsed.ungrounded == 0

    def test_a_rendering_in_the_translations_is_enough_on_its_own(self):
        """Either end, deliberately: the model may report a term under a
        form the source never quite spells — an inflection, a possessive —
        and what it wrote into the translation is evidence just the same."""
        parsed = self._parsed("Clover's → 克拉弗")
        assert parsed.glossary.lookup("Clover's").translation == "克拉弗"
        assert parsed.ungrounded == 0

    def test_a_reversed_pair_is_flipped_before_it_is_grounded(self):
        """Flipping still runs first, and grounding no longer cares: both
        ends are looked for across the whole window, so a pair that arrived
        backwards is grounded either way and comes out the right way round."""
        parsed = self._parsed("拳击手 → Boxer", target_language="Chinese")
        assert parsed.flipped == 1
        assert parsed.glossary.lookup("Boxer").translation == "拳击手"
        assert parsed.ungrounded == 0

    def test_a_rendering_carried_in_on_the_seed_grounds_its_pair(self):
        """The window does not divide by language, so neither does the
        search (codex review 260913, P2). The seed arrives as a *user*
        message: the previous window's target-language renderings therefore
        sit on the source side, and a pair whose term this window only
        implies is still a pair the run established."""
        window = WindowText.of(
            ["Previously: 克拉弗 stayed by the gate.", "The mare grazed."],
            ["母马在吃草。"],
        )
        parsed = parse_handoff_glossary(
            "<renderings>\nClover → 克拉弗\n</renderings>\n", window=window
        )
        assert parsed.glossary.lookup("Clover").translation == "克拉弗"
        assert parsed.ungrounded == 0

    def test_a_name_left_untranslated_grounds_its_pair_too(self):
        """The other direction of the same fact: models keep "Boxer" as
        "Boxer" inside a Chinese sentence, so the SOURCE term is what turns
        up on the translation side."""
        window = WindowText.of(["The mare grazed by the gate."], ["Boxer 站在门口。"])
        parsed = parse_handoff_glossary(
            "<renderings>\nBoxer → 博克瑟\n</renderings>\n", window=window
        )
        assert parsed.glossary.lookup("Boxer").translation == "博克瑟"
        assert parsed.ungrounded == 0

    def test_grounding_folds_case_rather_than_lowering_it(self):
        """`"Straße".lower()` is still "straße", so a window shouting
        STRASSE would not match it; `casefold` is the one that does.

        The translation side deliberately does NOT contain the rendering:
        with it there the pair grounds through that end and the test
        passes with or without case folding, proving nothing."""
        window = WindowText.of(["THE HOUSE ON STRASSE 5."], ["他们沿着那条街走。"])
        parsed = parse_handoff_glossary(
            "<renderings>\nStraße → 斯特拉塞街\n</renderings>\n", window=window
        )
        assert parsed.glossary.lookup("Straße").translation == "斯特拉塞街"
        assert parsed.ungrounded == 0

    def test_without_a_window_nothing_is_grounded(self):
        """The stripper asks the parse which lines were read, not which
        survived, so it passes no window and this must not filter."""
        text = "They walked.\n\n<renderings>\nNapoleon → 拿破仑\n</renderings>\n"
        parsed = parse_handoff_glossary(text)
        assert parsed.glossary.lookup("Napoleon") is not None
        assert parsed.ungrounded == 0

    def test_the_window_is_read_from_the_session_before_the_reset(self):
        """End to end on the API route: a report naming a term the window
        never contained teaches the run nothing."""
        report = (
            "They walked.\n\n<renderings>\n"
            "Boxer → 拳击手\nNapoleon → 拿破仑\n"
            "</renderings>\n"
        )
        t = _translator(["译文", report], glossary_auto=True)
        t.get_translation("Boxer pulled the cart. " + "a" * 6000)
        assert t.learned.lookup("Boxer") is not None
        assert t.learned.lookup("Napoleon") is None


class TestTheReplyIsSplitIntoSections:
    """The ladder: the protocol headers, then shape, then all summary.

    Owner ruling 260913. A style note that lands in the summary costs a few
    tokens of a capped seed; a summary that lands in the style is missing
    from the seed entirely, which was the whole point of compacting. So
    every tie-break leans towards the summary.
    """

    HEADED = (
        "## Summary\n\nNapoleon took the farm.\n\n"
        "## Style\n\nPlain, no adverbs.\n\n"
        "## Renderings\n\n<renderings>\nBoxer → 拳击手\n</renderings>\n"
    )

    def test_the_headers_split_it_directly(self):
        summary, style = split_handoff_sections(self.HEADED)
        assert summary == "Napoleon took the farm."
        assert style == "Plain, no adverbs."

    def test_a_headed_reply_seeds_a_window_with_no_headings_in_it(self):
        report = HandoffReport(window=2, summary=trim_handoff_prose(self.HEADED))
        seed = report.seed_text()
        assert "Napoleon took the farm." in seed
        assert "## " not in seed
        assert "Boxer" not in seed

    def test_a_target_language_heading_is_trimmed_by_shape(self):
        """The headers are asked for in English, and a model writes its own
        in the book's language anyway. Nothing may depend on the words."""
        reply = (
            "Napoleon took the farm.\n\n"
            "## 术语表\n\n"
            "Boxer → 拳击手\nClover → 三叶草\n"
        )
        assert trim_handoff_prose(reply) == "Napoleon took the farm."

    def test_two_headerless_blocks_split_by_length(self):
        reply = (
            "Napoleon took the farm and the windmill was rebuilt twice.\n\n"
            "Plain, no adverbs.\n\n"
            "Boxer → 拳击手\n"
        )
        summary, style = split_handoff_sections(reply, with_style=True)
        assert summary.startswith("Napoleon took the farm")
        assert style == "Plain, no adverbs."

    def test_a_user_fixed_style_leaves_both_blocks_as_summary(self):
        """`with_style` is False when the operator fixed a style, and then
        nothing in a reply can be read as one — the note is a standing
        instruction, not something a model may erode a window at a time."""
        reply = (
            "Napoleon took the farm and the windmill was rebuilt twice.\n\n"
            "Plain, no adverbs.\n"
        )
        summary, style = split_handoff_sections(reply, with_style=False)
        assert "Napoleon took the farm" in summary
        assert "Plain, no adverbs." in summary
        assert style == ""

    def test_a_single_headerless_block_is_all_summary(self):
        reply = "Napoleon took the farm.\n\nBoxer → 拳击手\n"
        summary, style = split_handoff_sections(reply, with_style=True)
        assert "Napoleon took the farm." in summary
        assert style == ""

    def test_an_empty_fence_does_not_count_as_a_section(self):
        """The renderings come out by their arrows, and the fence lines with
        them — otherwise an empty block was one of the two "prose blocks"
        and the summary was filed as the style."""
        reply = (
            "Napoleon took the farm.\n\n<renderings>\nBoxer → 拳击手\n</renderings>\n"
        )
        summary, style = split_handoff_sections(reply, with_style=True)
        assert summary.startswith("Napoleon took the farm.")
        assert style == ""

    def test_the_user_fixed_note_is_what_the_report_carries(self):
        t = _translator(style_note="Clipped, no adverbs.")
        report = t._handoff_report(2, "Napoleon took the farm.\n\nBreezy.\n")
        assert report.style_note == "Clipped, no adverbs."
        assert "Breezy." in report.summary


class TestTheCompactPromptShowsATemplate:
    """Owner ruling 260913: a shown template, no numbered list.

    Models mirror numbering back into the report — six of six cells of the
    260913 eval emitted `3. **Established renderings**:` — and a number read
    back is a line of a capped seed spent on furniture.
    """

    def test_the_prompt_carries_the_three_english_headers(self):
        prompt = handoff_prompt(with_glossary=True, with_style=True)
        for header in ("## Summary", "## Style", "## Renderings"):
            assert header in prompt

    def test_the_prompt_numbers_no_sections(self):
        prompt = handoff_prompt(with_glossary=True, with_style=True)
        assert not [
            line for line in prompt.splitlines() if re.match(r"^\s{0,3}\d+[.)]\s", line)
        ]

    def test_a_section_that_is_not_asked_for_leaves_no_header(self):
        prompt = handoff_prompt(with_glossary=False, with_style=False)
        assert "## Summary" in prompt
        assert "## Style" not in prompt
        assert "## Renderings" not in prompt


class TestARelearnedTermReplaces:
    def test_relearned_term_replaces_old_rendering(self, tmp_path):
        """This window has read more of the book than the last one, so its
        reading wins — in memory and in the snapshot, once each."""
        path = tmp_path / "book_handoff.md"
        first = "They walked.\n\n<renderings>\nBoxer → 拳击手\n</renderings>\n"
        second = "They rested.\n\n<renderings>\nBoxer → 鲍克瑟\n</renderings>\n"
        t = _translator(
            ["译文", first, "译文", second], glossary_auto=True, handoff_path=path
        )
        t.get_translation("Boxer pulled. " + "a" * 6000)
        t.get_translation("Boxer rested. " + "b" * 6000)

        assert t.learned.lookup("Boxer").translation == "鲍克瑟"
        assert len(t.learned) == 1
        body = path.read_text(encoding="utf-8")
        assert body.count("Boxer → 鲍克瑟") == 1
        assert "拳击手" not in body

    def test_a_pin_beats_both_readings_in_memory_and_on_disk(self, tmp_path):
        """The artifact half of `test_a_pin_is_never_overwritten_by_what_was
        _learned`: #569 inverted this precedence exactly where the old test
        did not look, so the file is checked too."""
        path = tmp_path / "book_handoff.md"
        report = "They walked.\n\n<renderings>\nBoxer → 拳击手\n</renderings>\n"
        t = _translator(
            ["译文", report],
            glossary_auto=True,
            handoff_path=path,
            glossary=Glossary.parse("Boxer → 鲍克瑟\n"),
        )
        t.get_translation("Boxer pulled. " + "a" * 6000)

        assert t.glossary.lookup("Boxer").translation == "鲍克瑟"
        body = path.read_text(encoding="utf-8")
        assert "Boxer → 鲍克瑟" in body
        assert "Boxer → 拳击手" not in body


class TestTheSeedCarriesTheSummaryOnly:
    def test_seed_carries_summary_and_style_only(self):
        """Owner ruling 260913: the seed is the report's prose and nothing
        else — where the model was asked to describe a style, that prose is
        where it is.

        The renderings leave: they reach the model next to the unit that
        names them, through `prompt_block`, and replaying the whole list at
        the head of every window is what grew the seed without bound. A style
        the operator fixed never rode here in the first place; it is a
        standing instruction, and it stays on the standing channel.
        """
        report = HandoffReport(
            window=2,
            summary="They walked to the farm.",
            style_note="Clipped.",
            glossary_lines="Boxer → 拳击手\nClover → 三叶草\n",
        )
        seed = report.seed_text()
        assert "They walked to the farm." in seed
        assert "Clipped." not in seed
        assert "Boxer" not in seed and "拳击手" not in seed
        # both are recorded in the file instead
        assert "Boxer → 拳击手" in report.render()
        assert "Clipped." in report.render()

    def test_the_terms_still_reach_the_unit_that_names_them(self):
        glossary = Glossary.parse("Boxer → 拳击手\n")
        assert "拳击手" in glossary.prompt_block("Boxer pulled the cart")
        assert glossary.prompt_block("the windmill stood") == ""


# ------------------------------------------------------ the style that stands


LONG_UNIT = "Boxer pulled the cart to the farm. " + "a" * 6000


def _styled_report(style, summary="They walked to the farm."):
    return f"## Summary\n\n{summary}\n\n## Style\n\n{style}\n"


def _system_carrying(t, unit):
    """The system message of the request that carried this unit."""
    for call in t.sent:
        messages = call["messages"]
        if any(unit in (m.get("content") or "") for m in messages):
            return "\n".join(
                m.get("content") or "" for m in messages if m.get("role") == "system"
            )
    raise AssertionError(f"no request carried {unit[:30]!r}")


class TestTheObservedStyleStandsUntilItIsReplaced:
    """A style the run observed has to reach the model, not just the file.

    codex review 260913 (P2). Before the reply was split into sections the
    observed style rode the seed inside the summary, so the next window read
    it; splitting it out sent it to the snapshot and nowhere else, which made
    the whole `## Style` request write-only. It goes back on the standing
    channel — where a style belongs (owner-pinned decision 8) — and the
    operator's own `--prompt` style still outranks it absolutely.
    """

    def test_a_reported_style_reaches_the_next_window(self):
        t = _translator(["译文", _styled_report("Clipped, no adverbs."), "译文"])
        t.get_translation(LONG_UNIT)
        t.get_translation("Clover watched.")
        assert "Clipped, no adverbs." in _system_carrying(t, "Clover watched.")

    def test_the_newest_report_replaces_the_previous_one(self):
        """Snapshot semantics, like the glossary's: the model has read more
        of the book than it had last window, so its newest description wins
        outright rather than accumulating next to the old one."""
        t = _translator(
            [
                "译文",
                _styled_report("Clipped, no adverbs."),
                "译文",
                _styled_report("Formal, long sentences."),
                "译文",
            ]
        )
        t.get_translation(LONG_UNIT)
        t.get_translation(LONG_UNIT)
        t.get_translation("Clover watched.")
        system = _system_carrying(t, "Clover watched.")
        assert "Formal, long sentences." in system
        assert "Clipped" not in system

    def test_a_report_with_no_style_leaves_the_standing_one_alone(self):
        """Same as an empty renderings block: "nothing to add" is not "forget
        what you knew". A window the model describes in prose only must not
        silently drop the register the book has been translated in."""
        t = _translator(
            [
                "译文",
                _styled_report("Clipped, no adverbs."),
                "译文",
                "They reached the barn.",
                "译文",
            ]
        )
        t.get_translation(LONG_UNIT)
        t.get_translation(LONG_UNIT)
        t.get_translation("Clover watched.")
        assert "Clipped, no adverbs." in _system_carrying(t, "Clover watched.")

    def test_a_fixed_style_is_never_replaced_by_a_reply(self):
        """The structural guarantee. A standing instruction the model can
        erode a window at a time would not be standing — and with a fixed
        style the compact turn is not even asked for one, so anything that
        looks like a style section is just more of the summary."""
        t = _translator(
            ["译文", _styled_report("Clipped, no adverbs."), "译文"],
            style_note="Formal and old-fashioned.",
        )
        t.get_translation(LONG_UNIT)
        t.get_translation("Clover watched.")
        system = _system_carrying(t, "Clover watched.")
        assert "Formal and old-fashioned." in system
        assert "Clipped, no adverbs." not in system
        assert t.handoff_style == ""

    def test_the_snapshot_records_the_style_that_is_standing(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        t = _translator(
            [
                "译文",
                _styled_report("Clipped, no adverbs."),
                "译文",
                "They reached the barn.",
            ],
            handoff_path=path,
        )
        t.get_translation(LONG_UNIT)
        t.get_translation(LONG_UNIT)
        # the second report mentioned no style, so the file keeps the one the
        # run is actually translating under rather than emptying out
        assert "Clipped, no adverbs." in path.read_text(encoding="utf-8")


# ------------------------------------------------------------------- resume


def _loader(**kwargs):
    """An EPUBBookLoader with only what the restore gate reads.

    Built without `__init__` on purpose: opening a book would pull in the
    whole loader, and the gate is three attributes and a translator.
    """
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.resume = kwargs.get("resume", True)
    loader.context_mode = kwargs.get("context_mode", "session")
    loader.translate_model = kwargs["translate_model"]
    return loader


def _snapshot(path, **kwargs):
    kwargs.setdefault("window", 3)
    kwargs.setdefault("summary", "Napoleon took the farm.")
    HandoffReport(**kwargs).write_snapshot(path)
    return path


class TestResumeReadsTheSnapshotBack:
    def test_resume_reseeds_session_from_snapshot(self, tmp_path, capsys):
        """The owner's 260902 request, finally landing: `--resume` used to
        pick up the text and nothing else, so the second half of a book was
        translated with none of the terminology or register the first half
        established. `latest_seed()` had been dead code since #564."""
        path = _snapshot(tmp_path / "book_handoff.md", style_note="Clipped.")
        t = _translator(handoff_path=path)
        _loader(translate_model=t)._restore_session_handoff()

        seed = t.session.messages()[0]["content"]
        assert "Napoleon took the farm." in seed
        # the counter continues rather than restarting, so the seams the
        # operator sees keep counting up across the interruption
        assert t.session.windows == 4
        assert "resume: context window 4" in capsys.readouterr().out

    def test_resume_restores_learned_terms_only_with_glossary_auto(self, tmp_path):
        path = _snapshot(
            tmp_path / "book_handoff.md", glossary_lines="Boxer → 拳击手\n"
        )

        on = _translator(handoff_path=path, glossary_auto=True)
        _loader(translate_model=on)._restore_session_handoff()
        assert on.learned.lookup("Boxer").translation == "拳击手"
        assert on.glossary.lookup("Boxer").translation == "拳击手"

        # a run that did not ask for a derived glossary must not acquire one
        # from a file on disk, however many terms it holds
        off = _translator(handoff_path=path)
        _loader(translate_model=off)._restore_session_handoff()
        assert not off.learned
        assert "Napoleon took the farm." in off.session.messages()[0]["content"]

    def test_resume_puts_the_style_back_on_the_standing_channel(self, tmp_path):
        """The snapshot's style is restored the way its summary is — session
        plus `--resume`, never gated on `--glossary-auto`. A style is how the
        book reads, not a derived vocabulary an operator opts into, and the
        second half of a book should not change register at the seam."""
        path = _snapshot(tmp_path / "book_handoff.md", style_note="Clipped.")
        t = _translator(handoff_path=path)
        _loader(translate_model=t)._restore_session_handoff()
        assert t.handoff_style == "Clipped."
        assert "Clipped." in t.style_section()

    def test_a_fixed_style_outranks_the_restored_one(self, tmp_path):
        path = _snapshot(tmp_path / "book_handoff.md", style_note="Clipped.")
        t = _translator(handoff_path=path, style_note="Formal and old-fashioned.")
        _loader(translate_model=t)._restore_session_handoff()
        assert "Formal and old-fashioned." in t.style_section()
        assert "Clipped." not in t.style_section()

    def test_a_pin_still_wins_over_what_is_restored(self, tmp_path):
        path = _snapshot(
            tmp_path / "book_handoff.md", glossary_lines="Boxer → 拳击手\n"
        )
        t = _translator(
            handoff_path=path,
            glossary_auto=True,
            glossary=Glossary.parse("Boxer → 鲍克瑟\n"),
        )
        _loader(translate_model=t)._restore_session_handoff()
        assert t.glossary.lookup("Boxer").translation == "鲍克瑟"

    def test_the_restored_seed_is_capped_like_any_other(self, tmp_path):
        path = _snapshot(tmp_path / "book_handoff.md", summary="word " * 4000)
        t = _translator(handoff_path=path)
        _loader(translate_model=t)._restore_session_handoff()
        assert t.session.estimated_tokens() <= MAX_SEED_TOKENS

    @pytest.mark.parametrize(
        "gate",
        (
            {"resume": False},
            {"context_mode": "window"},
            {"resume": False, "context_mode": "window"},
        ),
    )
    def test_stale_handoff_never_leaks_into_a_fresh_run(self, tmp_path, gate, capsys):
        """A `<book>_handoff.md` beside a book is inert unless this run asked
        for it. #569 wired the restore without the gates, so a file left by
        an unrelated run seeded a fresh one — pinned here per gate."""
        path = _snapshot(
            tmp_path / "book_handoff.md", glossary_lines="Boxer → 拳击手\n"
        )
        t = _translator(handoff_path=path, glossary_auto=True)
        _loader(translate_model=t, **gate)._restore_session_handoff()

        if t.session is not None:
            assert t.session.messages() == []
            assert t.session.windows == 1
        assert not t.learned
        assert "resume:" not in capsys.readouterr().out

    def test_a_missing_snapshot_is_not_an_error(self, tmp_path, capsys):
        t = _translator(handoff_path=tmp_path / "nothing_handoff.md")
        capsys.readouterr()  # the translator's own setup lines
        _loader(translate_model=t)._restore_session_handoff()
        assert t.session.messages() == []
        assert capsys.readouterr().out == ""

    def test_an_old_log_does_not_seed_a_resumed_run(self, tmp_path, capsys):
        path = tmp_path / "book_handoff.md"
        path.write_text(
            "\n## Window 1 — 2026-09-01 10:00:00Z\n\nThey walked.\n", encoding="utf-8"
        )
        t = _translator(handoff_path=path)
        _loader(translate_model=t)._restore_session_handoff()
        assert t.session.messages() == []
        assert "older version" in capsys.readouterr().out


# ------------------------------------------------- what a cheap model answers


JUNK_REPLIES = {
    "empty": "",
    "whitespace": "   \n\n\t  ",
    "prompt echo": handoff_prompt(with_glossary=True),
    "prompt echo with headings": (
        "1. **Summary**\n\n2. **Style**\n\n3. **Established renderings**\n"
    ),
    "glossary only": "<renderings>\nBoxer → 拳击手\n</renderings>\n",
    "non-pair glossary junk": (
        "<renderings>\n"
        "Here are the terms I would keep unified in this chapter, "
        "if I had to choose some.\n"
        "I cannot comply with this request.\n"
        "Boxer\n"
        "</renderings>\n"
    ),
    "punctuation": "!!!!! ??? ---- ****",
}


class TestAJunkCompactReplyDegradesSafely:
    """Owner ruling 260913: the compact reply is NEVER judged for format.

    Owner emphasis, same discussion: "especially for some cheap models all
    kinds of stuff can happen". The earlier answer was to recognise a report
    and refuse anything else — and that cost more than it saved, because the
    recogniser was wrong about legitimate reports (a numbered summary, a
    report under one heading) and every mistake threw a whole window away for
    a paid compaction that produced nothing.

    So the bar is emptiness and nothing else. Junk compacts: it is trimmed by
    shape, bounded by the cap, and seeds the next window, where it is one
    short paragraph of nonsense in front of a fresh translation — and the
    window after that replaces it. What junk must never do is reach the
    *glossary*, where a stored term is injected into every later request that
    matches it, or overwrite a good snapshot with nothing.
    """

    @pytest.mark.parametrize("label", ("empty", "whitespace"))
    def test_an_empty_reply_is_the_one_failure(self, tmp_path, label, capsys):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="The good one.").write_snapshot(path)
        good = path.read_text(encoding="utf-8")

        t = _translator(["译文", JUNK_REPLIES[label]], handoff_path=path)
        assert t.get_translation("a" * 6000) == "译文"

        # the window is kept for a retry rather than reset onto nothing
        assert t.session.windows == 1
        assert t.session.estimated_tokens() > 0
        assert path.read_text(encoding="utf-8") == good
        assert "handoff report failed" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "label",
        sorted(set(JUNK_REPLIES) - {"empty", "whitespace"}),
    )
    def test_junk_that_is_not_empty_compacts_anyway(self, tmp_path, label):
        path = tmp_path / "book_handoff.md"
        HandoffReport(
            window=1,
            summary="The good one: Napoleon took the farm.",
            glossary_lines="Napoleon → 拿破仑\n",
        ).write_snapshot(path)

        t = _translator(
            ["译文", JUNK_REPLIES[label]], glossary_auto=True, handoff_path=path
        )
        # no exception, the paragraph is translated, the window rolls over
        assert t.get_translation("a" * 6000) == "译文"
        assert t.session.windows == 2

        # whatever it said, the seed is bounded — that is what makes seeding
        # junk affordable rather than a death loop
        assert t.session.estimated_tokens() <= MAX_SEED_TOKENS

        # and nothing junk was learned: none of it is in the window
        for term in ("Here", "I cannot comply with this request", "Boxer", "!!!!!"):
            assert t.glossary.lookup(term) is None

        # the file on disk is still a snapshot a resume can read
        assert parse_snapshot(path) is not None

    @pytest.mark.parametrize(
        "reply",
        (
            "Boxer → 拳击手\nClover → 三叶草\n",
            "## Summary\n\nBoxer → 拳击手\nClover → 三叶草\n",
            "### Established renderings\n\nBoxer → 拳击手\n",
        ),
    )
    def test_a_reply_of_only_renderings_keeps_the_previous_summary(
        self, tmp_path, reply
    ):
        """Owner ruling 260913, which resolved this by design rather than by
        a guard: such a reply is not refused, it simply has no prose in it.
        The renderings are harvested, the trim leaves nothing, and an empty
        summary is the one thing `write_snapshot` will not write — so the
        good snapshot stands and the next window opens unseeded, which is the
        ordinary rollover shape rather than a failure.
        """
        path = tmp_path / "book_handoff.md"
        HandoffReport(
            window=1, summary="The good one.", glossary_lines="Napoleon → 拿破仑\n"
        ).write_snapshot(path)
        good = path.read_bytes()

        t = _translator(["译文", reply], glossary_auto=True, handoff_path=path)
        assert t.get_translation("a" * 6000) == "译文"

        assert path.read_bytes() == good  # not overwritten with nothing
        assert t.session.windows == 2  # but the compaction did happen
        assert t.session.messages() == []  # unseeded, having nothing to say

    def test_an_empty_summary_is_never_written(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="The good one.").write_snapshot(path)
        assert HandoffReport(window=2, summary="   ").write_snapshot(path) is False
        assert "The good one." in path.read_text(encoding="utf-8")

    def test_a_junk_summary_that_is_not_empty_is_written(self, tmp_path):
        """The other half of the same ruling, pinned so it cannot drift back
        into a quality test: what is written is whatever there was."""
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="The good one.").write_snapshot(path)
        assert HandoffReport(window=2, summary="!!!!! ???").write_snapshot(path) is True
        assert "!!!!! ???" in path.read_text(encoding="utf-8")

    def test_an_oversized_blob_is_a_report_and_is_cut(self, tmp_path, capsys):
        """The one reply in this family that *is* a report: a real summary,
        ten times too long. It compacts normally and the seed is truncated —
        max_tokens is assumed to have been ignored, because it was."""
        path = tmp_path / "book_handoff.md"
        blob = "\n".join(f"They walked to the farm, part {n}." for n in range(500))
        t = _translator(["译文", blob], handoff_path=path)
        t.get_translation("a" * 6000)

        assert t.session.windows == 2
        assert t.session.estimated_tokens() <= MAX_SEED_TOKENS
        assert "seed cap" in capsys.readouterr().out
        assert "part 0" in path.read_text(encoding="utf-8")

    def test_a_report_with_no_summary_does_not_overwrite_the_snapshot(self, tmp_path):
        """Checked on the writer too, not only on the reply: whatever reaches
        it, a snapshot with nothing in it is worth less than the one already
        on disk."""
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="The good one.").write_snapshot(path)
        assert HandoffReport(window=2, summary="   ").write_snapshot(path) is False
        assert "The good one." in path.read_text(encoding="utf-8")
