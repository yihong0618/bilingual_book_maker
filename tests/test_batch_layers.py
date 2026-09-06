"""Implementation-level cover for the batching layers the acceptance file leaves.

`tests/test_batch_groups_and_markers.py` holds the contracts the change set
is accepted against. This file covers what the design names and those
contracts do not reach: layer E's recording rules on the openai route,
gemini's own JSON batch raising instead of retrying, the prose the strict
request carries so a model can produce the id-echo shape, the marker
write-back through the real insertion path, and the schema bump that the
partition change forces.
"""

import json
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup as bs

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.helper import EPUBBookLoaderHelper
from book_maker.loader.plan import DisplayResolver, partition_soup
from book_maker.translator.base_translator import BATCH_DELIMITER, BatchMismatch
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    batch_translation_model,
    single_field_name,
)

BATCH_FIELD = batch_field_name("Chinese")
ITEM_FIELD = single_field_name("Chinese")


# ------------------------------------------------ E. recording (openai)


def _batch_parsed(items, raw=None):
    message = SimpleNamespace(
        parsed=SimpleNamespace(**{BATCH_FIELD: items}),
        refusal=None,
        content=raw,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )


def _item(i, text):
    return SimpleNamespace(**{"id": i, ITEM_FIELD: text})


def _session_openai(verdict="strict", **kwargs):
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    t = ChatGPTAPI(key="k", language="Chinese", **kwargs)
    t.model = "test-model"
    t.capabilities.record("test-model", verdict)
    return t


class TestOpenAISessionRecordsOneExchange:
    def _wire(self, translator, answer):
        sent = []

        def parse(**call):
            sent.append(call)
            return answer

        def create(**call):
            sent.append(call)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="译文", refusal=None)
                    )
                ],
                usage=None,
            )

        translator.openai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create, parse=parse)
            )
        )
        return sent

    def test_a_good_batch_is_one_pair_not_n(self):
        t = _session_openai()
        answer = _batch_parsed(
            [_item(0, "一"), _item(1, "二"), _item(2, "三")], raw='{"x": 1}'
        )
        sent = self._wire(t, answer)

        assert t.translate_list(["one", "two", "three"]) == ["一", "二", "三"]

        messages = t.session.messages()
        assert len(messages) == 2
        # exactly what was sent, so the next request extends the cached prefix
        assert messages[0]["content"] == sent[0]["messages"][-1]["content"]
        assert messages[1]["content"] == '{"x": 1}'

    def test_the_recorded_user_content_carries_the_ids_that_were_sent(self):
        t = _session_openai()
        answer = _batch_parsed([_item(0, "一"), _item(1, "二")])
        self._wire(t, answer)

        t.translate_list(["one", "two"])

        content = t.session.messages()[0]["content"]
        assert '"id": 0' in content and '"id": 1' in content

    def test_a_failed_batch_is_not_appended(self):
        t = _session_openai()
        # two sources, one answer: a mismatch, and a bad exchange in the
        # prefix is worse than the cache miss its absence costs
        answer = _batch_parsed([_item(0, "一")])
        self._wire(t, answer)

        with pytest.raises(BatchMismatch):
            t.translate_list(["one", "two"])

        assert t.session.messages() == []

    def test_window_mode_still_records_one_pair_per_line(self):
        t = ChatGPTAPI(
            key="k",
            language="Chinese",
            context_flag=True,
            context_paragraph_limit=8,
        )
        t.model = "test-model"
        t.capabilities.record("test-model", "strict")
        self._wire(t, _batch_parsed([_item(0, "一"), _item(1, "二")]))

        t.translate_list(["one", "two"])

        assert t.context_list == ["one", "two"]
        assert t.context_translated_list == ["一", "二"]


class TestOpenAIDelimiterSessionRecording:
    """The non-strict openai path uses the shared delimiter carrier."""

    def _translator(self, reply):
        t = _session_openai(verdict="unsupported")
        sent = []

        def create(**call):
            sent.append(call)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=reply, refusal=None)
                    )
                ],
                usage=None,
            )

        t.openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create, parse=None))
        )
        t.sent = sent
        return t

    def test_one_exchange_for_a_good_delimiter_batch(self):
        t = self._translator(BATCH_DELIMITER.join(["一", "二"]))

        assert t.translate_list(["one", "two"]) == ["一", "二"]

        messages = t.session.messages()
        assert len(messages) == 2
        assert messages[0]["content"] == t.sent[0]["messages"][-1]["content"]

    def test_a_misaligned_delimiter_batch_records_nothing(self):
        t = self._translator("一二三")

        with pytest.raises(BatchMismatch):
            t.translate_list(["one", "two"])

        assert t.session.messages() == []
        assert len(t.sent) == 1  # no per-line self-repair


