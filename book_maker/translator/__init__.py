from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude
from book_maker.translator.codex_translator import Codex
from book_maker.translator.custom_api_translator import CustomAPI
from book_maker.translator.deepl_translator import DeepL
from book_maker.translator.deepl_free_translator import DeepLFree
from book_maker.translator.gemini_translator import Gemini
from book_maker.translator.google_translator import Google
from book_maker.translator.apiroute_translator import ApiRouteTranslator
from book_maker.translator.groq_translator import GroqClient
from book_maker.translator.litellm_translator import liteLLM
from book_maker.translator.orcarouter_translator import OrcaRouterTranslator
from book_maker.translator.qwen_translator import QwenTranslator
from book_maker.translator.tencent_transmart_translator import TencentTranSmart
from book_maker.translator.xai_translator import XAIClient

# A translator is chosen by the *wire format* its endpoint speaks, never by a
# model name. `openai` and `anthropic` reach any host serving those shapes —
# vendor, gateway, or something on localhost — and the model is whatever
# --model names. The rest are fixed-endpoint machine-translation
# services that speak only their own protocol and take no model at all.
FORMAT_DICT = {
    "openai": ChatGPTAPI,
    "anthropic": Claude,
    # Not a wire format but a local sidecar: `codex app-server` drives the
    # user's ChatGPT subscription, so there is no endpoint or key to name.
    "codex": Codex,
    # Two vendor protocols that are not the OpenAI shape and are not
    # reducible to it: Gemini's own SDK (native constrained decoding, its
    # own safety settings and chat history) and Qwen-MT, whose request
    # carries a source/target language pair and terminology rather than a
    # chat prompt. Both vendors also serve an OpenAI-compatible base, which
    # `--api_format openai --api_base ...` reaches; these are the native
    # routes, and they take a --model like any other LLM format.
    "gemini": Gemini,
    "qwen": QwenTranslator,
    # Vendors that serve the OpenAI shape at their own address. Each is the
    # OpenAI translator with that address and that vendor's key variable, so
    # the route keeps the structured-output ladder, session context,
    # batching and the model check rather than a thinner copy of them.
    "groq": GroqClient,
    "xai": XAIClient,
    "litellm": liteLLM,
    "google": Google,
    "caiyun": Caiyun,
    "deepl": DeepL,
    "deeplfree": DeepLFree,
    "tencent": TencentTranSmart,
    "customapi": CustomAPI,
}

# Model names that select a route of their own rather than a format. The
# class carries the endpoint's address and its default model.
ROUTE_DICT = {
    "orcarouter": OrcaRouterTranslator,
    "apiroute": ApiRouteTranslator,
}

# Formats that talk to a model and therefore take one. `codex` is the one
# that can resolve its own default, so --model is optional there; see
# MODEL_OPTIONAL_FORMATS in cli.py.
LLM_FORMATS = (
    "openai",
    "anthropic",
    "codex",
    "gemini",
    "qwen",
    "groq",
    "xai",
    "litellm",
)

# The address a format calls when the command names none, taken from the
# translator that owns it. A format with no entry has no address of its
# own: `openai` and `anthropic` mean their vendor, and the SDK knows it.
FORMAT_DEFAULT_BASES = {
    name: cls.DEFAULT_API_BASE
    for name, cls in FORMAT_DICT.items()
    if getattr(cls, "DEFAULT_API_BASE", None)
}
