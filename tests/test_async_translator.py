import asyncio
import threading
from itertools import cycle
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import BadRequestError, RateLimitError

from book_maker.translator.base_translator import (
    AsyncTranslationUnsupported,
    Base,
    TranslationContext,
    TranslationResult,
)
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.groq_translator import GroqClient


class SyncOnlyTranslator(Base):
    def rotate_key(self):
        pass

    def translate(self, text):
        return f"translated:{text}"


class AsyncEchoTranslator(SyncOnlyTranslator):
    async def translate_async(self, text, *, context=None):
        context = context or TranslationContext()
        translated = f"translated:{text}"
        return TranslationResult(
            text=translated, context=context.append(text, translated, 2)
        )


class FakeAsyncClient:
    def __init__(self, translated):
        message = SimpleNamespace(content=translated)
        completion = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=AsyncMock(return_value=completion))
        )
        self.closed = False

    async def close(self):
        self.closed = True


def _chatgpt_for_async_test():
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.keys = cycle(["key"])
    translator.model = "test-model"
    translator.model_list = None
    translator.temperature = 1.0
    translator.extra_body = {}
    translator.context_flag = True
    translator.context_list = ["legacy source"]
    translator.context_translated_list = ["legacy translation"]
    translator.context_paragraph_limit = 3
    translator.system_content = ""
    translator.prompt_sys_msg = ""
    translator.prompt_template = ChatGPTAPI.DEFAULT_PROMPT
    translator.language = "Chinese"
    translator.deployment_id = None
    translator.api_base = None
    translator._api_lock = threading.Lock()
    translator._structured_lock = threading.RLock()
    translator._temperature_unsupported = {}
    translator._async_clients = {}
    return translator


def _bad_request(message):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError(message, response=response, body=None)


def _rate_limit(message="rate limited"):
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError(message, response=response, body=None)


def test_translation_context_is_immutable_and_limited():
    context = TranslationContext().append("one", "一", 2)
    updated = context.append("two", "二", 2).append("three", "三", 2)

    assert context.source_texts == ("one",)
    assert updated.source_texts == ("two", "three")
    assert updated.translated_texts == ("二", "三")


def test_translation_context_rejects_mismatched_history():
    with pytest.raises(ValueError, match="lengths must match"):
        TranslationContext(("one",), ())


def test_sync_only_provider_rejects_async_translation():
    translator = SyncOnlyTranslator("key", "Chinese")

    with pytest.raises(AsyncTranslationUnsupported):
        asyncio.run(translator.translate_async("text"))


def test_chatgpt_subclass_with_custom_transport_rejects_native_async():
    translator = GroqClient.__new__(GroqClient)

    with pytest.raises(AsyncTranslationUnsupported):
        asyncio.run(translator.translate_async("text"))


def test_async_list_threads_explicit_context_sequentially():
    translator = AsyncEchoTranslator("key", "Chinese")

    result = asyncio.run(translator.translate_list_async(["one", "two", "three"]))

    assert result.texts == (
        "translated:one",
        "translated:two",
        "translated:three",
    )
    assert result.context.source_texts == ("two", "three")


def test_chatgpt_context_messages_can_use_explicit_history_without_mutation():
    translator = ChatGPTAPI.__new__(ChatGPTAPI)
    translator.context_flag = True
    translator.context_list = ["legacy source"]
    translator.context_translated_list = ["legacy translation"]
    context = TranslationContext(("chapter source",), ("chapter translation",))

    messages = translator.create_context_messages(context)

    assert messages == [
        {"role": "user", "content": "chapter source"},
        {"role": "assistant", "content": "chapter translation"},
    ]
    assert translator.context_list == ["legacy source"]
    assert translator.context_translated_list == ["legacy translation"]