# --------------------------------------------- C. the strict request's prose


class TestStructuredRequestShape:
    def test_the_prose_tail_describes_the_id_echo_shape(self):
        t = ChatGPTAPI(key="k", language="Chinese")
        messages = t._create_structured_batch_messages(["one", "two"])
        content = messages[-1]["content"]
        assert "EXACTLY 2" in content
        assert "'id'" in content
        assert ITEM_FIELD in content

    def test_the_source_payload_is_id_keyed(self):
        t = ChatGPTAPI(key="k", language="Chinese")
        messages = t._create_structured_batch_messages(["one", "two"])
        payload = json.loads(
            messages[-1]["content"][
                messages[-1]["content"].index("{") : messages[-1]["content"].rindex("}")
                + 1
            ]
        )
        assert payload["paragraphs"] == [
            {"id": 0, "text": "one"},
            {"id": 1, "text": "two"},
        ]

    def test_the_schema_is_per_count(self):
        assert batch_translation_model("Chinese", 2) is batch_translation_model(
            "Chinese", 2
        )
        assert batch_translation_model("Chinese", 2) is not batch_translation_model(
            "Chinese", 3
        )


# ------------------------------------------------------ gemini raises


class TestGeminiRaisesBatchMismatch:
    def _gemini(self, payload):
        from book_maker.translator.gemini_translator import Gemini

        g = Gemini.__new__(Gemini)
        g.interval = 0
        g._fatal_error_detected = False
        return g, payload

    def test_a_short_reply_raises_instead_of_returning_none(self):
        from book_maker.translator.gemini_translator import Gemini

        g = Gemini.__new__(Gemini)
        body = json.dumps({"translated_paragraphs": ["一", "二"]})
        with pytest.raises(BatchMismatch):
            g._parse_batch_response(body, 3)

    def test_a_matching_reply_parses(self):
        from book_maker.translator.gemini_translator import Gemini

        g = Gemini.__new__(Gemini)
        body = json.dumps({"translated_paragraphs": ["一", "二"]})
        assert g._parse_batch_response(body, 2) == ["一", "二"]

    def test_a_mismatch_is_not_retried(self):
        from book_maker.translator.gemini_translator import _should_retry

        assert _should_retry(BatchMismatch("nope")) is False
        assert _should_retry(ValueError("transient")) is True


# --------------------------------------- markers through the insertion path


def _fp(html):
    soup = bs(f"<html><body>{html}</body></html>", "html.parser")
    return soup, partition_soup(soup, DisplayResolver([]), "chap.xhtml")


def _loader():
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = SimpleNamespace(TRANSLATION_ERROR_MARKER=None)
    loader.language_tag = "zh-Hant"
    loader.translation_style = ""
    loader.single_translate = False
    loader.exclude_translate_tags = "sup,code"
    loader.helper = EPUBBookLoaderHelper(
        SimpleNamespace(TRANSLATION_ERROR_MARKER=None), "", False, "zh-Hant"
    )
    return loader


class TestMarkerWriteBack:
    def test_bilingual_restores_a_clone_of_the_source_node(self):
        soup, fp = _fp('<p>Press <code id="k">Ctrl+C</code> to stop it now.</p>')
        unit = fp.units[0]
        token = next(iter(unit.markers))
        loader = _loader()

        loader._insert_plan_translation(unit, f"按 {token} 立即停止。")

        codes = soup.find_all("code")
        assert len(codes) == 2  # the original, and the one in the translation
        assert [c.get_text() for c in codes] == ["Ctrl+C", "Ctrl+C"]
        # the original keeps the anchor; the second rendering must not
        assert [c.get("id") for c in codes] == ["k", None]
        assert "⟦" not in soup.get_text()

    def test_a_dropped_marker_still_gets_its_node(self):
        soup, fp = _fp("<p>Press <code>Ctrl+C</code> to stop it now.</p>")
        unit = fp.units[0]
        loader = _loader()

        loader._insert_plan_translation(unit, "立即停止。")

        assert len(soup.find_all("code")) == 2
        assert "⟦" not in soup.get_text()

    def test_single_translate_moves_the_node_instead_of_cloning_it(self):
        soup, fp = _fp('<p>Press <code id="k">Ctrl+C</code> to stop it now.</p>')
        unit = fp.units[0]
        token = next(iter(unit.markers))
        loader = _loader()

        loader._insert_plan_translation(unit, f"按 {token} 立即停止。", "", True)

        codes = soup.find_all("code")
        assert len(codes) == 1  # moved, not duplicated
        assert codes[0].get("id") == "k"  # the original was replaced, so its id stays
        assert "Press" not in soup.get_text()
        assert "⟦" not in soup.get_text()

    def test_an_invented_marker_never_reaches_the_book(self):
        soup, fp = _fp("<p>Press <code>Ctrl+C</code> to stop it now.</p>")
        unit = fp.units[0]
        token = next(iter(unit.markers))
        loader = _loader()

        loader._insert_plan_translation(unit, f"按 {token}⟦img9⟧ 立即停止。")

        assert "⟦img9⟧" not in soup.get_text()
        assert "⟦" not in soup.get_text()

    def test_the_next_paragraphs_literal_token_is_left_alone(self):
        """Only what the insertion path just wrote is searched.

        Scanning the owner's next sibling instead put the <code> into the
        *following source paragraph* in single-translate mode, where the same
        token happens to be printed as literal text — the node left the
        translation it belonged to and landed a paragraph later.
        """
        soup, fp = _fp(
            '<p>Press <code id="k">Ctrl+C</code> now.</p>'
            "<p>Print ⟦code1⟧ literally.</p>"
        )
        unit = fp.units[0]
        token = next(iter(unit.markers))
        assert token == "⟦code1⟧", "the collision is the point of the test"
        loader = _loader()

        loader._insert_plan_translation(unit, f"按 {token} 停止。", "", True)

        paragraphs = soup.find_all("p")
        assert len(soup.find_all("code")) == 1  # moved exactly once
        assert soup.find("code").parent is paragraphs[0]
        # the second paragraph is source text nobody translated yet
        assert paragraphs[1].get_text() == "Print ⟦code1⟧ literally."


