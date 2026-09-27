"""`book_maker/classifier.py`: the request layer both classification callers use.

Packet F (260923): a `Question` goes to the first backend of the run's
preference order that can take it; the reply is linted once for everybody
(unknown ids dropped and listed, out-of-set values set aside); what a missing
id means stays the caller's.
"""

import pytest

from book_maker.classifier import (
    DEFAULT_PREFER,
    SESSION_FIRST,
    Answer,
    Classifier,
    NoBackend,
    Question,
    Reply,
    SchemaBackend,
    SessionBackend,
)
from book_maker.loader.classify import classify_plan
from book_maker.translator.base_translator import UsageMeter


class Fake:
    """A backend that records what it was asked and answers from a script."""

    def __init__(self, name, answer=None, can=True, weak=False, meter=None):
        self.name = name
        self.answer = answer if answer is not None else {}
        self._can = can
        self._weak = weak
        self.asked = []
        self.usage = meter

    def can(self, question):
        return self._can(question) if callable(self._can) else self._can

    def weak(self, question):
        return self._weak

    def why_not(self, question):
        return f"{self.name} says no"

    def ask(self, question):
        self.asked.append(question)
        answer = self.answer(question) if callable(self.answer) else self.answer
        reply = Reply(answer)
        reply.usage = {"prompt_tokens": 3, "completion_tokens": 1}
        return reply


def _q(**kw):
    kw.setdefault("prompt", "p")
    kw.setdefault("schema", {"name": "s", "strict": True, "schema": {}})
    kw.setdefault("candidates", {"a": ("x", "y"), "b": ("x", "y")})
    return Question(**kw)


class TestDispatch:
    def test_the_first_backend_that_can_answers(self):
        schema, session = Fake("schema", {"a": "x"}), Fake("session", {"a": "y"})
        c = Classifier(None, "m", backends=[schema, session])
        assert c.ask(_q()).backend == "schema"
        assert schema.asked and not session.asked

    def test_the_session_first_order_asks_the_session(self):
        schema, session = Fake("schema"), Fake("session")
        c = Classifier(None, "m", prefer=SESSION_FIRST, backends=[schema, session])
        assert c.prefer == ("session", "schema")
        assert c.ask(_q()).backend == "session"

    def test_one_that_cannot_is_passed_over(self):
        schema, session = Fake("schema", can=False), Fake("session")
        c = Classifier(None, "m", backends=[schema, session])
        assert c.ask(_q()).backend == "session"

    def test_a_weak_structured_channel_yields_a_text_question(self):
        # below json_object and able to hold a conversation: the
        # conversation is asked (the rule session_classify_engaged states)
        schema, session = Fake("schema", weak=True), Fake("session")
        c = Classifier(None, "m", backends=[schema, session])
        assert c.text_backend() == "session"
        # ...but a weak channel alone still answers (its prompt rung)
        alone = Classifier(None, "m", backends=[Fake("schema", weak=True)])
        assert alone.text_backend() == "schema"

    def test_an_image_on_a_text_only_classifier_raises_no_backend(self):
        c = Classifier(
            None,
            "m",
            backends=[Fake("session", can=lambda q: q.image_png is None)],
        )
        with pytest.raises(NoBackend, match="an image"):
            c.ask(_q(image_png=b"png"))

    def test_the_default_order(self):
        assert DEFAULT_PREFER == ("schema", "session")
        assert SESSION_FIRST == ("session", "schema")


