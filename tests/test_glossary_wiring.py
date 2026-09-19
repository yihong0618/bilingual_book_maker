"""The glossary flags end to end: parsing, injection, and what is derived.

Three things are pinned here, and each has a way of failing quietly.

1. `--glossary` and `--terminology` are one flag. A user who knows only one of
   the two words must get exactly the run the other word gives.
2. A pinned block is sent *only* with a request whose text contains the term.
   Sending it everywhere would be paid for on every request in session mode
   (the fresh tail message is never cached); sending it nowhere would drop the
   pin without a word.
3. The derived (session-learned) glossary is a runtime thing. It rides in
   requests and in `<book>_handoff.md`, it never touches the operator's own
   file, and `options.glossary_path` — what the translation metadata stamp records —
   stays exactly what was typed.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from book_maker.cli import build_parser, glossary_auto_flag, parse_args
from book_maker.glossary import Glossary
from book_maker.session_context import (
    HandoffReport,
    handoff_prompt,
    parse_handoff_glossary,
    trim_handoff_prose,
)
from book_maker.translator import chatgptapi_translator, codex_translator
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.codex_translator import Codex

REPO = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "glossary.txt"

# The section only a glossary-learning compact turn asks for. The header is
# a protocol token asked for in English whatever the target language is.
RENDERINGS_MARKER = "## Renderings"

# A unit naming the terms the fixture reports back, so a harvested pair is
# grounded in the window (owner ruling 260913: a pair with neither end in
# the text was invented, not observed, and is dropped).
UNIT = "Boxer and Clover walked to the farm. " + "a" * 200


@pytest.fixture
def glossary_path(tmp_path):
    path = tmp_path / "pins.txt"
    path.write_text("Winston → 温斯顿\nJulia → 茱莉亚\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ the flags


class TestTheTwoSpellings:
    def test_terminology_parses_to_the_same_run_as_glossary(self, glossary_path):
        a = vars(
            parse_args(["--book_name", "b.epub", "--glossary", str(glossary_path)])
        )
        b = vars(
            parse_args(["--book_name", "b.epub", "--terminology", str(glossary_path)])
        )
        # everything but the record of which word was typed
        assert {k: v for k, v in a.items() if k != "glossary_flag"} == {
            k: v for k, v in b.items() if k != "glossary_flag"
        }
        assert a["glossary_path"] == b["glossary_path"] == str(glossary_path)

    def test_the_spelling_that_was_typed_is_remembered(self, glossary_path):
        # so a warning about the flag names the word the operator used
        typed = parse_args(
            ["--book_name", "b.epub", "--terminology", str(glossary_path)]
        )
        assert typed.glossary_flag == "--terminology"

    def test_neither_spelling_leaves_a_usable_default(self):
        options = parse_args(["--book_name", "b.epub"])
        assert options.glossary_path is None
        assert options.glossary_flag == "--glossary"

    def test_a_missing_file_is_refused_at_parse_time(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as stopped:
            parse_args(
                ["--book_name", "b.epub", "--glossary", str(tmp_path / "no.txt")]
            )
        assert stopped.value.code == 2
        assert "no glossary file at" in capsys.readouterr().err

    def test_the_alias_is_refused_just_as_loudly(self, tmp_path, capsys):
        with pytest.raises(SystemExit):
            parse_args(
                ["--book_name", "b.epub", "--terminology", str(tmp_path / "no.txt")]
            )
        assert "no glossary file at" in capsys.readouterr().err

    def test_both_spellings_are_in_the_help(self):
        proc = subprocess.run(
            [sys.executable, "make_book.py", "--help"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0
        flat = " ".join(proc.stdout.split())
        assert "--glossary" in flat
        assert "--terminology" in flat
        assert "--glossary-auto" in flat

    def test_a_malformed_file_stops_the_run_before_it_spends(self, tmp_path):
        bad = tmp_path / "bad.txt"
        bad.write_text("Winston 温斯顿\n", encoding="utf-8")
        book = tmp_path / "animal_farm.epub"
        book.write_bytes((REPO / "test_books" / "animal_farm.epub").read_bytes())
        proc = subprocess.run(
            [
                sys.executable,
                "make_book.py",
                "--book_name",
                str(book),
                "--api_format",
                "google",
                "--glossary",
                str(bad),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        assert "Could not read --glossary" in proc.stdout + proc.stderr

    def test_the_auto_switch_is_off_unless_asked(self):
        """Owner ruling 260913. It was a tri-state whose None meant "on
        wherever a session runs"; saying nothing now means off."""
        assert glossary_auto_flag(None) is False
        assert glossary_auto_flag("on") is True
        assert glossary_auto_flag("off") is False

    def test_the_auto_switch_refuses_anything_else(self, capsys):
        with pytest.raises(SystemExit):
            parse_args(["--book_name", "b.epub", "--glossary-auto", "yes"])
        assert "--glossary-auto" in capsys.readouterr().err

    def test_neither_flag_is_hidden(self):
        # made public deliberately: a pin is a substitution instruction, and an
        # unadvertised one is the version nobody can audit
        actions = {
            option: action.help
            for action in build_parser()._actions
            for option in action.option_strings
        }
        for flag in ("--glossary", "--terminology", "--glossary-auto"):
            assert actions[flag], f"{flag} has no help text"