# ------------------------------------- the marker source is the wrapper


def test_anchor_wrapped_image_marks_the_anchor_not_the_image():
    """`<a href><img/></a>`: the link is part of what is being preserved.

    The anchor owns no translatable text and renders exactly what the image
    renders, so it is the marker source. Marking the <img> alone restored the
    picture without its link — visible in single-translate mode, where the
    original markup is gone.
    """
    soup, fp = _fp('<p>See <a href="full.jpg"><img src="thumb.jpg"/></a> for it.</p>')
    unit = fp.units[0]

    assert len(unit.markers) == 1
    token, source = next(iter(unit.markers.items()))
    assert source.name == "a"
    assert unit.text == f"See {token} for it."

    loader = _loader()
    loader._insert_plan_translation(unit, f"詳見 {token} 。")
    anchors = soup.find_all("a")
    assert len(anchors) == 2  # the original and the clone
    assert [a.get("href") for a in anchors] == ["full.jpg", "full.jpg"]
    assert anchors[1].find("img") is not None


# --------------------------------- reconcile against the issued tokens


class TestReconcileAgainstIssuedTokens:
    """Three classes of marker-shaped token, three different answers."""

    def test_a_literal_token_the_book_prints_twice_survives_both_times(self):
        from book_maker.loader.markers import reconcile_markers

        # collision avoidance refused ⟦x1⟧ as a placeholder precisely because
        # the source prints it; deduping it here would delete a character the
        # author wrote
        sent = "Type ⟦x1⟧ then ⟦x1⟧ again, and press ⟦code2⟧."
        reply = "输入 ⟦x1⟧ 再 ⟦x1⟧，然后按 ⟦code2⟧。"

        assert reconcile_markers(sent, reply, ["⟦code2⟧"]) == reply

    def test_an_issued_token_repeated_keeps_only_the_first(self):
        from book_maker.loader.markers import reconcile_markers

        out = reconcile_markers("a ⟦code1⟧ b", "甲 ⟦code1⟧ 乙 ⟦code1⟧", ["⟦code1⟧"])

        assert out.count("⟦code1⟧") == 1

    def test_a_token_neither_issued_nor_in_the_source_is_scrubbed(self):
        from book_maker.loader.markers import reconcile_markers

        out = reconcile_markers("a ⟦code1⟧ b", "甲 ⟦code1⟧ ⟦img9⟧ 乙", ["⟦code1⟧"])

        assert "⟦img9⟧" not in out
        assert "⟦code1⟧" in out

    def test_a_lost_issued_token_is_still_appended(self):
        from book_maker.loader.markers import reconcile_markers

        out = reconcile_markers("a ⟦code1⟧ b ⟦x1⟧", "甲乙 ⟦x1⟧", ["⟦code1⟧"])

        assert out.endswith("⟦code1⟧")
        assert out.count("⟦x1⟧") == 1  # the literal one was not touched

    def test_the_report_does_not_call_a_literal_token_invented(self):
        from book_maker.loader.markers import marker_report

        assert (
            marker_report("f#1", "a ⟦x1⟧ ⟦code1⟧", "甲 ⟦x1⟧ ⟦code1⟧", ["⟦code1⟧"])
            is None
        )

    def test_two_argument_calls_still_read_the_shape(self):
        from book_maker.loader.markers import reconcile_markers

        # the acceptance file calls it this way; without an issued set there
        # is nothing to go on but the shape
        assert reconcile_markers("a ⟦code1⟧", "甲 ⟦code1⟧ ⟦img9⟧") == "甲 ⟦code1⟧ "