class TestTheLint:
    def test_unknown_ids_are_dropped_and_listed(self):
        c = Classifier(None, "m", backends=[Fake("schema", {"a": "x", "zz": "x"})])
        answer = c.ask(_q())
        assert answer.values == {"a": "x"}
        assert answer.unknown_ids == ("zz",)

    def test_a_value_outside_its_set_is_set_aside(self):
        c = Classifier(None, "m", backends=[Fake("schema", {"a": "x", "b": "q"})])
        answer = c.ask(_q())
        assert answer.values == {"a": "x"}
        assert answer.invalid == {"b": "q"}

    def test_a_missing_id_stays_missing(self):
        c = Classifier(None, "m", backends=[Fake("schema", {"a": "x"})])
        assert "b" not in c.ask(_q()).values

    def test_an_object_answer_is_checked_on_its_field(self):
        c = Classifier(
            None,
            "m",
            backends=[
                Fake(
                    "schema",
                    {"a": {"verdict": "x", "note": "n"}, "b": {"verdict": "nope"}},
                )
            ],
        )
        answer = c.ask(_q(field="verdict"))
        assert answer.values == {"a": {"verdict": "x", "note": "n"}}
        assert answer.invalid == {"b": {"verdict": "nope"}}

    def test_usage_and_model_pass_through(self):
        c = Classifier(None, "m", backends=[Fake("schema", {"a": "x"})])
        answer = c.ask(_q())
        assert isinstance(answer, Answer)
        assert answer.usage["prompt_tokens"] == 3
        assert answer.usage["completion_tokens"] == 1
        assert answer.model == "m"


def test_describe_names_model_address_backends_and_source():
    c = Classifier(
        None,
        "gpt-5.6-luna",
        backends=[Fake("schema"), Fake("session")],
        source="cli",
        base="https://api.openai.com/v1",
    )
    assert (
        c.describe("schema")
        == "gpt-5.6-luna at https://api.openai.com/v1 via schema (cli)"
    )
    assert c.describe() == (
        "gpt-5.6-luna at https://api.openai.com/v1 via schema/session (cli)"
    )


def test_the_classifier_reports_its_backend_s_meter():
    meter = UsageMeter()
    c = Classifier(None, "jev-latest", backends=[Fake("jev", meter=meter)])
    assert c.usage is meter


# ------------------------------------------------------------- real backends


class Translator:
    """Just enough of a translator for the two built-in backends."""

    model = "t-model"

    def __init__(self, verdict="strict", vision="verified", session=None):
        self.usage = UsageMeter()
        self.verdict = verdict
        self._vision = vision
        self._session = session
        self.calls = []

    def _probe_verdict(self, model=None):
        return self.verdict

    def supports_structured_json(self):
        return True

    def structured_json(self, prompt, schema, model=None, accept=None):
        self.calls.append(("text", prompt, model))
        self.usage.note(prompt=10, completion=2)
        return {"a": "x"}

    def structured_json_with_image(
        self, prompt, schema, image_png, model=None, accept=None, deadline=None
    ):
        self.calls.append(("image", prompt, model, deadline))
        self.usage.note(prompt=100, completion=5)
        return {"a": "y"}

    def vision_verdict(self, model=None):
        return self._vision

    def classify_session(self, model=None):
        return self._session


class TestTheSchemaBackend:
    def test_a_text_question_goes_to_structured_json_metered(self):
        t = Translator()
        answer = Classifier(t, "m2", backends=[SchemaBackend(t, "m2")]).ask(_q())
        assert t.calls == [("text", "p", "m2")]
        assert answer.values == {"a": "x"}
        assert (answer.usage["prompt_tokens"], answer.usage["completion_tokens"]) == (
            10,
            2,
        )

    def test_an_image_question_carries_the_image_and_the_deadline(self):
        t = Translator()
        c = Classifier(t, "m2", backends=[SchemaBackend(t, "m2")])
        answer = c.ask(_q(image_png=b"png", deadline=123.0))
        assert t.calls == [("image", "p", "m2", 123.0)]
        assert answer.values == {"a": "y"}

    def test_an_image_is_refused_until_the_probe_image_was_read(self):
        t = Translator(vision="unsupported")
        c = Classifier(t, "m2", backends=[SchemaBackend(t, "m2")])
        with pytest.raises(NoBackend, match="did not read the probe image"):
            c.ask(_q(image_png=b"png"))
        assert t.calls == []


class Session:
    def __init__(self, replies):
        self.replies = list(replies)
        self.started = []
        self.asked = []

    def budget(self):
        return 0

    def start(self, trunk):
        self.started.append(trunk)

    def ask(self, text):
        self.asked.append(text)
        return self.replies.pop(0)


