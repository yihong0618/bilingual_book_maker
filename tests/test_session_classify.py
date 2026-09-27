"""Plan classification over a plain conversation, for routes with no JSON.

The contract: an endpoint whose structured-output verdict is below
`json_object` but which can hold a conversation now *plans* instead of
dropping to tag mode. It is asked for one comma-separated verdict per
signature, in one append-only session, and every reply is checked verbatim
— no fuzzy matching, no JSON anywhere.

How many signatures a turn carries is a ladder, 5 → 3 → 1 → none, walked
down by the endpoint's own replies: two *consecutive* mis-shaped turns at a
rung degrade exactly one step. That replaced the two rate breakers the first
version shipped with, so nothing here counts failure rates any more.

The schema-capable path is not this file's subject and must not move:
`test_structured_classify.py` and `test_translation_plan.py` pin it, and
the tests at the bottom here check that a route with a verdict never
reaches this entry at all.
"""

import pytest

from book_maker.loader.classify import (
    can_session_classify,
    classify_plan,
)
from book_maker.loader.classify.model import PlanClassifyFatal
from book_maker.loader.classify.session import (
    ENGAGE_WARNING,
    EXAMPLE_REPLY,
    FAILS_BEFORE_DEGRADING,
    NAMED_BY_SESSION,
    NAMED_UNANSWERED,
    NAMED_UNSURE,
    PROGRESS_DESC,
    RUNGS,
    TRUNK,
    build_example_turn,
    build_trunk,
    classify_over_session,
    degrade_line,
    parse_verdicts,
    render_turn,
    stop_line,
    trunk_with_inline_example,
)
from book_maker.loader.ledger import Ledger
from book_maker.session_context import DEFAULT_COMPACT_BUDGET
from book_maker.translator.base_translator import Base

# ----------------------------------------------------------------- fixtures


def key_of(signature):
    return signature if signature.startswith("block:") else f"block:{signature}"


def _ledger_with(signatures):
    """A ledger whose open questions are exactly `signatures`, in that order.

    Rows settle largest-first, so each signature is given a distinct size:
    the candidate order is then the order written here and a scripted reply
    can be read against it.
    """
    ledger = Ledger()
    for i, signature in enumerate(signatures):
        for _ in range(3):
            ledger.add_occurrence("block", signature, 100 - i, f"sample of {signature}")
    return ledger.finalize(sum(3 * (100 - i) for i in range(len(signatures))))


class FakeSession:
    """A classifier conversation, scripted and instrumented.

    `reply` takes the turn's text and returns what the endpoint said. The
    session records every trunk it was started with and every turn asked of
    it, which is what the append-only and restart tests read.
    """

    def __init__(self, reply, budget=8000):
        self.reply = reply
        self._budget = budget
        self.starts = []
        self.asks = []
        # (session index, turn text) — which conversation each turn landed in
        self.turns = []

    def budget(self):
        return self._budget

    def start(self, trunk):
        self.starts.append(trunk)

    def ask(self, text):
        assert self.starts, "a turn was asked before the trunk was sent"
        self.asks.append(text)
        self.turns.append((len(self.starts), text))
        return self.reply(text)


class SessionOnly(Base):
    """A translator with a conversation and no structured output at all —
    the codex route's shape, and any endpoint the probe finds bare."""

    def __init__(self, session):
        super().__init__("key", "zh-hans")
        self.model = "fake-session-model"
        self.session_obj = session
        self.sessions_opened = 0

    def rotate_key(self):
        pass

    def translate(self, text):
        raise AssertionError("classification must never translate")

    def classify_session(self, model=None):
        self.sessions_opened += 1
        return self.session_obj


def scripted(verdicts, unknown="translate"):
    """A reply function answering with `verdicts[key]` per signature asked."""

    def reply(text):
        asked = [key for key in verdicts if f'"{key}"' in text]
        return ",".join(verdicts.get(key, unknown) for key in asked)

    return reply


def _run(signatures, reply, budget=8000, quiet=True):
    session = FakeSession(reply, budget=budget)
    translator = SessionOnly(session)
    # the progress bar is a separate subject (TestTheProgressLine); silenced
    # here so every other test reads a stderr it did not have to filter
    translator.quiet = quiet
    decisions, candidates = classify_over_session(_ledger_with(signatures), translator)
    return decisions, candidates, session


SIX = ["p.a", "p.b", "p.c", "p.d", "p.e", "p.f"]


def _many(n):
    return [f"p.s{i:02d}" for i in range(n)]


def _sizes(session):
    """How many signatures each turn carried, in order."""
    return [text.count("occurrence(s)") for text in session.asks]


class Endpoint:
    """Answers every turn in the asked shape, except the group turns named.

    `fail_on` holds *group-turn* ordinals, 1-based — the turns the ladder
    itself asks. The singles a failed 5- or 3-group is recovered with are
    not group turns and always answer, so a test can name "the second turn
    failed" without counting the recovery behind the first.
    """

    def __init__(self, fail_on=(), bad="no idea"):
        self.fail_on = set(fail_on)
        self.bad = bad
        self.groups = 0
        self.group_sizes = []
        self._owed_singles = 0

    def __call__(self, text):
        asked = text.count("occurrence(s)")
        if self._owed_singles:
            self._owed_singles -= 1
            return "skip"
        self.groups += 1
        self.group_sizes.append(asked)
        if self.groups in self.fail_on:
            if asked > 1:
                self._owed_singles = asked
            return self.bad
        return ",".join(["skip"] * asked)