# --------------------------------------------------------- the injected block


def _translator(replies=None, **kwargs):
    """A ChatGPTAPI wired to a scripted client. No network, real __init__."""
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
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))
            ],
            usage=None,
        )

    t.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=Mock(side_effect=create), parse=Mock(side_effect=create)
            )
        )
    )
    t.sent = sent
    return t


def _tails(translator):
    return [call["messages"][-1]["content"] for call in translator.sent]


def _unit_request(translator, text):
    """The fresh tail message of the request that carried `text`."""
    for content in reversed(_tails(translator)):
        if text in content:
            return content
    raise AssertionError(f"no request carried {text!r}")


class TestTheLegacyPathSendsNothingUnasked:
    """No flags: not a word about a glossary reaches any request."""

    def test_a_plain_run_carries_no_glossary_text(self):
        t = _translator()
        for text in ("Winston went home", "Julia waited", "nothing relevant"):
            t.get_translation(text)
        for content in _tails(t):
            assert "<glossary>" not in content
            assert "verbatim" not in content

    def test_a_plain_run_learns_nothing_to_send(self):
        t = _translator()
        assert t.glossary_auto_on is False
        assert not t.glossary


class TestTheBlockRidesWithTheUnitThatNeedsIt:
    def test_only_a_request_whose_text_has_the_term_carries_the_block(
        self, glossary_path
    ):
        t = _translator(glossary=Glossary.from_file(glossary_path))
        t.get_translation("Winston went home")
        t.get_translation("the weather was cold")
        hit, miss = _tails(t)
        assert "<glossary>" in hit and "温斯顿" in hit
        assert "<glossary>" not in miss

    def test_only_the_terms_that_hit_are_named(self, glossary_path):
        t = _translator(glossary=Glossary.from_file(glossary_path))
        t.get_translation("Winston went home")
        assert "茱莉亚" not in _tails(t)[0]

    def test_the_block_rides_in_the_fresh_tail_not_the_system_message(
        self, glossary_path
    ):
        # a per-unit block in a fixed position would invalidate the cached
        # prefix on every request in session mode
        t = _translator(
            glossary=Glossary.from_file(glossary_path),
            context_flag=True,
            context_mode="session",
        )
        t.get_translation("Winston went home")
        messages = t.sent[0]["messages"]
        assert "<glossary>" in messages[-1]["content"]
        assert "<glossary>" not in messages[0]["content"]

    def test_a_grouped_request_carries_the_terms_its_group_uses(self, glossary_path):
        # plan mode sends whole batches; the "unit" is then the group, and a
        # batch that lost the block would translate a pinned name freely
        t = _translator(glossary=Glossary.from_file(glossary_path))
        messages = t._create_structured_batch_messages(
            ["Winston went home", "the weather was cold"]
        )
        content = messages[-1]["content"]
        assert "<glossary>" in content and "温斯顿" in content
        assert "茱莉亚" not in content
        # the shape and the target language stay the last thing read
        assert content.index("<glossary>") < content.index("JSON object")

    def test_a_grouped_request_with_no_hits_carries_no_block(self, glossary_path):
        t = _translator(glossary=Glossary.from_file(glossary_path))
        messages = t._create_structured_batch_messages(["one", "two"])
        assert "<glossary>" not in messages[-1]["content"]

    def test_the_history_stores_exactly_what_was_sent(self, glossary_path):
        # the next request replays this message verbatim; a stored copy
        # without the block would be a full-price cache miss every request
        t = _translator(
            ["一", "二"],
            glossary=Glossary.from_file(glossary_path),
            context_flag=True,
            context_mode="session",
        )
        t.get_translation("Winston went home")
        t.get_translation("Julia waited")
        assert t.sent[1]["messages"][: len(t.sent[0]["messages"])] == (
            t.sent[0]["messages"]
        )


