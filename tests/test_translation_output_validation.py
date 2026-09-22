import json
import threading
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from book_maker.session_context import SessionHistory
from book_maker.translator.capabilities import CapabilityLedger
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    batch_field_name,
    single_field_name,
)
from book_maker.translator.output_validation import (
    TranslationContamination,
    detect_translation_contamination,
    high_confidence_issues,
)

LANGUAGE = "Chinese"
SINGLE_FIELD = single_field_name(LANGUAGE)
BATCH_FIELD = batch_field_name(LANGUAGE)
CONTAMINATED = (
    "译文开头。}]} Wait source last has opening Japanese quote and no close? "
    "It ends `"
)


def _single(text):
    return SimpleNamespace(**{SINGLE_FIELD: text})


def _batch(texts):
    rows = [
        SimpleNamespace(**{"id": index, SINGLE_FIELD: text})
        for index, text in enumerate(texts)
    ]
    return SimpleNamespace(**{BATCH_FIELD: rows})


def _completion(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


def _parsed(parsed, content=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(parsed=parsed, refusal=None, content=content)
            )
        ]
    )


def _translator(*, create=None, parse=None, session=False):
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "test-model"
    translator.model_list = None
    translator.keys = cycle(["k"])
    translator.temperature = 1.0
    translator.extra_body = {}
    translator.context_flag = session
    translator.context_list = []
    translator.context_translated_list = []
    translator.context_paragraph_limit = 10
    translator.context_mode = "session" if session else "window"
    translator.session = SessionHistory() if session else None
    translator.no_context_compact = False
    translator.context_compact_at = None
    translator.glossary = None
    translator.style_note = None
    translator.handoff_style = ""
    translator.prompt_sys_msg = ""
    translator.prompt_template = ChatGPTAPI.DEFAULT_PROMPT
    translator.language = LANGUAGE
    translator.language_field_tag = None
    translator.source_language = "Japanese"
    translator._api_lock = threading.Lock()
    translator.capabilities = CapabilityLedger()
    translator._rung_refusals = {}
    translator._model_names = ()
    translator._route_state = {"pending": None, "failure": None}
    translator.content_validation_events = []
    translator.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=create or Mock(return_value=_completion("plain")),
                parse=parse or Mock(return_value=_parsed(_single("clean"))),
            )
        )
    )
    return translator


def test_cross_paragraph_japanese_quote_is_not_an_error():
    sources = ["「これは第一段落です。", "第二段落まで続きます」"]
    outputs = ["“这是第一段。", "一直延续到第二段。”"]

    assert not high_confidence_issues(sources[0], outputs[0])
    assert not high_confidence_issues(sources[1], outputs[1])


def test_known_json_tail_and_wait_source_residue_is_rejected():
    issues = detect_translation_contamination("「原文", CONTAMINATED)

    assert {issue.rule for issue in issues if issue.confidence == "high"} >= {
        "model-source-self-check",
        "json-tail-followed-by-self-check",
    }


def test_code_fence_requires_explicit_process_language_to_fail():
    output = (
        '```json\n{"id": 7, "translation": "译文"}\n```\n'
        "Analysis: I need to check the translation field."
    )

    assert "code-fence-with-analysis-schema" in {
        issue.rule for issue in high_confidence_issues("原文", output)
    }


@pytest.mark.parametrize(
    "output",
    [
        "Wait, source the parts from London before dawn.",
        'He said, "I need to translate this letter for Maria."',
        "The book is titled Source Code (2011).",
        '```json\n{"id": 7, "translation": "legal example"}\n```',
        '```json\n{"schema": 1, "translation": "legal example"}\n```',
        "Analysis: A Novel, by John Doe",
    ],
)
def test_legal_english_and_code_do_not_raise_high_confidence(output):
    assert not high_confidence_issues("合法原文", output)