# ------------------------------------------------- 1. the route now plans


class TestEngaging:
    def test_a_bare_route_that_can_talk_classifies_instead_of_giving_up(self, capsys):
        decisions, candidates, session = _run(
            SIX, scripted({key_of(s): "translate" for s in SIX})
        )
        assert len(decisions) == len(candidates) == 6
        assert session.starts, "the trunk was never sent"
        assert ENGAGE_WARNING in capsys.readouterr().out

    def test_the_engage_line_says_replies_are_unconstrained(self):
        # the operator-facing promise: nothing on the wire enforces the
        # vocabulary, so the parser is the whole guarantee
        assert ENGAGE_WARNING == (
            "plan: classifying over a plain session (this endpoint has no "
            "structured output); replies are checked verbatim"
        )

    def test_a_route_with_no_conversation_is_not_engaged(self):
        class PromptOnly(Base):
            def rotate_key(self):
                pass

            def translate(self, text):
                pass

            def _chat_completion(self, prompt, model=None):
                return "{}"

        assert not can_session_classify(PromptOnly("k", "zh-hans"))

    def test_the_ladder_opens_at_five_units_per_turn(self):
        _decisions, _candidates, session = _run(
            _many(10), scripted({key_of(s): "translate" for s in _many(10)})
        )
        assert RUNGS == (5, 3, 1, None)
        assert _sizes(session) == [5, 5]

    def test_a_final_partial_turn_asks_for_that_many_verdicts(self):
        signatures = _many(7)
        _decisions, _candidates, session = _run(
            signatures, scripted({key_of(s): "skip" for s in signatures})
        )
        assert _sizes(session) == [5, 2]


class TestTheLedgerTakesWhatComesBack:
    def test_every_row_is_decided_and_every_decision_is_legal(self):
        # the row state machine demands a content_type with every verdict.
        # A three-token reply has no room to name the content, so what is
        # recorded is how the verdict was reached — which is the part a plan
        # can be audited on.
        state = {"turns": 0}

        def reply(text):
            state["turns"] += 1
            if state["turns"] == 1:
                return "skip,unsure,translate,skip,translate"
            # the tail turn carries one signature, so there is no smaller
            # shape to re-ask it in: it is translated by policy
            return "not a verdict"

        ledger = _ledger_with(SIX)
        translator = SessionOnly(FakeSession(reply))
        translator.quiet = True
        decisions, _candidates = classify_over_session(ledger, translator)
        for key, (verdict, content_type) in decisions.items():
            ledger.decide(key, verdict, "llm", content_type)
        assert not ledger.undecided_keys()
        assert sorted(row["content_type"] for row in ledger.rows.values()) == sorted(
            [NAMED_BY_SESSION] * 4 + [NAMED_UNSURE, NAMED_UNANSWERED]
        )


# ------------------------------------------------------------- 2. the parser


class TestParser:
    @pytest.mark.parametrize(
        "reply,expected",
        [
            ("skip,translate,unsure", ["skip", "translate", "unsure"]),
            ("SKIP,Translate,UNSURE", ["skip", "translate", "unsure"]),
            ("  skip , translate , unsure  ", ["skip", "translate", "unsure"]),
            ("skip,translate,unsure.", ["skip", "translate", "unsure"]),
            ("skip,\ntranslate,\nunsure", ["skip", "translate", "unsure"]),
        ],
    )
    def test_what_a_triple_may_look_like(self, reply, expected):
        assert parse_verdicts(reply, 3) == expected

    @pytest.mark.parametrize(
        "reply",
        [
            "skip,translate",  # short
            "skip;translate;unsure",  # not commas
            "Here you go: skip,translate,unsure",  # a preamble is not a token
            "skip,translate,maybe",  # not the vocabulary
            "skip,translate,unsure — the third is apparatus",  # trailing prose
            "",
            None,
        ],
    )
    def test_what_it_refuses(self, reply):
        assert parse_verdicts(reply, 3) is None

    @pytest.mark.parametrize(
        "reply,count",
        [
            ("translate,skip,translate,unsure", 3),
            ("skip, translate", 1),
            ("skip,skip", 1),
        ],
    )
    def test_surplus_verdicts_are_refused_not_truncated(self, reply, count):
        """A reply longer than the question is a model that lost track.

        Keeping its first `count` tokens assumes the ordering the verdicts
        depend on survived — and a wrong skip loses content. It falls to the
        singles path instead, which cannot be misaligned.
        """
        assert parse_verdicts(reply, count) is None

    def test_no_fuzzy_matching(self):
        # "do not translate" contains "translate"; a parser that went looking
        # for the word inside a sentence would answer the opposite of what
        # was said
        assert parse_verdicts("do not translate,skip,skip", 3) is None

    def test_a_single_is_parsed_the_same_way(self):
        assert parse_verdicts("Skip.", 1) == ["skip"]
        assert parse_verdicts("translate", 1) == ["translate"]
        assert parse_verdicts("nope", 1) is None