# ------------------------------------------------------- the derived glossary

HANDOFF_WITH_TERMS = (
    "They walked to the farm.\n\n"
    "<renderings>\nBoxer → 拳击手\nClover → 三叶草\n</renderings>\n"
)


def _session(replies, **kwargs):
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    kwargs.setdefault("context_compact_at", 10)
    # Asked for, because since 260913 it has to be: these tests are about what
    # learning does, not about whether a run opts into it. The default itself
    # is pinned by `TestLearningIsAskedFor`.
    kwargs.setdefault("glossary_auto", True)
    return _translator(replies, **kwargs)


class TestLearningIsAskedFor:
    """Owner ruling 260913: `--glossary-auto` is off until it is asked for.

    It used to default on wherever a session ran, on the reasoning that the
    compact turn exists anyway. The section is not free — it costs output on
    every compaction, and harvesting names out of a model's prose needs a
    model that answers with names — so a run now opts in.
    """

    def test_a_session_run_does_not_learn_unless_asked(self):
        t = _translator(context_flag=True, context_mode="session")
        assert t.glossary_auto_on is False

    def test_and_its_compact_turn_asks_for_no_renderings(self):
        """The other half of the default: no learning means the section is
        never sent, so the run is not billed for output nobody reads."""
        t = _translator(context_flag=True, context_mode="session")
        prompt = handoff_prompt(with_glossary=t.glossary_auto_on)
        assert RENDERINGS_MARKER not in prompt

    def test_on_turns_it_on(self):
        t = _translator(context_flag=True, context_mode="session", glossary_auto=True)
        assert t.glossary_auto_on is True

    def test_a_windowed_run_does_not(self):
        assert _translator(
            context_flag=True, context_mode="window"
        ).glossary_auto_on is (False)

    def test_the_codex_thread_waits_to_be_asked_too(self):
        # its thread is the history whether or not --use_context was typed,
        # which used to be reason enough; since 260913 it is not
        assert (
            Codex(key="", language="Chinese", server=SimpleNamespace()).glossary_auto_on
            is False
        )

    def test_on_turns_the_codex_thread_on(self):
        codex = Codex(
            key="", language="Chinese", server=SimpleNamespace(), glossary_auto=True
        )
        assert codex.glossary_auto_on is True

    def test_off_turns_the_codex_thread_off_too(self):
        codex = Codex(
            key="", language="Chinese", server=SimpleNamespace(), glossary_auto=False
        )
        assert codex.glossary_auto_on is False

    def test_on_cannot_conjure_a_session(self):
        # the derived glossary is a by-product of compaction; a windowed run
        # has no compact turn to learn from, and the CLI warns rather than
        # pretending otherwise
        t = _translator(context_flag=True, context_mode="window", glossary_auto=True)
        assert t.glossary_auto_on is False


