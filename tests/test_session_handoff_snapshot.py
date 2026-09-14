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
    estimate_tokens,
    handoff_prompt,
    parse_handoff_glossary,
    parse_snapshot,
    seed_cap,
)
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

        assert estimate_tokens(seed) <= SEED_CAP_TOKENS + 40  # + the preamble
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

        # asked with the cap, refused, asked again with the other spelling
        assert "max_tokens" in calls[0]
        assert calls[1].get("max_completion_tokens") == SEED_MAX_TOKENS
        # and remembered: the next compact does not re-learn it
        assert t.capabilities.compact_cap_kwargs("test-model", SEED_MAX_TOKENS) == {
            "max_completion_tokens": SEED_MAX_TOKENS
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
        from book_maker.session_context import strip_handoff_glossary

        report_text = (
            "They walked to the farm.\n\n"
            "3. **Established renderings**:\n\n"
            "<renderings>\nBoxer → 拳击手\n</renderings>\n"
        )
        parsed = parse_handoff_glossary(report_text, target_language="Chinese")
        path = tmp_path / "book_handoff.md"
        HandoffReport(
            window=1,
            summary=strip_handoff_glossary(report_text),
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

        t = _translator(["译文", report_text], glossary_auto=True)
        t.get_translation("a" * 6000)

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
        t.get_translation("a" * 6000)
        t.get_translation("b" * 6000)
        assert t.learned.lookup("Boxer").translation == "鲍克瑟"

    def test_compact_prompt_states_the_per_report_cap(self):
        """The prompt says how many renderings one report may carry, and
        asks for new or changed ones only. It never carries a list of what
        is established — that list is what used to ride in the seed, and
        putting it back in the prompt would be the same unbounded growth by
        another door."""
        prompt = handoff_prompt(with_glossary=True)
        assert f"at most {GLOSSARY_MAX_PER_COMPACT}" in prompt
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
        t.get_translation("a" * 6000)
        t.get_translation("b" * 6000)

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
        t.get_translation("a" * 6000)

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
        assert t.session.estimated_tokens() <= SEED_CAP_TOKENS + 40

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
    """Owner emphasis 260913: "especially for some cheap models all kinds of
    stuff can happen". Every one of these used to count as a successful
    compaction — the window was reset and seeded with the reply, whatever it
    was, and the good snapshot on disk was overwritten with it."""

    @pytest.mark.parametrize("label", sorted(JUNK_REPLIES))
    def test_junk_compact_reply_degrades_safely(self, tmp_path, label, capsys):
        path = tmp_path / "book_handoff.md"
        HandoffReport(
            window=1,
            summary="The good one: Napoleon took the farm.",
            glossary_lines="Napoleon → 拿破仑\n",
        ).write_snapshot(path)
        good = path.read_text(encoding="utf-8")

        t = _translator(
            ["译文", JUNK_REPLIES[label]], glossary_auto=True, handoff_path=path
        )
        # no exception, and the paragraph is translated
        assert t.get_translation("a" * 6000) == "译文"

        # the previous snapshot is untouched
        assert path.read_text(encoding="utf-8") == good
        # nothing junk was learned
        for term in ("Here", "I cannot comply with this request", "Boxer", "!!!!!"):
            assert t.glossary.lookup(term) is None
        # the window was kept for a retry rather than reset onto nothing
        assert t.session.windows == 1
        assert t.session.estimated_tokens() > 0
        assert "handoff report failed" in capsys.readouterr().out

    def test_an_oversized_blob_is_a_report_and_is_cut(self, tmp_path, capsys):
        """The one reply in this family that *is* a report: a real summary,
        ten times too long. It compacts normally and the seed is truncated —
        max_tokens is assumed to have been ignored, because it was."""
        path = tmp_path / "book_handoff.md"
        blob = "\n".join(f"They walked to the farm, part {n}." for n in range(500))
        t = _translator(["译文", blob], handoff_path=path)
        t.get_translation("a" * 6000)

        assert t.session.windows == 2
        assert t.session.estimated_tokens() <= SEED_CAP_TOKENS + 40
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