class TestEveryRungHasItsOwnReplyLength:
    """One shape for all three asking rungs: that many comma-joined verdicts.

    The 5-rung was ruled (260920 amendment) to reuse the 3-rung's form rather
    than the earlier `content_class,verdict` lines, so there is exactly one
    parser and exactly one thing the trunk has to teach.
    """

    @pytest.mark.parametrize(
        "size, reply, expected",
        (
            (
                5,
                "skip,translate,unsure,translate,skip",
                ["skip", "translate", "unsure", "translate", "skip"],
            ),
            (3, "skip,translate,unsure", ["skip", "translate", "unsure"]),
            (1, "translate", ["translate"]),
        ),
    )
    def test_a_group_of_that_size_parses_that_many_verdicts(
        self, size, reply, expected
    ):
        assert parse_verdicts(reply, size) == expected

    @pytest.mark.parametrize("size", (5, 3, 1))
    def test_the_wrong_number_of_verdicts_is_refused_at_every_rung(self, size):
        assert parse_verdicts(",".join(["skip"] * (size + 1)), size) is None
        if size > 1:
            assert parse_verdicts(",".join(["skip"] * (size - 1)), size) is None

    def test_the_rung_that_is_asked_is_the_rung_that_is_parsed(self):
        # the sizes the ladder asks at, driven end to end: 5 while the
        # endpoint holds the format, 3 after one pair of misses, 1 after two
        endpoint = Endpoint(fail_on={1, 2, 3, 4})
        _decisions, _candidates, _session = _run(_many(20), endpoint)
        assert endpoint.group_sizes == [5, 5, 3, 3, 1, 1, 1, 1]


class TestMalformedFallsBackToSingles:
    def _run_with_one_bad_group(self, bad_reply="I could not decide"):
        state = {"turns": 0}

        def reply(text):
            state["turns"] += 1
            if state["turns"] == 1:
                return bad_reply
            return "skip"

        return _run(SIX[:3], reply)

    def test_a_malformed_group_is_re_asked_one_unit_at_a_time(self):
        decisions, candidates, session = self._run_with_one_bad_group()
        # the group, then one turn per unit, all in the same session
        assert _sizes(session) == [3, 1, 1, 1]
        assert session.starts == [build_trunk()]
        for turn in session.asks[1:]:
            assert turn.startswith("1. ")
        assert {v for v, _ in decisions.values()} == {"skip"}
        assert all(name == NAMED_BY_SESSION for _, name in decisions.values())

    def test_a_single_that_still_fails_becomes_translate(self):
        def reply(text):
            return "no idea"

        decisions, _candidates, session = _run(SIX[:3], reply)
        assert _sizes(session) == [3, 1, 1, 1]
        assert {v for v, _ in decisions.values()} == {"translate"}
        assert all(name == NAMED_UNANSWERED for _, name in decisions.values())

    def test_a_failed_single_is_not_re_asked_at_all(self):
        # at the 1-rung there is no smaller shape, so the signature is
        # translated by policy rather than bought a second time
        endpoint = Endpoint(fail_on={1, 2, 3, 4, 5})
        decisions, _candidates, session = _run(_many(17), endpoint)
        # 5-group + 5 singles, 5-group + 5 singles, 3-group + 3 singles,
        # 3-group + 3 singles, then one lone single and nothing after it
        assert _sizes(session) == (
            [5] + [1] * 5 + [5] + [1] * 5 + [3] + [1] * 3 + [3] + [1] * 3 + [1]
        )
        assert len(decisions) == 17
        assert decisions[key_of("p.s16")] == ("translate", NAMED_UNANSWERED)


# ------------------------------------------------------------- 3. the policy


class TestUnsureIsTranslate:
    def test_unsure_never_skips(self):
        decisions, _candidates, _session = _run(
            SIX[:3], scripted({key_of(s): "unsure" for s in SIX[:3]})
        )
        assert {v for v, _ in decisions.values()} == {"translate"}

    def test_and_says_so_in_the_plan(self):
        # the row is auditable: a reader can tell a judged translate from a
        # defaulted one
        decisions, _candidates, _session = _run(
            SIX[:3],
            scripted(
                {
                    key_of("p.a"): "unsure",
                    key_of("p.b"): "translate",
                    key_of("p.c"): "skip",
                }
            ),
        )
        assert decisions[key_of("p.a")] == ("translate", NAMED_UNSURE)
        assert decisions[key_of("p.b")] == ("translate", NAMED_BY_SESSION)
        assert decisions[key_of("p.c")] == ("skip", NAMED_BY_SESSION)


# -------------------------------------------------------- 4. append-only


def _bare_openai(context_compact_at=None, reply="skip,skip,skip"):
    """A ChatGPTAPI with nothing built but what a classifier session reads."""
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "gpt-fake"
    translator.context_compact_at = context_compact_at
    translator.turns = []

    def _classify_turn(messages, model):
        translator.turns.append([dict(m) for m in messages])
        return reply

    translator._classify_turn = _classify_turn
    return translator


class RecordingTranslator:
    """Records the message list of every classifier turn."""

    def __init__(self, replies):
        self.model = "rec"
        self.replies = list(replies)
        self.requests = []
        self.context_compact_at = 8000

    def _session_budget(self):
        return self.context_compact_at

    def _classify_turn(self, messages, model):
        self.requests.append([dict(m) for m in messages])
        return self.replies.pop(0)