def test_structured_batch_retranslates_only_the_contaminated_id():
    parse = Mock(
        side_effect=[
            _parsed(_batch(["第一段译文", CONTAMINATED])),
            _parsed(_single("第二段干净译文")),
        ]
    )
    translator = _translator(parse=parse)
    translator.capabilities.verdicts["test-model"] = "strict"

    result = translator._do_structured_batch_translate(["「第一段。", "第二段结束」"])

    assert result == ["第一段译文", "第二段干净译文"]
    assert parse.call_count == 2
    assert translator.content_validation_events[-1]["status"] == "retranslated"


def test_single_translation_is_validated_before_session_context():
    parse = Mock(
        side_effect=[
            _parsed(_single(CONTAMINATED)),
            _parsed(_single("干净单段译文")),
        ]
    )
    translator = _translator(parse=parse, session=True)
    translator.capabilities.verdicts["test-model"] = "strict"

    result = translator.get_translation("「跨段引语开始。")

    assert result == "干净单段译文"
    history = json.dumps(translator.session.messages(), ensure_ascii=False)
    assert CONTAMINATED not in history
    assert "干净单段译文" in history
    assert parse.call_count == 2


def test_contaminated_batch_never_enters_session_or_glossary_context():
    raw = json.dumps(
        {
            BATCH_FIELD: [
                {"id": 0, SINGLE_FIELD: "干净"},
                {"id": 1, SINGLE_FIELD: CONTAMINATED},
            ]
        }
    )
    parse = Mock(
        side_effect=[
            _parsed(_batch(["第一段译文", CONTAMINATED]), content=raw),
            _parsed(_single("第二段干净译文")),
        ]
    )
    translator = _translator(parse=parse, session=True)
    translator.capabilities.verdicts["test-model"] = "strict"

    translator._do_structured_batch_translate(["第一段", "第二段"])

    history = json.dumps(translator.session.messages(), ensure_ascii=False)
    assert "Wait source" not in history
    assert "第二段干净译文" in history


def test_two_failed_corrections_stop_without_saving_context():
    parse = Mock(
        side_effect=[
            _parsed(_batch(["第一段译文", CONTAMINATED])),
            _parsed(_single(CONTAMINATED)),
            _parsed(_single(CONTAMINATED)),
        ]
    )
    translator = _translator(parse=parse, session=True)
    translator.capabilities.verdicts["test-model"] = "strict"

    with pytest.raises(TranslationContamination, match="after 2 correction"):
        translator._do_structured_batch_translate(["第一段", "第二段"])

    assert parse.call_count == 3
    assert translator.session.messages() == []


def test_delimiter_fallback_repairs_only_the_bad_extracted_item():
    from book_maker.translator.base_translator import BATCH_DELIMITER

    create = Mock(
        side_effect=[
            _completion(f"第一段译文{BATCH_DELIMITER}{CONTAMINATED}"),
            _completion("第二段干净译文"),
        ]
    )
    translator = _translator(create=create)
    translator.capabilities.verdicts["test-model"] = False

    assert translator.translate_list(["第一段", "第二段"]) == [
        "第一段译文",
        "第二段干净译文",
    ]
    assert create.call_count == 2


def test_loader_adds_member_and_anchor_when_corrections_fail():
    from book_maker.loader.epub_loader import EPUBBookLoader

    error = TranslationContamination(
        {0: high_confidence_issues("原文", CONTAMINATED)}, attempts=2
    )
    translator = SimpleNamespace(
        translate=Mock(side_effect=error),
        _fatal_error_detected=False,
    )
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = translator
    element = BeautifulSoup(
        '<p><span id="kobo.1188.1">原文</span></p>', "html.parser"
    ).p
    unit = SimpleNamespace(file_name="EPUB/xhtml/p-023.xhtml", element=element)

    with pytest.raises(TranslationContamination) as stopped:
        loader._translate_texts_aligned(["原文"], translator, [unit])

    assert "EPUB/xhtml/p-023.xhtml#kobo.1188.1" in str(stopped.value)