class TestLearningFromTheHandoff:
    def test_the_compact_turn_asks_for_renderings(self, tmp_path):
        t = _session(["译文", HANDOFF_WITH_TERMS], handoff_path=tmp_path / "h.md")
        t.get_translation(UNIT)
        asked = [c for c in _tails(t) if handoff_prompt()[:40] in c]
        assert asked and RENDERINGS_MARKER in asked[-1]

    def test_the_learned_terms_reach_the_next_window(self, tmp_path):
        t = _session(
            ["译文", HANDOFF_WITH_TERMS, "译文"], handoff_path=tmp_path / "h.md"
        )
        t.get_translation(UNIT)
        t.get_translation("Boxer pulled the cart")
        # the translation request, not the compact turn that follows it: with
        # this budget every unit rolls the window over
        assert "拳击手" in _unit_request(t, "Boxer pulled the cart")

    def test_the_report_records_them_for_the_operator(self, tmp_path):
        path = tmp_path / "h.md"
        t = _session(["译文", HANDOFF_WITH_TERMS], handoff_path=path)
        t.get_translation(UNIT)
        # the snapshot's own heading, written by us for whoever opens the
        # file — not the protocol header the prompt asks the model for
        assert "## Established renderings" in path.read_text(encoding="utf-8")

    def test_the_block_is_not_left_in_the_prose_as_well(self, tmp_path):
        path = tmp_path / "h.md"
        t = _session(["译文", HANDOFF_WITH_TERMS], handoff_path=path)
        t.get_translation(UNIT)
        assert "<renderings>" not in path.read_text(encoding="utf-8")

    def test_an_empty_block_does_not_erase_what_was_learned(self, tmp_path):
        """codex review 260905 (P2): an empty block means "no additions",
        not "forget everything" — a later report that learns nothing new
        must not cost the run the vocabulary earlier ones established.

        The claim is unchanged; where it lands is. Before the 260913
        redesign (owner-authorized, docs/260913-feat-SESSION_HANDOFF_
        SNAPSHOT_SEED_BOUNDS.md, rulings 4 and 6) the evidence was the seed:
        the whole merged glossary rode into every window, so "still there"
        meant "still in the seed", and the handoff file was an append log
        where it could be counted twice. Now the seed carries summary and
        style only and the file is one overwritten snapshot, so the same
        claim is made against memory and against that snapshot.

        This is deliberately not #569's silent flip of the same behaviour:
        that patch dropped the vocabulary from the seed as a side effect of
        an accounting change, with no ruling and no test moved to follow it.
        """
        path = tmp_path / "h.md"
        empty = "Nothing new this window.\n\n<renderings>\n</renderings>\n"
        t = _session(["译文", HANDOFF_WITH_TERMS, "译文", empty], handoff_path=path)
        t.get_translation(UNIT)
        t.get_translation("b" * 200)
        # memory: the empty window added nothing and forgot nothing
        assert t.glossary.lookup("Boxer").translation == "拳击手"
        assert t.learned.lookup("Clover").translation == "三叶草"
        # the snapshot: the current set, written once, by the latest window
        text = path.read_text(encoding="utf-8")
        assert text.count("Boxer → 拳击手") == 1
        assert "Nothing new this window." in text

    def test_a_pin_is_never_overwritten_by_what_was_learned(self, tmp_path):
        pinned = Glossary.parse("Boxer → 鲍克瑟\n")
        t = _session(
            ["译文", HANDOFF_WITH_TERMS],
            glossary=pinned,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation(UNIT)
        assert t.glossary.lookup("Boxer").translation == "鲍克瑟"
        # and the operator's own set is left exactly as it was read
        assert t.pinned == pinned
        assert t.pinned.lookup("Clover") is None


HANDOFF_WITHOUT_TAGS = (
    "They walked to the farm.\n\n"
    "### Established renderings\n\n"
    "Boxer → 拳击手\n"
    "Clover → 三叶草\n\n"
    "### Next window\n\n"
    "The rebellion starts in chapter two.\n"
)

HANDOFF_WITHOUT_RENDERINGS = (
    "They walked to the farm.\n\n"
    "### Next window\n\n"
    "The rebellion starts in chapter two."
)


def _assembled(report_text, learned_lines):
    """The handoff a window writes: stripped prose plus the canonical block.

    The two halves `_compact_window` puts together, so a term that survives
    the strip and is re-rendered too is visible as a count of two.
    """
    return HandoffReport(
        window=1,
        summary=trim_handoff_prose(report_text),
        glossary_lines=learned_lines,
    )


class TestTheStripperRemovesExactlyWhatWasParsed:
    """A term the parse recovered is re-rendered canonically, so it has to
    leave the prose — once, and only if it was really recovered."""

    def test_a_tagged_block_leaves_the_prose_once(self):
        parsed = parse_handoff_glossary(HANDOFF_WITH_TERMS)
        assert parsed.source == "tagged"
        report = _assembled(HANDOFF_WITH_TERMS, parsed.glossary.to_lines())
        assert report.render().count("Boxer → 拳击手") == 1
        assert report.render().count("Clover → 三叶草") == 1
        # and not in the seed at all: since 260913 the seed is summary and
        # style only (`TestTheSeedCarriesSummaryAndStyleOnly`)
        assert "Boxer → 拳击手" not in report.seed_text()
        assert "<renderings>" not in report.render()
        assert "They walked to the farm." in report.summary

    def test_every_heading_goes_whatever_it_says(self):
        """Owner ruling 260913: the trim is by shape, not by keyword.

        Models write their section headers in the target language, so a list
        of English words to recognise is unwinnable — `## 术语表` above a
        block of arrow lines is the ordinary case, not the exotic one. A
        heading is furniture for a document, and a seed is not one: its
        preamble already says what the text under it is.
        """
        text = (
            "## Summary\n\nThey walked to the farm.\n\n"
            "## 术语表\n\n"
            "<renderings>\nBoxer → 拳击手\n</renderings>\n"
        )
        summary = trim_handoff_prose(text)
        assert summary == "They walked to the farm."

    def test_loose_lines_leave_the_prose_once(self):
        # the fallback recovers these, and nothing used to remove them: the
        # terms landed twice in the file and twice in the next window's seed
        parsed = parse_handoff_glossary(HANDOFF_WITHOUT_TAGS)
        assert parsed.source == "scanned"
        report = _assembled(HANDOFF_WITHOUT_TAGS, parsed.glossary.to_lines())
        assert report.render().count("Boxer → 拳击手") == 1
        assert report.render().count("Clover → 三叶草") == 1
        assert "Boxer → 拳击手" not in report.seed_text()
        assert "Clover → 三叶草" not in report.seed_text()
        # the report's own prose stays; only its headings go with the terms
        assert "The rebellion starts in chapter two." in report.summary

    @pytest.mark.parametrize(
        "report, expected",
        (
            (
                HANDOFF_WITHOUT_RENDERINGS,
                "They walked to the farm.\n\nThe rebellion starts in chapter two.",
            ),
            (
                "They walked to the farm.\n\n### Open questions",
                "They walked to the farm.",
            ),
            (
                "## Terms\n\nNothing new to keep unified this window.",
                "Nothing new to keep unified this window.",
            ),
        ),
    )
    def test_a_report_that_established_nothing_keeps_all_its_prose(
        self, report, expected
    ):
        """Nothing is parsed out of it, so nothing but furniture is removed."""
        assert parse_handoff_glossary(report).source == "missing"
        assert trim_handoff_prose(report) == expected

    def test_an_empty_block_does_not_take_the_loose_lines_with_it(self):
        # an empty block sends the parse to the loose lines, so those are
        # what was recovered and those are what has to go
        text = (
            "They walked to the farm.\n\n"
            "<renderings>\n</renderings>\n\n"
            "Boxer → 拳击手\n\n"
            "### Next window\n\nOn to chapter two."
        )
        parsed = parse_handoff_glossary(text)
        assert parsed.source == "scanned"
        report = _assembled(text, parsed.glossary.to_lines())
        assert report.render().count("Boxer → 拳击手") == 1
        assert "On to chapter two." in report.summary

    def test_the_whole_file_carries_each_term_once(self, tmp_path):
        path = tmp_path / "h.md"
        t = _session(["译文", HANDOFF_WITHOUT_TAGS], handoff_path=path)
        t.get_translation(UNIT)
        text = path.read_text(encoding="utf-8")
        assert text.count("Boxer → 拳击手") == 1
        assert text.count("Clover → 三叶草") == 1

    def test_the_next_window_carries_each_term_once(self, tmp_path):
        """One copy, and it comes from the unit's own block.

        It used to be the seed's copy that this counted, and the risk was a
        second one left in the prose. Since 260913 the seed carries no terms
        at all, so the question is the same and the answer moved: a unit that
        names a term gets it once, from `prompt_block`.
        """
        t = _session(
            ["译文", HANDOFF_WITHOUT_TAGS, "译文"], handoff_path=tmp_path / "h.md"
        )
        t.get_translation(UNIT)
        t.get_translation("Boxer pulled the cart")
        request = "\n".join(
            m["content"] for m in t.sent[-1]["messages"] if m.get("content")
        )
        assert request.count("Boxer → 拳击手") == 1

    def test_a_unit_naming_nothing_carries_no_terms_at_all(self, tmp_path):
        t = _session(
            ["译文", HANDOFF_WITHOUT_TAGS, "译文"], handoff_path=tmp_path / "h.md"
        )
        t.get_translation(UNIT)
        t.get_translation("the windmill stood")
        request = "\n".join(
            m["content"] for m in t.sent[-1]["messages"] if m.get("content")
        )
        assert "Boxer → 拳击手" not in request

    def test_the_codex_route_learns_from_a_renderings_only_reply(self, tmp_path):
        """codex review 260913 (P2): learning is not gated on the summary.

        A reply that is nothing but a renderings block still observed those
        renderings — they were parsed, they are grounded in the window, and
        dropping them because the prose was missing loses the one part of the
        reply that worked. The API routes already learned unconditionally
        after the empty-reply check; this route gated everything behind
        `has_summary()`. Writing the snapshot stays gated: an empty summary
        must not overwrite a good file with nothing.
        """
        path = tmp_path / "h.md"
        path.write_text("earlier snapshot\n", encoding="utf-8")
        codex = Codex(
            key="",
            language="Chinese",
            server=SimpleNamespace(),
            glossary_auto=True,
            handoff_path=path,
            context_compact_at=1500,
        )
        codex._thread_id = "t1"
        codex._window_sources = ["Boxer pulled the cart."]
        codex._window_translations = ["拳击手拉着车。"]
        codex._run_turn = Mock(
            return_value="<renderings>\nBoxer → 拳击手\n</renderings>\n"
        )
        seeds = []
        codex._ensure_thread = lambda seed=None: seeds.append(seed)

        codex._compact_window()

        assert codex.learned.lookup("Boxer").translation == "拳击手"
        # nothing to seed with and nothing to record: the thread opens clean
        # and the file on disk is left exactly as it was
        assert seeds == [""]
        assert path.read_text(encoding="utf-8") == "earlier snapshot\n"

    def test_every_route_assembles_the_report_the_same_way(self):
        # each route's compact path builds its HandoffReport through the one
        # shared helper, so the split and the trim cannot drift per route
        from book_maker.translator.base_translator import Base

        assert codex_translator.Codex._handoff_report is Base._handoff_report
        assert chatgptapi_translator.ChatGPTAPI._handoff_report is Base._handoff_report


class TestOffSuppressesTheDerivedGlossary:
    def test_the_compact_turn_does_not_ask_for_renderings(self, tmp_path):
        t = _session(
            ["译文", "They walked to the farm."],
            glossary_auto=False,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation(UNIT)
        asked = [c for c in _tails(t) if handoff_prompt()[:40] in c]
        assert asked and RENDERINGS_MARKER not in asked[-1]

    def test_a_report_that_volunteers_terms_anyway_is_not_learned(self, tmp_path):
        t = _session(
            ["译文", HANDOFF_WITH_TERMS, "译文"],
            glossary_auto=False,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation(UNIT)
        assert len(t.learned) == 0
        assert not t.glossary
        t.get_translation("Boxer pulled the cart")
        assert "拳击手" not in _unit_request(t, "Boxer pulled the cart")

    def test_nothing_derived_reaches_the_handoff_file(self, tmp_path):
        path = tmp_path / "h.md"
        t = _session(
            ["译文", HANDOFF_WITH_TERMS],
            glossary_auto=False,
            handoff_path=path,
        )
        t.get_translation(UNIT)
        assert RENDERINGS_MARKER not in path.read_text(encoding="utf-8")

    def test_the_pinned_block_still_rides_with_the_unit(self, glossary_path, tmp_path):
        # "off" declines the *learned* half; the operator's own pins are the
        # other half and are not affected
        t = _session(
            ["译文"],
            glossary=Glossary.from_file(glossary_path),
            glossary_auto=False,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("Winston went home")
        assert "温斯顿" in _tails(t)[0]


# ------------------------------------------------------------ the loader seam


class _CapturingModel:
    """A translator stand-in that records the kwargs the loader built it with."""

    kwargs = None

    def __init__(self, key, language, **kwargs):
        type(self).kwargs = kwargs
        self.language = language

    def rotate_key(self):
        pass

    def set_model_list(self, model_list):
        pass


def _epub(tmp_path):
    book = REPO / "test_books" / "animal_farm.epub"
    target = tmp_path / book.name
    target.write_bytes(book.read_bytes())
    return target


class TestTheLoaderForwardsIt:
    def test_the_epub_loader_hands_both_halves_to_the_translator(self, tmp_path):
        from book_maker.loader.epub_loader import EPUBBookLoader

        pinned = Glossary.parse("Winston → 温斯顿\n")
        EPUBBookLoader(
            str(_epub(tmp_path)),
            _CapturingModel,
            "k",
            False,
            language="Chinese",
            glossary=pinned,
            glossary_auto=False,
        )
        assert _CapturingModel.kwargs["glossary"] == pinned
        assert _CapturingModel.kwargs["glossary_auto"] is False

    def test_the_markdown_loader_does_too(self, tmp_path):
        from book_maker.loader.md_loader import MarkdownBookLoader

        source = tmp_path / "book.md"
        source.write_text("# One\n\nWinston went home.\n", encoding="utf-8")
        pinned = Glossary.parse("Winston → 温斯顿\n")
        MarkdownBookLoader(
            str(source),
            _CapturingModel,
            "k",
            False,
            language="Chinese",
            glossary=pinned,
            glossary_auto=True,
        )
        assert _CapturingModel.kwargs["glossary"] == pinned
        assert _CapturingModel.kwargs["glossary_auto"] is True

    def test_the_pdf_loader_hands_context_and_both_halves_to_the_translator(
        self, tmp_path
    ):
        import fitz

        from book_maker.loader.pdf_loader import PDFBookLoader

        source = tmp_path / "book.pdf"
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), "Winston went home.")
            document.save(source)
        pinned = Glossary.parse("Winston → 温斯顿\n")
        PDFBookLoader(
            str(source),
            _CapturingModel,
            "k",
            False,
            language="Chinese",
            context_flag=True,
            context_paragraph_limit=7,
            context_mode="session",
            context_compact_at=1500,
            no_context_compact=True,
            glossary=pinned,
            glossary_auto=True,
        )

        assert {
            key: _CapturingModel.kwargs[key]
            for key in (
                "context_flag",
                "context_paragraph_limit",
                "context_mode",
                "context_compact_at",
                "no_context_compact",
                "glossary",
                "glossary_auto",
                "handoff_path",
            )
        } == {
            "context_flag": True,
            "context_paragraph_limit": 7,
            "context_mode": "session",
            "context_compact_at": 1500,
            "no_context_compact": True,
            "glossary": pinned,
            "glossary_auto": True,
            "handoff_path": tmp_path / "book_handoff.md",
        }


def test_the_fixture_file_is_a_readable_glossary():
    # the compatibility fixtures point --glossary at it; a file the parser
    # accepts but the loader cannot read would hide a real failure
    assert len(Glossary.from_file(FIXTURE)) == 2