class TestAppendOnly:
    def test_the_second_request_is_the_first_plus_the_reply_plus_the_new_units(self):
        from book_maker.translator.chatgptapi_translator import ClassifierSession

        translator = RecordingTranslator(["skip,skip,skip", "translate,translate"])
        session = ClassifierSession(translator, model="rec")
        session.start("TRUNK")
        session.ask("units 1-3")
        session.ask("units 4-5")

        first, second = translator.requests
        assert first == [
            {"role": "system", "content": "TRUNK"},
            # the one demonstrated exchange the session opens on
            {"role": "user", "content": build_example_turn()},
            {"role": "assistant", "content": EXAMPLE_REPLY},
            {"role": "user", "content": "units 1-3"},
        ]
        # byte-identical prefix: this is the cache contract, so it is
        # compared as the whole list, not "starts with the same trunk"
        assert second == first + [
            {"role": "assistant", "content": "skip,skip,skip"},
            {"role": "user", "content": "units 4-5"},
        ]

    def test_the_trunk_is_sent_once_and_the_turns_carry_only_signatures(self):
        _decisions, _candidates, session = _run(
            SIX, scripted({key_of(s): "translate" for s in SIX})
        )
        assert session.starts == [build_trunk()]
        for turn in session.asks:
            assert "Reply with one verdict per signature" not in turn
            assert "skip,translate,unsure" not in turn

    def test_the_trunk_states_the_format_and_the_vocabulary(self):
        trunk = build_trunk()
        for word in ("skip", "translate", "unsure"):
            assert f'"{word}"' in trunk
        # the example the trunk prints and the example the session opens on
        # are the same five verdicts, so the two cannot teach different forms
        assert EXAMPLE_REPLY in trunk

    def test_the_trunk_names_no_per_turn_count_it_could_be_contradicted_on(self):
        """The rung changes mid-session; the history cannot be rewritten.

        So the trunk states the *form* ("one verdict per signature") and the
        count only inside its own example — which is the top rung's, with the
        sentence that covers a shorter message right after it.
        """
        trunk = build_trunk()
        assert "a few at a time" in trunk
        assert "three at a time" not in trunk
        assert (
            "When a message lists fewer than five signatures, reply with "
            "that many verdicts, in the same form." in trunk
        )

    def test_a_turn_carries_no_count_word_of_its_own(self):
        # `render_turn` is numbered signatures and nothing else: a count in
        # it would have to change with the rung inside an append-only history
        candidate = {
            "key": "block:p.a",
            "units": 3,
            "chars": 300,
            "pct": 92.9,
            "mean_chars": 100.0,
            "samples": ["a sample"],
        }
        text = render_turn([candidate, dict(candidate, key="block:p.b")]).lower()
        for word in ("five", "three", "verdict", "signature"):
            assert word not in text

    def test_the_classifier_session_is_not_the_translation_history(self):
        # `--use_context session` neither enables nor disables this: the
        # classifier conversation is planning machinery, held before the
        # first paragraph, and it must not leave a translation window behind
        translator = _bare_openai()
        assert translator.session is None

        session = translator.classify_session()
        session.start("TRUNK")
        session.ask("units 1-3")

        assert translator.session is None
        assert translator.turns[0][0] == {"role": "system", "content": "TRUNK"}

    def test_a_turn_shows_the_same_evidence_the_json_entry_shows(self):
        # one question, two channels: a verdict must not depend on which
        from book_maker.loader.classify.model import build_prompt

        candidate = {
            "key": "block:p.a",
            "units": 3,
            "chars": 300,
            "pct": 92.9,
            "mean_chars": 100.0,
            "samples": ["a sample"],
        }
        assert render_turn([candidate]) in build_prompt([candidate])


# ------------------------------------------- 4b. the demonstrated exchange


def _openai_session(replies, compact_at=8000):
    """A real `ClassifierSession` over a recording endpoint."""
    from book_maker.translator.chatgptapi_translator import ClassifierSession

    translator = RecordingTranslator(replies)
    translator.context_compact_at = compact_at
    return translator, ClassifierSession(translator, model="rec")


