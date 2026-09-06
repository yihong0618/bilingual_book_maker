"""Plan classification over a plain conversation, for routes with no JSON.

The contract: an endpoint whose structured-output verdict is below
`json_object` but which can hold a conversation now *plans* instead of
dropping to tag mode. It is asked for three verdicts per turn, in one
append-only session, and every reply is checked verbatim — no fuzzy
matching, no JSON anywhere.

The schema-capable path is not this file's subject and must not move:
`test_structured_classify.py` and `test_translation_plan.py` pin it, and
the tests at the bottom here check that a route with a verdict never
reaches this entry at all.
"""

import pytest

from book_maker.loader.classify import (
    can_session_classify,
    classify_plan,
    session_classify_engaged,
)
from book_maker.loader.classify.model import PlanClassifyFatal
from book_maker.loader.classify.session import (
    ENGAGE_WARNING,
    FORMAT_WARNING,
    NAMED_BY_SESSION,
    NAMED_UNANSWERED,
    NAMED_UNSURE,
    UNITS_PER_TURN,
    build_trunk,
    classify_over_session,
    parse_verdicts,
    render_turn,
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


def _run(signatures, reply, budget=8000):
    session = FakeSession(reply, budget=budget)
    translator = SessionOnly(session)
    decisions, candidates = classify_over_session(_ledger_with(signatures), translator)
    return decisions, candidates, session


SIX = ["p.a", "p.b", "p.c", "p.d", "p.e", "p.f"]


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
        assert not session_classify_engaged(PromptOnly("k", "zh-hans"))

    def test_three_units_per_turn(self):
        _decisions, _candidates, session = _run(
            SIX, scripted({key_of(s): "translate" for s in SIX})
        )
        assert UNITS_PER_TURN == 3
        assert len(session.asks) == 2
        for turn in session.asks:
            assert turn.count("occurrence(s)") == 3

    def test_a_final_partial_turn_asks_for_that_many_verdicts(self):
        signatures = SIX[:4]
        _decisions, _candidates, session = _run(
            signatures, scripted({key_of(s): "skip" for s in signatures})
        )
        assert len(session.asks) == 2
        assert session.asks[-1].count("occurrence(s)") == 1


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
                return "skip,unsure,translate"
            return "not a verdict"  # the second triple, and its singles

        ledger = _ledger_with(SIX)
        decisions, _candidates = classify_over_session(
            ledger, SessionOnly(FakeSession(reply))
        )
        for key, (verdict, content_type) in decisions.items():
            ledger.decide(key, verdict, "llm", content_type)
        assert not ledger.undecided_keys()
        assert sorted(row["content_type"] for row in ledger.rows.values()) == sorted(
            [NAMED_BY_SESSION, NAMED_UNSURE, NAMED_BY_SESSION] + [NAMED_UNANSWERED] * 3
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


class TestMalformedFallsBackToSingles:
    def _run_with_one_bad_triple(self, bad_reply="I could not decide"):
        state = {"turns": 0}

        def reply(text):
            state["turns"] += 1
            if state["turns"] == 1:
                return bad_reply
            return "skip"

        return _run(SIX[:3], reply)

    def test_a_malformed_triple_is_re_asked_one_unit_at_a_time(self):
        decisions, candidates, session = self._run_with_one_bad_triple()
        # the triple, then one turn per unit, all in the same session
        assert len(session.asks) == 1 + 3
        assert session.starts == [build_trunk()]
        for turn in session.asks[1:]:
            assert turn.count("occurrence(s)") == 1
            assert turn.startswith("1. ")
        assert {v for v, _ in decisions.values()} == {"skip"}
        assert all(name == NAMED_BY_SESSION for _, name in decisions.values())

    def test_a_single_that_still_fails_becomes_translate(self):
        def reply(text):
            return "no idea"

        decisions, _candidates, session = _run(SIX[:3], reply)
        assert len(session.asks) == 1 + 3
        assert {v for v, _ in decisions.values()} == {"translate"}
        assert all(name == NAMED_UNANSWERED for _, name in decisions.values())


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
        assert "skip,translate,unsure" in trunk

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


# ------------------------------------------------------ 6. circuit breakers


def _many(n):
    return [f"p.s{i:02d}" for i in range(n)]


def _singles_answer(text):
    """Every triple malformed, every single answered."""
    return "skip" if text.count("occurrence(s)") == 1 else "no idea"


class TestCircuitBreakers:
    def test_repeated_format_misses_warn_once(self, capsys):
        # 18 signatures = 6 triples, all malformed: the fifth is where the
        # warning has seen enough to be worth printing
        decisions, candidates, _session = _run(_many(18), _singles_answer)
        out = capsys.readouterr().out
        assert out.count(FORMAT_WARNING) == 1
        assert len(decisions) == len(candidates) == 18
        assert {v for v, _ in decisions.values()} == {"skip"}

    def test_one_bad_triple_out_of_one_says_nothing(self, capsys):
        # 100% of one triple is not evidence about an endpoint, and a run
        # that warns on its first flaky reply teaches the operator to ignore
        # the line
        decisions, _candidates, _session = _run(SIX[:3], _singles_answer)
        assert FORMAT_WARNING not in capsys.readouterr().out
        assert {v for v, _ in decisions.values()} == {"skip"}

    def test_the_warning_waits_for_five_triples(self, capsys):
        from book_maker.loader.classify.session import MIN_TRIPLES_BEFORE_WARNING

        assert MIN_TRIPLES_BEFORE_WARNING == 5
        # four triples, all falling back to singles: still under the floor
        _decisions, _candidates, session = _run(_many(12), _singles_answer)
        assert len([t for t in session.asks if t.count("occurrence(s)") == 3]) == 4
        assert FORMAT_WARNING not in capsys.readouterr().out

        # the fifth is what earns it
        _decisions, _candidates, _session = _run(_many(15), _singles_answer)
        assert FORMAT_WARNING in capsys.readouterr().out

    def test_the_format_warning_names_the_way_out(self):
        assert FORMAT_WARNING == (
            "plan: this endpoint keeps missing the reply format — singles "
            "cost three times the turns; --plan-classify all skips "
            "classification"
        )

    def test_a_small_book_that_fails_wholesale_stops_at_once(self, capsys):
        # nine signatures: the floor of 15 units can never be reached, so the
        # evidence is that nothing asked has been answerable. Grinding
        # singles through the rest buys three turns per signature and no
        # verdicts.
        def reply(text):
            return "nothing parseable here"

        decisions, candidates, session = _run(_many(9), reply)
        # the first triple and its three singles; then the breaker trips and
        # nothing more is bought
        assert len(session.asks) == 4
        assert len(decisions) == len(candidates) == 9
        assert {v for v, _ in decisions.values()} == {"translate"}
        assert all(name == NAMED_UNANSWERED for _, name in decisions.values())
        out = capsys.readouterr().out
        assert "classification stops here" in out
        assert "the remaining 6 are translated" in out

    def test_a_small_book_that_only_partly_fails_keeps_asking(self, capsys):
        # the same nine signatures, but the first triple's first single
        # answers: not "all of them failed", and 15 units are out of reach,
        # so nothing stops
        state = {"singles": 0}

        def reply(text):
            if text.count("occurrence(s)") == 1:
                state["singles"] += 1
                return "skip" if state["singles"] == 1 else "no idea"
            return "no idea"

        decisions, candidates, session = _run(_many(9), reply)
        out = capsys.readouterr().out
        assert "classification stops here" not in out
        assert len(session.asks) == 3 * (1 + 3)
        assert len(decisions) == len(candidates) == 9

    def test_a_big_book_waits_for_fifteen_units(self, capsys):
        from book_maker.loader.classify.session import MIN_UNITS_BEFORE_STOPPING

        assert MIN_UNITS_BEFORE_STOPPING == 15

        def reply(text):
            return "nothing parseable here"

        # 18 signatures: 15 units is reached at the end of the fifth triple,
        # which is where the asking stops — four triples' worth of grinding
        # was bought first, on purpose
        decisions, candidates, session = _run(_many(18), reply)
        assert len(session.asks) == 5 * (1 + 3)
        assert len(decisions) == len(candidates) == 18
        out = capsys.readouterr().out
        assert "could not answer 15 of 15 signature(s)" in out
        assert "the remaining 3 are translated" in out

    def test_a_working_endpoint_trips_neither(self, capsys):
        decisions, _candidates, _session = _run(
            SIX, scripted({key_of(s): "skip" for s in SIX})
        )
        out = capsys.readouterr().out
        assert FORMAT_WARNING not in out
        assert "classification stops here" not in out
        assert len(decisions) == 6

    def test_an_occasional_miss_does_not_stop_anything(self, capsys):
        # one bad triple in six, recovered by singles: under both thresholds
        # by unit count, so the run keeps asking
        eighteen = _many(18)
        state = {"turns": 0}

        def reply(text):
            state["turns"] += 1
            if state["turns"] == 4:
                return "hmm"
            asked = text.count("occurrence(s)")
            return ",".join(["skip"] * asked)

        decisions, candidates, session = _run(eighteen, reply)
        out = capsys.readouterr().out
        assert "classification stops here" not in out
        assert len(decisions) == len(candidates) == 18
        assert {v for v, _ in decisions.values()} == {"skip"}

    def test_plan_mode_survives_both(self):
        # neither breaker raises: every row comes back decided, so the caller
        # writes a plan and translates rather than stopping
        def reply(text):
            return "not a verdict"

        decisions, candidates, _session = _run(_many(18), reply)
        assert set(decisions) == {c["key"] for c in candidates}


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
        assert not session_classify_engaged(translator)

        decisions, _candidates = classify_plan(_ledger_with(SIX), translator)
        assert translator.asked_json
        assert translator.sessions_opened == 0
        assert not session.starts
        assert {v for v, _ in decisions.values()} == {"translate"}

    @pytest.mark.parametrize("verdict", [False, "unsupported", "request rejected: 400"])
    def test_anything_below_it_engages_the_session(self, verdict):
        translator = SchemaCapable(FakeSession(lambda text: "skip"), verdict)
        assert session_classify_engaged(translator)

    def test_a_probe_that_raises_is_not_a_verdict(self):
        class Broken(SchemaCapable):
            def _probe_verdict(self, model=None):
                raise RuntimeError("no route to host")

        assert session_classify_engaged(Broken(FakeSession(lambda text: "skip")))


class TestMTRoutesAreUntouched:
    @pytest.mark.parametrize("route", ["google", "deepl", "caiyun", "tencent"])
    def test_an_engine_that_only_translates_holds_no_conversation(self, route):
        from book_maker.translator import FORMAT_DICT

        translator = FORMAT_DICT[route].__new__(FORMAT_DICT[route])
        assert not can_session_classify(translator)
        assert not session_classify_engaged(translator)

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