# ------------------------------------ tag mode has no ladder to fall to


class TestTagModeBatchMismatch:
    def _model(self):
        class Model:
            TRANSLATION_ERROR_MARKER = None

            def __init__(self):
                self.singles = []

            def translate_list(self, texts):
                raise BatchMismatch("two in, one out")

            def translate(self, text, needprint=True):
                self.singles.append(text)
                return f"T[{text}]"

        return Model()

    def test_deal_old_falls_back_to_singles(self):
        from book_maker.loader.helper import EPUBBookLoaderHelper

        model = self._model()
        helper = EPUBBookLoaderHelper(model, "", False, "en")
        soup = bs("<body><p>one</p><p>two</p></body>", "html.parser")
        wait = soup.find_all("p")

        helper.deal_old(list(wait))  # must not raise

        assert model.singles == ["one", "two"]
        assert [p.next_sibling.get_text() for p in wait] == ["T[one]", "T[two]"]

    def test_the_helper_is_what_the_accumulated_path_calls(self):
        from book_maker.loader.helper import translate_list_or_singles

        model = self._model()
        assert translate_list_or_singles(model, ["a", "b"]) == ["T[a]", "T[b]"]


# ------------------------ the marker contract rides in the user message


class TestMarkerInstructionPlacement:
    def _wire(self, translator):
        sent = []

        def create(**call):
            sent.append(call)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="译文", refusal=None)
                    )
                ],
                usage=None,
            )

        translator.openai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create, parse=None))
        )
        return sent

    def test_the_system_message_never_moves_between_marker_and_plain(self):
        """A system message that flips costs session mode its cached prefix:
        every later request re-reads the whole history at full input price."""
        t = _session_openai(verdict="unsupported")
        sent = self._wire(t)

        t.get_translation("Press ⟦code1⟧ now.")
        t.get_translation("Plain sentence.")
        t.get_translation("And ⟦sup2⟧ again.")

        systems = [c["messages"][0]["content"] for c in sent]
        assert len(systems) == 3
        assert systems[0] == systems[1] == systems[2]

    def test_a_marker_request_still_states_the_contract(self):
        t = _session_openai(verdict="unsupported")
        sent = self._wire(t)

        t.get_translation("Press ⟦code1⟧ now.")
        t.get_translation("Plain sentence.")

        assert "⟦code1⟧" in sent[0]["messages"][-1]["content"]
        assert "never invent one" in sent[0]["messages"][-1]["content"]
        # and says nothing about markers to a request that carries none
        assert "never invent one" not in sent[1]["messages"][-1]["content"]

    def test_the_recorded_history_matches_what_was_sent(self):
        t = _session_openai(verdict="unsupported")
        sent = self._wire(t)

        t.get_translation("Press ⟦code1⟧ now.")

        assert t.session.messages()[0]["content"] == sent[0]["messages"][-1]["content"]


# ------------------------------- --source_lang reaches every route


class TestSourceLanguageReachesTheRoutes:
    def test_openai_puts_it_in_the_system_message(self):
        t = ChatGPTAPI(key="k", language="Chinese")
        t.source_language = "english"
        content = t.create_messages("hello")[0]["content"]
        assert "Translate from english" in content

    def test_gemini_puts_it_in_the_system_instruction(self):
        from book_maker.translator.gemini_translator import Gemini

        g = Gemini.__new__(Gemini)
        g.language = "Chinese"
        g.prompt_sys_msg = "be terse"
        g.source_language = "english"

        assert (
            "Translate from english" in g._build_config_kwargs()["system_instruction"]
        )

    def test_codex_puts_it_in_the_thread_instructions(self):
        from book_maker.translator.codex_translator import Codex

        codex = Codex(key="", language="Chinese", server=SimpleNamespace())
        codex.source_language = "english"

        assert "Translate from english" in codex._instructions()


# ------------------------------------------------------ the schema bump


def test_partition_change_carries_a_schema_bump():
    from book_maker.loader.ledger import PLAN_SCHEMA_VERSION

    # 5 was the pre-marker partition: a short excluded inline node split its
    # owner's run. Anything that changes which units a book yields has to
    # move this, or a saved plan names rows the book no longer has.
    assert PLAN_SCHEMA_VERSION >= 6