def test_chatgpt_native_async_translation_keeps_context_explicit(monkeypatch):
    translator = _chatgpt_for_async_test()
    client = FakeAsyncClient("当前译文")
    monkeypatch.setattr(translator, "_create_async_client", lambda key: client)
    context = TranslationContext(("chapter source",), ("chapter translation",))

    result = asyncio.run(translator.translate_async("current", context=context))

    assert result.text == "当前译文"
    assert result.context.source_texts == ("chapter source", "current")
    assert context.source_texts == ("chapter source",)
    assert translator.context_list == ["legacy source"]
    assert client.closed is False


def test_azure_async_client_uses_existing_deployment_configuration(monkeypatch):
    translator = _chatgpt_for_async_test()
    translator.deployment_id = "deployment"
    translator.api_base = "https://example.openai.azure.com"
    client = object()
    factory = Mock(return_value=client)
    monkeypatch.setattr(
        "book_maker.translator.chatgptapi_translator.AsyncAzureOpenAI", factory
    )

    assert translator._create_async_client("azure-key") is client
    factory.assert_called_once_with(
        api_key="azure-key",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2023-07-01-preview",
        azure_deployment="deployment",
    )


def test_chatgpt_async_retries_without_rejected_temperature(monkeypatch):
    translator = _chatgpt_for_async_test()
    translator.temperature = 0.2
    client = FakeAsyncClient("译文")
    client.chat.completions.create.side_effect = [
        _bad_request("Unsupported parameter: temperature"),
        client.chat.completions.create.return_value,
    ]
    monkeypatch.setattr(translator, "_create_async_client", lambda key: client)

    result = asyncio.run(translator.translate_async("source"))

    assert result.text == "译文"
    assert client.chat.completions.create.await_count == 2
    first, second = client.chat.completions.create.await_args_list
    assert first.kwargs["temperature"] == 0.2
    assert "temperature" not in second.kwargs
    assert translator._temperature_unsupported["test-model"] is True


def test_chatgpt_async_does_not_retry_unrelated_bad_request(monkeypatch):
    translator = _chatgpt_for_async_test()
    client = FakeAsyncClient("译文")
    client.chat.completions.create.side_effect = _bad_request("invalid messages")
    monkeypatch.setattr(translator, "_create_async_client", lambda key: client)

    with pytest.raises(BadRequestError, match="invalid messages"):
        asyncio.run(translator.translate_async("source"))

    assert client.chat.completions.create.await_count == 1


def test_chatgpt_async_retries_with_the_next_key_and_model(monkeypatch):
    translator = _chatgpt_for_async_test()
    translator.keys = cycle(["key-one", "key-two"])
    translator.model_list = cycle(["model-one", "model-two"])
    first_client = FakeAsyncClient("unused")
    first_client.chat.completions.create.side_effect = _rate_limit()
    second_client = FakeAsyncClient("translated")
    clients = {"key-one": first_client, "key-two": second_client}
    monkeypatch.setattr(translator, "_create_async_client", clients.__getitem__)

    async def translate_without_waiting():
        call = translator.translate_async.retry_with(wait=lambda retry_state: 0)
        return await call(translator, "source")

    result = asyncio.run(translate_without_waiting())

    assert result.text == "translated"
    assert first_client.chat.completions.create.call_args.kwargs["model"] == "model-one"
    assert (
        second_client.chat.completions.create.call_args.kwargs["model"] == "model-two"
    )


def test_chatgpt_async_reuses_and_closes_cached_client(monkeypatch):
    translator = _chatgpt_for_async_test()
    client = FakeAsyncClient("translated")
    factory = Mock(return_value=client)
    monkeypatch.setattr(translator, "_create_async_client", factory)

    async def translate_and_close():
        first = await translator.translate_async("one")
        second = await translator.translate_async("two", context=first.context)
        await translator.close_async()
        return second

    result = asyncio.run(translate_and_close())

    assert result.text == "translated"
    factory.assert_called_once_with("key")
    assert client.chat.completions.create.await_count == 2
    assert client.closed is True
    assert translator._async_clients == {}