class TestTheDemonstratedTurn:
    """The conversation opens on one exchange that has already gone right.

    A weak endpoint answers the *first* turn in prose about as often as it
    answers it in the format, and the first turn is also the one the format
    breaker starts counting on. So the first real turn is never the first
    turn of the conversation: it rides behind a synthetic pair, sent once
    per session like the trunk it follows.
    """

    def test_the_first_real_turn_rides_behind_the_example(self):
        translator, session = _openai_session(["skip,skip,skip"])
        _decisions, candidates = classify_over_session(
            _ledger_with(SIX[:3]), SessionOnly(session), session=session
        )

        messages = translator.requests[0]
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
        assert messages[0] == {"role": "system", "content": build_trunk()}
        assert messages[1] == {"role": "user", "content": build_example_turn()}
        assert messages[2] == {"role": "assistant", "content": EXAMPLE_REPLY}
        assert messages[3] == {"role": "user", "content": render_turn(candidates)}

    def test_the_example_reply_is_never_a_verdict(self):
        # the assistant turn is text this side wrote; only what the endpoint
        # said is parsed, so a session that answers "skip,skip,skip" decides
        # three skips and nothing leaks out of the demonstrated five
        translator, session = _openai_session(["skip,skip,skip"])
        decisions, candidates = classify_over_session(
            _ledger_with(SIX[:3]), SessionOnly(session), session=session
        )
        assert len(decisions) == len(candidates) == 3
        assert {v for v, _ in decisions.values()} == {"skip"}
        assert all(name == NAMED_BY_SESSION for _, name in decisions.values())
        # one request: nothing was re-asked, so the triple parsed on its own
        assert len(translator.requests) == 1

    def test_a_restart_reseeds_the_example(self):
        # a budget no turn can stay under: every turn opens a fresh session,
        # and a fresh session with no demonstration is a fresh first turn
        translator, session = _openai_session(
            ["skip,skip,skip,skip,skip", "skip"], compact_at=1
        )
        classify_over_session(_ledger_with(SIX), SessionOnly(session), session=session)

        assert len(translator.requests) == 2
        for request in translator.requests:
            assert request[0]["content"] == build_trunk()
            assert request[1] == {"role": "user", "content": build_example_turn()}
            assert request[2] == {"role": "assistant", "content": EXAMPLE_REPLY}
        # the second conversation carries nothing from the first: trunk,
        # the pair, and its own turn
        assert len(translator.requests[1]) == 4

    def test_the_codex_trunk_carries_the_example_inline(self):
        # a thread's turns are the model's own, so the pair degrades to text
        # in the instructions rather than being dropped
        inline = trunk_with_inline_example()
        assert inline.startswith(build_trunk())
        assert build_example_turn() in inline
        assert EXAMPLE_REPLY in inline
        assert inline.endswith(f"You reply exactly:\n{EXAMPLE_REPLY}")

        # and the shared trunk is untouched: one source of truth, two shapes
        assert build_example_turn() not in TRUNK
        assert "Example. Given:" not in TRUNK

    def test_the_example_cost_covers_the_codex_inline_shape(self):
        # reverify round 2, 260906: the codex route sends the framing lines
        # ("Example. Given:", "You reply exactly:") as well as the pair, so
        # an estimate counting only the pair undercounts and delays the
        # restart a turn past the budget. The cost is counted at the inline
        # shape — the larger of the two routes' realities.
        from book_maker.loader.classify.session import (
            estimate_tokens,
            example_tokens,
        )

        trunk = build_trunk()
        inline_delta = estimate_tokens(
            trunk_with_inline_example(trunk)
        ) - estimate_tokens(trunk)
        assert example_tokens() >= inline_delta

    def test_the_example_is_static(self):
        # the prefix a caching endpoint pays for once cannot vary per session
        assert build_example_turn() == build_example_turn()
        assert trunk_with_inline_example() == trunk_with_inline_example()

    def test_the_example_is_a_turn_in_the_top_rungs_shape(self):
        # the demonstration is the shape of the turn that follows it: five,
        # the rung a session opens at
        text = build_example_turn()
        assert text.startswith("1. ")
        assert text.count("occurrence(s)") == RUNGS[0] == 5
        # rendered through render_turn, so it cannot drift from a real turn
        from book_maker.loader.classify.session import EXAMPLE_CANDIDATES

        assert text == render_turn(EXAMPLE_CANDIDATES)

    def test_the_example_demonstrates_all_three_tokens_and_parses(self):
        assert parse_verdicts(EXAMPLE_REPLY, RUNGS[0]) == [
            "skip",
            "translate",
            "unsure",
            "translate",
            "skip",
        ]
        assert set(parse_verdicts(EXAMPLE_REPLY, RUNGS[0])) == {
            "skip",
            "translate",
            "unsure",
        }

    def test_the_example_signatures_are_visibly_synthetic(self):
        # a human reading a transcript must see a demonstration, not wonder
        # which chapter of their book these came from
        from book_maker.loader.classify.session import EXAMPLE_CANDIDATES

        assert [c["key"] for c in EXAMPLE_CANDIDATES] == [
            "block:span.example-folio",
            "block:p.example-body",
            "inline:abbr.example-ref",
            "block:h2.example-chapter",
            "block:p.example-runhead",
        ]


# ------------------------------------------------- 5. restart at the budget


class TestRestartAtTheBudget:
    def test_crossing_the_budget_starts_a_fresh_session_with_the_trunk(self):
        # a budget smaller than one turn's estimate, so every turn crosses it
        _decisions, _candidates, session = _run(
            SIX, scripted({key_of(s): "translate" for s in SIX}), budget=1
        )
        assert len(session.asks) == 2
        assert session.starts == [build_trunk(), build_trunk()]
        # the second turn landed in the second conversation
        assert [index for index, _text in session.turns] == [1, 2]

    def test_a_budget_no_turn_reaches_keeps_one_session(self):
        _decisions, _candidates, session = _run(
            SIX, scripted({key_of(s): "translate" for s in SIX}), budget=10**6
        )
        assert len(session.starts) == 1
        assert [index for index, _text in session.turns] == [1, 1]

    def test_no_handoff_report_is_ever_asked_for(self):
        from book_maker.session_context import handoff_prompt

        _decisions, _candidates, session = _run(
            SIX, scripted({key_of(s): "translate" for s in SIX}), budget=1
        )
        asked = " ".join(session.asks)
        assert "Summary" not in asked
        assert handoff_prompt() not in asked
        assert "Context is compacting" not in asked

    @pytest.mark.parametrize(
        "compact_at,expected", [(2500, 2500), (None, DEFAULT_COMPACT_BUDGET)]
    )
    def test_the_budget_is_the_one_the_run_would_have_used(self, compact_at, expected):
        # explicit --context-compact-at first, else the same default a
        # session-mode translation on this model would work to
        translator = _bare_openai(context_compact_at=compact_at)
        assert translator.classify_session().budget() == expected

    def test_the_codex_thread_works_to_the_same_budget(self):
        from book_maker.translator.codex_translator import Codex

        translator = Codex.__new__(Codex)
        translator.model = "gpt-5.6-luna"
        translator.context_compact_at = 3000
        assert translator.classify_session().budget() == 3000

    def test_a_named_classify_model_sizes_the_window_by_its_own_model(
        self, monkeypatch
    ):
        # --plan-classify-model holds the classifier's conversation with a
        # model of its own, so the window it rolls over against is that
        # model's, not the one the book is translated by
        import book_maker.translator.chatgptapi_translator as chatgpt

        windows = {"translates-the-book": 8000, "rules-on-the-plan": 1234}
        monkeypatch.setattr(chatgpt, "compact_budget_for", windows.__getitem__)

        translator = _bare_openai(context_compact_at=None)
        translator.model = "translates-the-book"
        session = translator.classify_session(model="rules-on-the-plan")

        assert session.budget() == 1234

    def test_the_codex_thread_sizes_the_window_by_its_own_model_too(self, monkeypatch):
        import book_maker.translator.codex_translator as codex_module
        from book_maker.translator.codex_translator import Codex

        windows = {"translates-the-book": 8000, "rules-on-the-plan": 1234}
        monkeypatch.setattr(codex_module, "compact_budget_for", windows.__getitem__)

        translator = Codex.__new__(Codex)
        translator.model = "translates-the-book"
        translator.context_compact_at = None
        session = translator.classify_session(model="rules-on-the-plan")

        assert session.budget() == 1234

    def test_an_explicit_budget_still_outranks_the_classify_model(self, monkeypatch):
        # --context-compact-at is the operator saying the number; a named
        # classifier does not overrule it
        import book_maker.translator.chatgptapi_translator as chatgpt

        monkeypatch.setattr(chatgpt, "compact_budget_for", lambda model: 1234)
        translator = _bare_openai(context_compact_at=2500)
        translator.model = "translates-the-book"

        assert translator.classify_session(model="rules-on-the-plan").budget() == 2500


