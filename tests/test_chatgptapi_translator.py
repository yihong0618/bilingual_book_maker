from types import SimpleNamespace
from unittest.mock import Mock

from book_maker.translator.chatgptapi_translator import ChatGPTAPI


def _completion(content):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _translator(create):
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.model = "test-model"
    translator._use_structured_outputs = None
    translator.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        )
    )
    return translator


def test_structured_output_probe_uses_only_capability_parameters():
    create = Mock(return_value=_completion('{"translated":"test"}'))
    translator = _translator(create)

    translator._test_structured_outputs()

    assert translator._use_structured_outputs is True
    request = create.call_args.kwargs
    assert "temperature" not in request
    assert request["response_format"]["type"] == "json_schema"


def test_structured_output_probe_falls_back_when_schema_is_rejected():
    translator = _translator(Mock(side_effect=Exception("unsupported response_format")))

    translator._test_structured_outputs()

    assert translator._use_structured_outputs is False


def test_model_validation_probe_uses_model_defaults():
    create = Mock(return_value=_completion("ok"))
    translator = _translator(create)

    translator._validate_model_with_test("test-model", "Test")

    request = create.call_args.kwargs
    assert request["model"] == "test-model"
    assert request["max_tokens"] == 10
    assert "temperature" not in request