class TestTheSessionBackend:
    def test_it_carries_no_image(self):
        backend = SessionBackend(Translator(session=Session([])))
        assert not backend.can(_q(trunk="T", image_png=b"png"))

    def test_a_text_question_dispatched_to_it_is_refused_by_name(self):
        """The session answers through `open()` and plan mode's own loop;
        `Classifier.ask` reaching it says so instead of an AttributeError
        (Codex review 01a0dde0, docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md)."""
        translator = Translator(session=Session([]))
        backend = SessionBackend(translator)
        assert backend.can(_q(trunk="T"))
        with pytest.raises(NotImplementedError, match="own conversation"):
            backend.ask(_q(trunk="T"))


def test_the_codex_agent_cannot_see_images():
    """A codex classifier has a session and no image channel: an image
    question is `NoBackend`, which the PDF route reports and skips."""
    from book_maker.translator.codex_translator import Codex

    class Server:
        pass

    codex = Codex("", "zh-hans", server=Server())
    c = Classifier(codex, None)
    assert c.text_backend() == "session"
    with pytest.raises(NoBackend):
        c.ask(_q(image_png=b"png", trunk="T"))


# ---------------------------------------------------- plan classification


class TestPlanClassificationGoesThroughTheClassifier:
    def test_every_page_is_one_question_with_the_module_s_prompt(self, monkeypatch):
        from book_maker.loader.classify import model as model_entry

        monkeypatch.setattr(model_entry, "gather_candidates", lambda ledger: ledger)
        page = [
            {"key": "block:p.a", "units": 1, "chars": 9, "samples": ["one"]},
            {"key": "block:p.b", "units": 1, "chars": 9, "samples": ["two"]},
        ]

        def answer(question):
            return {
                key: {"content_type": "prose", "verdict": "translate"}
                for key in question.candidates
            }

        schema = Fake("schema", answer)
        decisions, _ = classify_plan(page, Classifier(None, "m", backends=[schema]))
        assert decisions == {
            "block:p.a": ("translate", "prose"),
            "block:p.b": ("translate", "prose"),
        }
        (question,) = schema.asked
        assert question.prompt == model_entry.build_prompt(page)
        assert question.schema == model_entry.build_schema(page)
        # the lean fields a per-candidate backend (jev) reads instead
        assert question.context == model_entry.build_context(page)
        assert question.per_candidate["block:p.a"] == (
            model_entry.candidate_pointer(1, page[0])
        )
        assert question.criteria == model_entry.CRITERIA

    def test_a_label_only_backend_is_recorded_with_its_probability(self, monkeypatch):
        from book_maker.loader.classify import model as model_entry

        monkeypatch.setattr(model_entry, "gather_candidates", lambda ledger: ledger)
        page = [{"key": "block:p.a", "units": 1, "chars": 9, "samples": ["one"]}]

        class Jev(Fake):
            def ask(self, question):
                reply = super().ask(question)
                reply.confidence = {"block:p.a": 0.83}
                return reply

        jev = Jev("jev", {"block:p.a": "skip"})
        decisions, _ = classify_plan(page, Classifier(None, "jev", backends=[jev]))
        assert decisions == {
            "block:p.a": ("skip", "unnamed (jev verdict skip, confidence 0.83)")
        }

    def test_agent_order_takes_the_session_ladder(self, monkeypatch):
        from book_maker.loader.classify import session as session_entry

        seen = []
        monkeypatch.setattr(
            session_entry,
            "classify_over_session",
            lambda ledger, classifier, model=None: seen.append(classifier) or ({}, []),
        )
        import book_maker.loader.classify as package

        monkeypatch.setattr(
            package,
            "classify_over_session",
            session_entry.classify_over_session,
        )
        c = Classifier(
            None, "m", prefer=SESSION_FIRST, backends=[Fake("schema"), Fake("session")]
        )
        classify_plan([], c)
        assert seen == [c]