# ------------------------------------------------------------- 6. the ladder
#
# 5 → 3 → 1 → none, walked down by the endpoint's own replies. This section
# replaced the two rate breakers (`FORMAT_WARNING` and the `stopped` one) and
# their thresholds: a rate needs a floor of evidence before it means
# anything, and below that floor the old pair either said nothing while the
# run ground out three turns per signature, or abandoned a book's
# classification on one flaky reply.


class TestTheLadderDegrades:
    def test_one_failed_turn_alone_does_not_degrade(self):
        # a single flaky reply is not evidence about an endpoint; the next
        # turn is asked at the same rung it was
        endpoint = Endpoint(fail_on={1})
        _decisions, _candidates, _session = _run(_many(15), endpoint)
        assert endpoint.group_sizes == [5, 5, 5]

    def test_two_consecutive_failures_degrade_exactly_one_rung(self, capsys):
        endpoint = Endpoint(fail_on={1, 2})
        _decisions, _candidates, _session = _run(_many(16), endpoint)
        # 5, 5, then the rest at 3 — never straight to 1
        assert endpoint.group_sizes == [5, 5, 3, 3]
        assert FAILS_BEFORE_DEGRADING == 2
        out = capsys.readouterr().out
        assert out.count(degrade_line(5, 3)) == 1
        assert degrade_line(3, 1) not in out

    def test_the_degradation_line_is_the_one_the_operator_reads(self):
        assert degrade_line(5, 3) == (
            "plan: this endpoint missed the reply format twice at 5 per "
            "turn; continuing at 3 per turn"
        )
        assert degrade_line(3, 1) == (
            "plan: this endpoint missed the reply format twice at 3 per "
            "turn; continuing at 1 per turn"
        )

    def test_the_last_step_says_classification_is_over(self):
        assert stop_line(7) == (
            "plan: this endpoint missed the reply format twice even one at "
            "a time; classification stops here and the remaining 7 "
            "signature(s) are translated"
        )

    def test_the_last_step_with_nothing_left_names_no_remainder(self):
        # PIN (lead, 260920): "the remaining 0 signature(s)" is not a sentence
        # an operator should read; the clause is dropped when nothing remains.
        assert stop_line(0) == (
            "plan: this endpoint missed the reply format twice even one at "
            "a time; classification stops here"
        )

    def test_a_success_between_two_failures_resets_the_counter(self):
        # fail, success, fail: the two misses are not consecutive, so
        # nothing degrades — this is the difference between a ladder and a
        # failure count
        endpoint = Endpoint(fail_on={1, 3})
        _decisions, _candidates, _session = _run(_many(25), endpoint)
        assert endpoint.group_sizes == [5] * 5

    def test_a_fail_pair_is_spent_once(self, capsys):
        # the pair that degraded 5→3 must not also count toward 3→1: the
        # counter resets on the degradation, so the next step needs two
        # fresh misses
        # 22 = 5 + 5 + 3 + 3 + 3 + 3, so no turn is short of its rung
        endpoint = Endpoint(fail_on={1, 2, 3})
        _decisions, _candidates, _session = _run(_many(22), endpoint)
        assert endpoint.group_sizes == [5, 5, 3, 3, 3, 3]
        out = capsys.readouterr().out
        assert degrade_line(5, 3) in out
        assert degrade_line(3, 1) not in out

    def test_reaching_the_floor_takes_three_separate_pairs(self, capsys):
        endpoint = Endpoint(fail_on={1, 2, 3, 4, 5, 6})
        decisions, candidates, _session = _run(_many(20), endpoint)
        assert endpoint.group_sizes == [5, 5, 3, 3, 1, 1]
        out = capsys.readouterr().out
        assert degrade_line(5, 3) in out
        assert degrade_line(3, 1) in out
        # 20 - (5 + 5 + 3 + 3 + 1 + 1) = 2 never asked about
        assert stop_line(2) in out
        assert len(decisions) == len(candidates) == 20

    def test_nothing_is_asked_after_the_floor(self):
        endpoint = Endpoint(fail_on={1, 2, 3, 4, 5, 6})
        decisions, _candidates, session = _run(_many(20), endpoint)
        # the last turn is the sixth group turn; the two signatures left are
        # decided without buying anything
        assert endpoint.groups == 6
        assert _sizes(session)[-1] == 1
        for key in ("p.s18", "p.s19"):
            assert decisions[key_of(key)] == ("translate", NAMED_UNANSWERED)

    def test_the_rest_of_the_book_is_translated_by_policy_at_the_floor(self):
        # the `--plan-classify all` outcome, reached because the endpoint
        # could not answer rather than chosen: every row still comes back
        # decided, and none of them comes back a skip
        endpoint = Endpoint(fail_on=range(1, 20))
        decisions, candidates, _session = _run(_many(30), endpoint)
        assert len(decisions) == len(candidates) == 30
        floor_rows = [decisions[key_of(f"p.s{i:02d}")] for i in range(18, 30)]
        assert floor_rows == [("translate", NAMED_UNANSWERED)] * 12

    def test_there_is_no_re_promotion(self):
        # once degraded, a run of successes does not buy the rung back: an
        # endpoint that lost the format at five is not asked to prove it
        # again at the price of two more misses
        endpoint = Endpoint(fail_on={1, 2})
        _decisions, _candidates, _session = _run(_many(40), endpoint)
        assert endpoint.group_sizes == [5, 5] + [3] * 10

    def test_a_working_endpoint_never_leaves_the_top_rung(self, capsys):
        endpoint = Endpoint()
        decisions, candidates, _session = _run(_many(20), endpoint)
        assert endpoint.group_sizes == [5, 5, 5, 5]
        out = capsys.readouterr().out
        assert "missed the reply format" not in out
        assert len(decisions) == len(candidates) == 20
        assert {v for v, _ in decisions.values()} == {"skip"}

    def test_plan_mode_survives_the_whole_ladder(self):
        # nothing here raises: every row comes back decided, so the caller
        # writes a plan and translates rather than stopping
        def reply(text):
            return "not a verdict"

        decisions, candidates, _session = _run(_many(18), reply)
        assert set(decisions) == {c["key"] for c in candidates}

    def test_the_old_rate_breakers_are_gone(self):
        # they are replaced, not merely unused: a module still exporting
        # them would let a later change re-wire one in beside the ladder
        import book_maker.loader.classify.session as mod

        for name in (
            "FORMAT_WARNING",
            "FAILURE_RATE",
            "MIN_TRIPLES_BEFORE_WARNING",
            "MIN_UNITS_BEFORE_STOPPING",
            "_enough_to_stop",
            "UNITS_PER_TURN",
        ):
            assert not hasattr(mod, name), name


