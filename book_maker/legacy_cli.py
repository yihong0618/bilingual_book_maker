"""Rewrite old command lines into the endpoint surface.

This fork selects a translator by endpoint (`--api_base`, `--key`,
`--api_format`, `--model`) rather than by model name. Every flag that
change removed is still accepted here and rewritten before argparse runs, so
commands, scripts and CI written against the old CLI keep working.

Two rules keep the layer honest:

* **Say what happened.** Each rewrite prints the modern equivalent, so a user
  who reads the output once can update their command and stop depending on
  this module.
* **Never guess.** An alias with no faithful translation exits with an
  error naming it. Quietly mapping an unknown model name onto some default
  would bill a whole book to a model nobody chose.

Model ids come from the *old* preset lists, not from whatever is newest: the
job is to preserve what the command used to do. Some of those models are
retired by now, and the endpoint's own model check reports that clearly.
"""

import os
from dataclasses import dataclass, field

# Legacy `--model` value -> the head of its old preset list. Only aliases
# whose model is still served are kept; `gpt4`, `gpt5mini`, `o1`, `o1mini`
# and `o1preview` named retired models, and now travel verbatim to the
# endpoint's own model check like any unknown value.
_OPENAI_PRESETS = {
    "gpt4omini": "gpt-4o-mini",
    "gpt4o": "gpt-4o",
    "o3mini": "o3-mini",
}

# Aliases that named the OpenAI route rather than a model. They carry no
# model at all now: the openai format has its own default, and injecting a
# retired preset here would override it.
_DEFAULT_MODEL_ALIASES = ("openai", "chatgptapi")

# `--model` aliases that named a route rather than a model. Each is an
# --api_format now: the format carries the endpoint's address, so only the
# model the alias used to default to has to travel with it. `groq` names
# none because every id in its old preset list is retired, and inventing a
# replacement would bill a book to a model nobody chose.
_NATIVE_FORMATS = {
    "gemini": ("gemini", "gemini-flash-latest", "BBM_GOOGLE_GEMINI_KEY"),
    "geminipro": ("gemini", "gemini-pro-latest", "BBM_GOOGLE_GEMINI_KEY"),
    "qwen": ("qwen", "qwen-mt-turbo", "BBM_QWEN_API_KEY"),
    "qwen-mt-turbo": ("qwen", "qwen-mt-turbo", "BBM_QWEN_API_KEY"),
    "qwen-mt-plus": ("qwen", "qwen-mt-plus", "BBM_QWEN_API_KEY"),
    "groq": ("groq", None, "BBM_GROQ_API_KEY"),
    "xai": ("xai", "grok-4.3", "BBM_XAI_API_KEY"),
    # A local sidecar, not an endpoint: no address, no key, and the sidecar
    # picks its own model, so the format alone is the whole route. `--model
    # codex` was the old spelling; `--api_format codex` is the modern one and
    # naming a model still rides on it as `--model <id>`.
    "codex": ("codex", None, None),
}

# Two of those aliases are also real model ids that a live endpoint serves.
# With an explicit --api_format, that is what they are: `--api_format qwen
# --model qwen-mt-plus` is a modern command, and so is the same id against a
# gateway on the openai format. Nothing to rewrite, nothing to apologise for.
_ALIAS_IS_ALSO_A_MODEL_ID = ("qwen-mt-turbo", "qwen-mt-plus")

# Aliases that were always a fixed engine rather than a model.
_MT_FORMATS = {
    "google": "google",
    "caiyun": "caiyun",
    "deepl": "deepl",
    "deeplfree": "deeplfree",
    "tencentransmart": "tencent",
    "customapi": "customapi",
}

# Old per-vendor key flags. All of them are just a key now, but which one to
# take matters: a command carrying two keys must send each vendor its own.
# `--api_key` is deliberately absent: it is a second spelling of `--key` on
# the parser itself, not a legacy flag to rewrite and apologise for.
_KEY_FLAGS = (
    "--openai_key",
    "--claude_key",
    "--gemini_key",
    "--groq_key",
    "--xai_key",
    "--qwen_key",
    "--caiyun_key",
    "--deepl_key",
    "--orcarouter_key",
)