# ------------------------------------------------------- 6b. the progress line


class TestTheProgressLine:
    """`Classifying epub tags 12/49...`, rewritten in place.

    Classification runs before the first paragraph, so until this line
    existed a slow endpoint asking a book's worth of questions was
    indistinguishable from a stall.
    """

    @pytest.fixture(autouse=True)
    def _no_leftover_bars(self):
        # tqdm stacks a new bar under any bar still alive in the process, with
        # an extra newline and a cursor-up. Earlier tests that interrupt a
        # loader run leave theirs in unreachable cycles (tqdm keeps only weak
        # references), and in the full suite that shifted this bar down a row
        # (port 260923: failed 1-in-suite, passed alone, on the source branch
        # too). Collect them first; the assertions are about this bar alone.
        import gc

        gc.collect()

    def test_it_counts_signatures_decided_against_candidates(self, capsys):
        _decisions, _candidates, _session = _run(_many(12), Endpoint(), quiet=False)
        err = capsys.readouterr().err
        assert f"{PROGRESS_DESC} 0/12..." in err
        assert f"{PROGRESS_DESC} 12/12..." in err

    def test_it_is_rewritten_in_place_rather_than_line_by_line(self, capsys):
        _decisions, _candidates, _session = _run(_many(12), Endpoint(), quiet=False)
        err = capsys.readouterr().err
        # carriage returns, and at most the one newline the bar closes with
        assert "\r" in err
        assert err.count("\n") <= 1

    def test_the_rows_the_floor_defaults_still_count_as_done(self, capsys):
        # the bar must reach total even when the last signatures were never
        # asked about, or a run that degraded to the floor looks unfinished
        _decisions, _candidates, _session = _run(
            _many(20), Endpoint(fail_on=range(1, 20)), quiet=False
        )
        assert f"{PROGRESS_DESC} 20/20..." in capsys.readouterr().err

    def test_quiet_silences_it(self, capsys):
        # `--quiet` reaches this code on the translator, where the CLI sets
        # it — no flag of this module's own
        _decisions, _candidates, _session = _run(_many(12), Endpoint(), quiet=True)
        captured = capsys.readouterr()
        assert PROGRESS_DESC not in captured.err
        assert PROGRESS_DESC not in captured.out
        # and the rest of the plan narration is not silenced with it
        assert ENGAGE_WARNING in captured.out


# ---------------------------------------------------------------- 6c. metering


class _FakeUsage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.prompt_tokens_details = None