# `--model` alias -> the key flag that alias used to read.
_ALIAS_KEY_FLAG = {
    "gemini": "--gemini_key",
    "geminipro": "--gemini_key",
    "groq": "--groq_key",
    "xai": "--xai_key",
    "qwen": "--qwen_key",
    "qwen-mt-turbo": "--qwen_key",
    "qwen-mt-plus": "--qwen_key",
    "caiyun": "--caiyun_key",
    "deepl": "--deepl_key",
    # a live route, not a legacy alias; only its key flag is old
    "orcarouter": "--orcarouter_key",
}


@dataclass
class LegacyTranslation:
    """A rewritten command line, plus what to tell the user about it."""

    argv: list
    notices: list = field(default_factory=list)
    # Key variables the old route used, consulted after the modern ones so an
    # existing BBM_GROQ_API_KEY still authenticates a translated groq command.
    env_keys: tuple = ()


def _fail(message):
    raise SystemExit(message)


def _value_of(argv, flag):
    """Value of `flag` in argv, in either the spaced or `=` form."""
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return None


def _drop_flag(argv, flag):
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == flag:
            i += 2
            continue
        if argv[i].startswith(f"{flag}="):
            i += 1
            continue
        out.append(argv[i])
        i += 1
    return out


def _split(argv):
    """Pull the removed flags out of argv, leaving everything else in order.

    Returns (remaining argv, {flag: value}). A flag with no value is left in
    place so argparse produces its own error rather than this module
    inventing one.
    """
    taken = {}
    rest = []
    i = 0
    aliases = {"-m": "--model"}
    known = set(_KEY_FLAGS) | {
        "--model",
        "-m",
        "--ollama_model",
        "--custom_api",
        "--deployment_id",
    }
    while i < len(argv):
        arg = argv[i]
        name, _, inline = arg.partition("=")
        if name not in known:
            rest.append(arg)
            i += 1
            continue
        if inline:
            value = inline
        elif i + 1 < len(argv):
            value = argv[i + 1]
            i += 1
        else:
            _fail(
                f"{name} needs a value. It is also a flag this fork replaced; "
                f'see "Migrating from the old flags" in the README.'
            )
        taken[aliases.get(name, name)] = value
        i += 1
    return rest, taken


def translate_legacy_argv(argv):
    """Rewrite `argv`, returning the modern command line and what changed."""
    rest, legacy = _split(list(argv))
    if not legacy:
        return LegacyTranslation(argv=list(argv))

    notices = []
    env_keys = []
    # What the user already said in modern flags always wins.
    has_base = any(a == "--api_base" or a.startswith("--api_base=") for a in rest)
    has_models = any(a == "--model_list" or a.startswith("--model_list=") for a in rest)
    given_format = _value_of(rest, "--api_format")
    has_format = given_format is not None
    api_format = None
    api_base = None
    model = None
    # A --model value that is not an alias: returned untouched, never
    # weighed against the user's other flags here.
    passthrough_model = None
    # What the user wrote that caused a route rewrite, named in the notice.
    route_source = None

    # Which key flag to honor depends on the route, so pick it after the
    # alias is known. Preferring position would hand vendor A's key to B.
    alias = legacy.get("--model", "")
    preferred = _ALIAS_KEY_FLAG.get(alias.lower())
    if alias.lower().startswith("claude"):
        preferred = "--claude_key"
    key_flag = next((f for f in (preferred,) + _KEY_FLAGS if f and f in legacy), None)
    if key_flag:
        rest = ["--key", legacy[key_flag]] + rest
        notices.append(f"{key_flag} is now --key")

    if "--model" in legacy:
        alias = legacy["--model"]
        # Matched case-insensitively: every branch below is a route name, not
        # a model id, and `--model Codex` / `--model Gemini` named the route
        # before this module owned the spelling. `alias` keeps what the user
        # typed, for the notice and the passthrough.
        key = alias.lower()
        if key in _OPENAI_PRESETS:
            model = _OPENAI_PRESETS[key]
            route_source = f"--model {alias}"
        elif key in _DEFAULT_MODEL_ALIASES:
            notices.append(
                f"--model {alias} named the OpenAI route, which is now the "
                f"default; the format's own default model runs unless "
                f"--model names one"
            )
            route_source = None
        elif key == "claude":
            # the bare alias was a stand-in for a default model
            model = "claude-haiku-4-5-20251001"
            route_source = "--model claude"
        elif key in _ALIAS_IS_ALSO_A_MODEL_ID and has_format:
            passthrough_model = alias
        elif key in _NATIVE_FORMATS:
            fmt, alias_model, env_key = _NATIVE_FORMATS[key]
            if has_format and given_format != fmt:
                alias_key = _ALIAS_KEY_FLAG.get(key)
                if alias_key is None or key_flag != alias_key:
                    # Nothing says the old route was meant: an endpoint may
                    # legitimately serve a model called `groq` or `gemini`
                    # (a LiteLLM config names its backends whatever it
                    # likes), and the format and any base were given
                    # explicitly. Hand the id back untouched.
                    passthrough_model = alias
                    route_source = None
                else:
                    # The alias's own key flag is here, so the old route was
                    # meant — and there is no faithful reading: honouring
                    # the format would send that vendor's key to a host it
                    # does not belong to, and honouring the alias would
                    # ignore what the user typed.
                    _fail(
                        f"--model {alias} with {_ALIAS_KEY_FLAG[key]} "
                        f"selects the {fmt} route, but --api_format "
                        f"{given_format} selects another one. Pass one of "
                        f"them: --api_format {fmt} for that route, or drop "
                        f"--model {alias} and name the model id the "
                        f"{given_format} endpoint uses."
                    )
            else:
                api_format, model = fmt, alias_model
                env_keys.append(env_key)
                route_source = f"--model {alias}"
        elif key in _MT_FORMATS:
            api_format = _MT_FORMATS[key]
            route_source = f"--model {alias}"
            if key == "customapi":
                # the endpoint used to arrive as --custom_api or its variable
                api_base = api_base or os.environ.get("BBM_CUSTOM_API", "")
        else:
            # Not an old alias: --model names an actual model id, which is
            # the normal case now. Hand it straight back so the parser sees
            # the flag the user typed — including any conflict with
            # --model_list, which is not this module's to resolve.
            #
            # `qwen-mt-turbo` and `qwen-mt-plus` land here when the command
            # already says --api_format: they are real model ids on that
            # route, so there is nothing to rewrite and nothing to apologise
            # for. Only a command that named them *instead of* a format is
            # an old command line.
            passthrough_model = alias

    if "--ollama_model" in legacy:
        api_base = api_base or "http://localhost:11434/v1"
        model = legacy["--ollama_model"]
        route_source = "--ollama_model"
        if not key_flag:
            # ollama authenticates nobody, on localhost or a LAN box alike;
            # the old CLI passed this placeholder for every ollama route.
            rest = ["--key", "ollama"] + rest

    if "--custom_api" in legacy:
        api_format = "customapi"
        api_base = legacy["--custom_api"]
        route_source = "--custom_api"

    if "--deployment_id" in legacy:
        model = legacy["--deployment_id"]
        route_source = "--deployment_id"
        # A bare Azure resource root serves nothing at /chat/completions;
        # the OpenAI shape lives under /openai/v1. Rewrite the base the user
        # gave rather than leaving a command that 404s.
        given = _value_of(rest, "--api_base")
        if given and "azure.com" in given and "/openai/v1" not in given:
            api_base = given.rstrip("/") + "/openai/v1"
            rest = _drop_flag(rest, "--api_base")
            has_base = False
            notices.append(
                "Azure now goes through its OpenAI-compatible endpoint; "
                f"--api_base became {api_base}"
            )

    if passthrough_model is not None:
        rest = ["--model", passthrough_model] + rest

    prefix = []
    superseded = []
    if api_format:
        prefix += ["--api_format", api_format] if not has_format else []
        superseded += ["--api_format"] if has_format else []
    if api_base:
        prefix += ["--api_base", api_base] if not has_base else []
        superseded += ["--api_base"] if has_base else []
    if model:
        # --model_list exists for rotation across several models; a single
        # model belongs in --model, which is what the user typed.
        flag = "--model_list" if "," in model else "--model"
        prefix += [flag, model] if not has_models else []
        superseded += ["--model_list"] if has_models else []

    # Describe what was actually applied. Announcing a default that the
    # user's own flag overrode would report a run that did not happen.
    if route_source:
        if prefix:
            note = f"{route_source} is now {' '.join(prefix)}"
            if superseded:
                note += f" (your own {', '.join(superseded)} kept)"
        else:
            note = (
                f"{route_source} is fully covered by the "
                f"{', '.join(superseded)} you passed"
            )
        notices.append(note)

    return LegacyTranslation(
        argv=prefix + rest,
        notices=notices,
        env_keys=tuple(dict.fromkeys(k for k in env_keys if k)),
    )