class _FakeCompletion:
    def __init__(self, text, prompt, completion):
        self.usage = _FakeUsage(prompt, completion)
        message = type("M", (), {"content": text, "refusal": None})()
        self.choices = [type("C", (), {"message": message})()]


def _metered_openai(reply, prompt=120, completion=7):
    """A real ChatGPTAPI with a real `UsageMeter` and a stubbed transport.

    Everything between `classify_over_session` and the meter is the shipped
    code: `ClassifierSession.ask` -> `_classify_turn` -> `_note_usage`.
    """
    from book_maker.translator.base_translator import UsageMeter
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI

    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "gpt-fake"
    translator.context_compact_at = 8000
    translator.extra_body = {}
    translator.usage = UsageMeter()
    translator.quiet = True
    translator._request = lambda call, model=None: _FakeCompletion(
        reply, prompt, completion
    )
    return translator


class TestClassificationIsMetered:
    """Classifier turns are paid context, billed to the translation meter.

    They were invisible on the progress bar and in the closing summary, so a
    book whose classification cost more than a chapter of translation showed
    nothing at all.
    """

    def test_every_turn_lands_in_the_same_counter_translation_uses(self):
        translator = _metered_openai("skip,skip,skip,skip,skip")
        decisions, candidates = classify_over_session(
            _ledger_with(_many(10)), translator
        )
        assert len(decisions) == len(candidates) == 10
        # two turns of five, and the meter grew by exactly their usage
        assert translator.usage.requests == 2
        assert translator.usage.prompt == 2 * 120
        assert translator.usage.completion == 2 * 7

    def test_the_singles_a_failure_is_recovered_with_are_billed_too(self):
        # the expensive case: one mis-shaped reply buys five more requests,
        # and that is precisely what the operator needs to see
        translator = _metered_openai("not a verdict")
        classify_over_session(_ledger_with(_many(5)), translator)
        # the group, then one single per signature
        assert translator.usage.requests == 1 + 5
        assert translator.usage.prompt == 6 * 120

    def test_it_is_the_meter_the_progress_bar_and_the_summary_read(self):
        translator = _metered_openai("skip,skip,skip,skip,skip")
        assert translator.usage_summary() is None
        classify_over_session(_ledger_with(_many(5)), translator)
        assert translator.usage_postfix() is not None
        assert translator.usage_summary() is not None


class TestTransportFailuresAreTerminal:
    def test_a_dead_endpoint_stops_classification_rather_than_retrying(self):
        def reply(text):
            raise RuntimeError("connection reset")

        with pytest.raises(PlanClassifyFatal):
            _run(SIX, reply)


# ----------------------------------------- 7 & 8. what must not have moved


class SchemaCapable(SessionOnly):
    """A route that can hold a conversation *and* has a JSON verdict."""

    def __init__(self, session, verdict="json"):
        super().__init__(session)
        self.verdict = verdict
        self.asked_json = 0

    def _probe_verdict(self, model=None):
        return self.verdict

    def supports_structured_json(self):
        return True

    def structured_json(self, prompt, schema, model=None, accept=None):
        self.asked_json += 1
        return {
            key: {"verdict": "translate", "content_type": "prose"}
            for key in schema["schema"]["required"]
        }


class TestSchemaCapableRoutesAreUntouched:
    @pytest.mark.parametrize("verdict", ["strict", "shape", "json"])
    def test_a_verdict_at_json_object_or_above_keeps_the_json_path(self, verdict):
        def reply(text):
            raise AssertionError("a schema-capable route must not be talked to")

        session = FakeSession(reply)
        translator = SchemaCapable(session, verdict)

        decisions, _candidates = classify_plan(_ledger_with(SIX), translator)
        assert translator.asked_json
        assert translator.sessions_opened == 0
        assert not session.starts
        assert {v for v, _ in decisions.values()} == {"translate"}


class TestMTRoutesAreUntouched:
    @pytest.mark.parametrize("route", ["google", "deepl", "caiyun", "tencent"])
    def test_an_engine_that_only_translates_holds_no_conversation(self, route):
        from book_maker.translator import FORMAT_DICT

        translator = FORMAT_DICT[route].__new__(FORMAT_DICT[route])
        assert not can_session_classify(translator)

    def test_the_classifier_still_refuses_them_loudly(self):
        from book_maker.loader.classify.model import PlanClassifyError
        from book_maker.translator.google_translator import Google

        with pytest.raises(PlanClassifyError):
            classify_plan(_ledger_with(SIX), Google("k", "zh-hans"))


class TestTheRoutesThatOfferOne:
    def test_the_codex_route_can_hold_a_classifier_session(self):
        from book_maker.translator.codex_translator import Codex

        assert can_session_classify(Codex.__new__(Codex))

    def test_so_can_the_openai_route_and_its_resellers(self):
        from book_maker.translator.chatgptapi_translator import ChatGPTAPI
        from book_maker.translator.orcarouter_translator import OrcaRouterTranslator

        assert can_session_classify(ChatGPTAPI.__new__(ChatGPTAPI))
        assert can_session_classify(OrcaRouterTranslator.__new__(OrcaRouterTranslator))

    @pytest.mark.parametrize("route", ["anthropic", "gemini", "qwen"])
    def test_a_route_with_no_session_of_its_own_does_not_pretend(self, route):
        # they answer single prompts, which is not the same thing: nothing
        # here builds them a conversation, so plan mode stays as it was
        from book_maker.translator import FORMAT_DICT

        cls = FORMAT_DICT[route]
        assert not can_session_classify(cls.__new__(cls))
