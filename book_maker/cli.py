import argparse
import json
import os
import sys
from collections import namedtuple
from os import environ as env
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from rich import print
from rich.markup import escape

from book_maker.glossary import Glossary
from book_maker.loader import BOOK_LOADER_DICT
from book_maker.legacy_cli import translate_legacy_argv
from book_maker.loader.classify import can_session_classify
from book_maker.loader.ledger import PlanLedgerError
from book_maker.loader.plan import GENERAL_GROUP_MAX_UNITS
from book_maker.provider_loader import resolve_provider
from book_maker.session_context import DEFAULT_COMPACT_BUDGET, compact_budget_notice
from book_maker.translator import (
    FORMAT_DEFAULT_BASES,
    FORMAT_DICT,
    LLM_FORMATS,
    ROUTE_DICT,
)
from book_maker.redaction import redact
from book_maker.translator.base_translator import PriceTable
from book_maker.translator.capabilities import ModelUnavailable
from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE, parse_language_spec


class LanguageChoices(list):
    """`--language` values: a language, or `TAG:NAME`.

    A list, so `--help` still prints the languages it knows; membership is
    open, because the tables cannot hold every language anyone will ask for
    and refusing what they miss is what `TAG:NAME` exists to undo. A value
    the tables do not know is not refused — it is narrated once, at the top
    of the run, by `language_guidance`.
    """

    def __contains__(self, value):
        return bool((value or "").strip())


def language_arg(value):
    """argparse `type` for `--language`: validates, returns the string.

    Only the shape is checked here, so `options.language` stays the string
    the rest of the CLI (and the rerun command a plan records) already
    treats it as. `parse_language_spec` is called again where the value is
    used; it is pure.
    """
    try:
        parse_language_spec(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError(str(err)) from err
    return value


def source_evidence(source_lang):
    """The source language a run states, or None.

    `--source_lang auto` is the default and states nothing: a note saying
    "translate from auto" is worse than no note. A code is spelled out, so
    `--source_lang en` reads "Translate from english" rather than
    "Translate from en".
    """
    text = (source_lang or "").strip()
    if not text or text.lower() == "auto":
        return None
    return LANGUAGES.get(text, text)


def language_guidance(spec):
    """The one line a free-typed, unmatched `--language` earns, or None.

    Said once, before anything is paid for: a name the tables do not know
    drives the prompt but leaves the run with no tag, so the output markup
    and `dc:language` are stamped with nothing at all. Both ways out are in
    the line, because which one is right depends on whether the language has
    a tag we know.
    """
    if spec.pinned or spec.known:
        return None
    return (
        f"[bold yellow]Note:[/bold yellow] --language {escape(spec.name)} "
        f"matched no known language tag, so nothing is stamped on the "
        f"output markup. Use the tag (--language zh-hant) or state both "
        f'(--language "zh-hant:Traditional Chinese"); the tags are '
        f"listed in docs/languages.md."
    )


# Where each format looks for a key when --key is absent. $BBM_API_KEY is the
# one this project asks for; the rest are the variables people already have
# exported for that vendor.
FORMAT_ENV_KEYS = {
    "openai": ("BBM_API_KEY", "OPENAI_API_KEY", "BBM_OPENAI_API_KEY"),
    "anthropic": ("BBM_API_KEY", "ANTHROPIC_API_KEY", "BBM_CLAUDE_API_KEY"),
    "gemini": ("BBM_API_KEY", "BBM_GOOGLE_GEMINI_KEY", "GEMINI_API_KEY"),
    "qwen": ("BBM_API_KEY", "BBM_QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    "groq": ("BBM_API_KEY", "BBM_GROQ_API_KEY", "GROQ_API_KEY"),
    "xai": ("BBM_API_KEY", "BBM_XAI_API_KEY", "XAI_API_KEY"),
    "litellm": ("BBM_API_KEY", "BBM_LITELLM_API_KEY", "LITELLM_MASTER_KEY"),
    "caiyun": ("BBM_API_KEY", "BBM_CAIYUN_API_KEY"),
    "deepl": ("BBM_API_KEY", "BBM_DEEPL_API_KEY"),
}

# Formats that will not work at all without a credential. The others are
# public endpoints (google, deeplfree, tencent) or carry their own address
# instead of a key (customapi).
FORMATS_REQUIRING_KEY = (
    "openai",
    "anthropic",
    "gemini",
    "qwen",
    "groq",
    "xai",
    # a proxy on this machine authenticates nobody, and is_local_endpoint
    # answers that before this list is consulted
    "litellm",
    "caiyun",
    "deepl",
)

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")

# The loaders that actually forward context settings into the translator. The
# others accept `context_flag` and drop it, so a session budget passed with
# them would silently do nothing.
CONTEXT_AWARE_BOOK_TYPES = ("epub", "md", "markdown")

# LLM formats that can resolve a model on their own, so --model is optional.
MODEL_OPTIONAL_FORMATS = ("codex",)

# The model a format falls back to when the command names none. The anthropic
# format asks for an id; the other three name what their own route used to
# run by default, so `--api_format gemini` alone is a working command.
DEFAULT_MODELS = {
    "openai": "gpt-5.6-luna",
    "gemini": "gemini-flash-latest",
    "qwen": "qwen-mt-turbo",
}

# One id each endpoint actually serves, for the "--model is required"
# message. Naming a model from the wrong vendor there sends the reader to
# an id that endpoint will refuse.
MODEL_EXAMPLES = {
    "anthropic": "claude-sonnet-4-6",
    "groq": "llama-3.3-70b-versatile",
    "xai": "grok-4.3",
    "litellm": "<the model_name in your proxy's config>",
}


def infer_api_format(api_base, model=""):
    """Which wire format the endpoint speaks, guessed from host then model.

    The host is the stronger signal: a gateway serves Claude models over the
    OpenAI shape too. Only without an endpoint does the model id decide, and
    there `claude` or `anthropic` in it means Anthropic. `--api_format`
    overrides both.
    """
    name = (model or "").strip().lower()
    if api_base:
        host = (urlparse(api_base).hostname or "").lower()
        official = host == "anthropic.com" or host.endswith(".anthropic.com")
        return "anthropic" if official else "openai"
    if "claude" in name or "anthropic" in name:
        return "anthropic"
    return "openai"


# Endpoint paths people paste in along with the base. The SDKs build these
# themselves, so a base carrying one produces /v1/chat/completions/chat/completions.
_ENDPOINT_SUFFIXES = ("/chat/completions", "/messages", "/completions")


def normalize_api_base(api_base, api_format):
    """Trim a pasted request path off `--api_base`.

    Copying the URL out of a provider's docs or a curl line is the common
    way to get this flag, and those URLs end at the endpoint rather than the
    base. Trailing slashes go too, so `.../v1/` and `.../v1` are one thing.

    Only for the SDK-backed formats, which build the request path themselves.
    `customapi` posts to this URL verbatim, so a path is the address, not
    noise to strip.
    """
    if not api_base or api_format not in LLM_FORMATS:
        return api_base
    base = api_base.strip().rstrip("/")
    for suffix in _ENDPOINT_SUFFIXES:
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    return base


def is_local_endpoint(api_base):
    if not api_base:
        return False
    return (urlparse(api_base).hostname or "").lower() in LOCAL_HOSTS


def resolve_api_key(api_format, explicit_key, api_base, extra_env_keys=()):
    """The key to use, or a loud failure naming where one was looked for.

    `extra_env_keys` carries the variables an old command line implies — a
    translated `--model groq` still authenticates from BBM_GROQ_API_KEY.
    They come first: they name the endpoint being called, so with both
    OPENAI_API_KEY and BBM_GROQ_API_KEY exported, a groq command must not
    hand the OpenAI key to Groq.
    """
    env_names = tuple(extra_env_keys) + FORMAT_ENV_KEYS.get(
        api_format, ("BBM_API_KEY",)
    )
    key = explicit_key or next((env[n] for n in env_names if env.get(n)), "")
    if key:
        return key

    # A server on this machine is not authenticating anyone, but the OpenAI
    # SDK refuses to construct without some string.
    if is_local_endpoint(api_base):
        return "local"

    if api_format in FORMATS_REQUIRING_KEY:
        raise SystemExit(
            f"No API key for the {api_format} endpoint. Pass --key, or set "
            f"one of: {', '.join(env_names)}."
        )
    return ""


def _entry_address(api_base, api_format):
    """The host a (base, format) pair actually calls, for comparing two of them.

    An empty base means the format's own address — which for the vendor
    formats is a real URL. `openai` and `anthropic` have none written down
    here (their SDK holds the vendor host), so the format itself stands in:
    two formats with no address are two different hosts, not one empty one,
    and an openai entry asked for the anthropic format is calling Anthropic.
    Trailing slashes are noise here: `.../v1` and `.../v1/` are one host.
    """
    base = (api_base or FORMAT_DEFAULT_BASES.get(api_format, "")).rstrip("/")
    return base or f"the {api_format} endpoint's own host"


def apply_provider(options):
    """Fill in the endpoint flags `--provider` covers, and name its key variable.

    Only what the command left out: a provider is a shorthand for flags, so
    every flag actually typed outranks it. Returns the variables to consult
    for the key, ahead of the format's conventional ones — the entry names
    the endpoint being called, so its own variable is the right one.
    """
    if not options.provider:
        return ()
    try:
        route = resolve_provider(options.provider)
    except ValueError as err:
        raise SystemExit(str(err))
    # The entry's key belongs to the entry's *address*, and travels only as
    # far as that address does. Either flag can move it: --api_base says so
    # outright, and --api_format moves an entry that has no base_url of its
    # own, because then the format is what supplies the address. Asking for
    # another wire format at the entry's own gateway moves nothing, and
    # there the key is still the right one.
    entry_address = _entry_address(route.api_base, route.api_format)
    run_address = _entry_address(
        options.api_base or route.api_base, options.api_format or route.api_format
    )
    entry_endpoint_kept = run_address == entry_address
    if not entry_endpoint_kept:
        print(
            f"[bold yellow]Warning:[/bold yellow] provider "
            f"{options.provider} names {entry_address}, but this run calls "
            f"{run_address}; its key variable is not read for another "
            f"endpoint."
        )
    options.api_format = options.api_format or route.api_format
    options.api_base = options.api_base or route.api_base
    # Prices are the entry's to know and the meter's to apply; they ride
    # on the options until the translator exists.
    options.price_table = (
        PriceTable(route.prices, route.currency) if route.prices else None
    )
    # The codex sidecar is not the entry's HTTP endpoint: it ignores the
    # entry's base and key and resolves its own default model, so a provider's
    # model list is not its to take. On that route a model comes only from an
    # explicit --model/--model_list.
    if (
        route.models
        and not options.model
        and not options.model_list
        and options.api_format != "codex"
    ):
        # One model belongs in --model; several rotate, first one first.
        if len(route.models) == 1:
            options.model = route.models[0]
        else:
            options.model_list = ",".join(route.models)
    if not entry_endpoint_kept:
        return ()
    return (route.env_key,) if route.env_key else ()


def named_models(options):
    """The models the command names, in the order it named them."""
    return [
        name.strip()
        for name in (
            options.model_list.split(",")
            if options.model_list
            else [options.model or ""]
        )
        if name.strip()
    ]


def resolve_endpoint(options):
    """`(model names, api format, key variables)` for this command.

    `options.api_base` is left holding the address the run will use.

    Order matters: `--provider` is shorthand for flags the command left out,
    so it is applied only after everything the command itself said — a route
    the model name selected included.
    """
    # A model may be named once, in either flag. Accepting both would leave
    # two answers to "which model is this run using".
    if options.model and options.model_list:
        raise SystemExit(
            "Name the model once: --model for a single model, --model_list "
            "only to rotate across several."
        )
    model_names = named_models(options)

    # A model name that selects a route says where the request goes, so a
    # provider entry must not capture it. `--model orcarouter` is upstream's
    # OrcaRouter route: its class carries the gateway's address and its
    # smart-routing model, and the key comes from BBM_ORCAROUTER_API_KEY.
    if len(model_names) == 1 and model_names[0].lower() in ROUTE_DICT:
        options.api_base = normalize_api_base(options.api_base, "openai")
        return [model_names[0].lower()], "openai", ("BBM_ORCAROUTER_API_KEY",)
    # `--model codex` is rewritten to `--api_format codex` by the legacy shim;
    # `--model_list` is not, so a bare `codex` here came from --model_list and
    # names the route, not a model to rotate to. Say what to type instead of
    # sending `codex` on as a model id the endpoint will refuse.
    listed = [n.strip() for n in (options.model_list or "").split(",") if n.strip()]
    if len(listed) == 1 and listed[0].lower() == "codex":
        raise SystemExit(
            "--model_list codex names the codex route, not a model. Use "
            "--api_format codex instead, and --model only to name a model on it."
        )

    provider_env_keys = apply_provider(options)
    if not model_names:
        # the entry names the models when the command named none
        model_names = named_models(options)

    api_format = options.api_format or infer_api_format(
        options.api_base, model_names[0] if model_names else ""
    )
    # A format that stands for one vendor's endpoint carries its address, so
    # the format alone is a complete route. Filled in here rather than left
    # to the translator, so everything downstream — the local-endpoint check
    # that skips the key, and the record the output file keeps — sees the
    # address the run will actually call.
    options.api_base = options.api_base or FORMAT_DEFAULT_BASES.get(api_format, "")
    options.api_base = normalize_api_base(options.api_base, api_format)
    if not model_names and api_format in DEFAULT_MODELS:
        model_names = [DEFAULT_MODELS[api_format]]
    return model_names, api_format, provider_env_keys


def get_book_type(book_name):
    return Path(book_name).suffix.lower().lstrip(".")


def parse_prompt_arg(prompt_arg, announce=True):
    """The `--prompt` argument as a config dict, or None.

    `announce` is off for the compatibility pass, which asks the same
    question earlier only to decide whether a row fires: the run's own
    "prompt config:" line must still be printed once, by the real call.
    """
    prompt = None
    if prompt_arg is None:
        return prompt

    # Check if it's a path to a markdown file (PromptDown format)
    if prompt_arg.endswith(".md") and os.path.exists(prompt_arg):
        try:
            from promptdown import StructuredPrompt

            structured_prompt = StructuredPrompt.from_promptdown_file(prompt_arg)

            # Initialize our prompt structure
            prompt = {}

            # Handle developer_message or system_message
            # Developer message takes precedence if both are present
            if (
                hasattr(structured_prompt, "developer_message")
                and structured_prompt.developer_message
            ):
                prompt["system"] = structured_prompt.developer_message
            elif (
                hasattr(structured_prompt, "system_message")
                and structured_prompt.system_message
            ):
                prompt["system"] = structured_prompt.system_message

            # Extract user message from conversation
            if (
                hasattr(structured_prompt, "conversation")
                and structured_prompt.conversation
            ):
                for message in structured_prompt.conversation:
                    if message.role.lower() == "user":
                        prompt["user"] = message.content
                        break

            # Ensure we found a user message
            if "user" not in prompt or not prompt["user"]:
                raise ValueError(
                    "PromptDown file must contain at least one user message"
                )

            if announce:
                print(f"Successfully loaded PromptDown file: {prompt_arg}")

            # Validate required placeholders
            if any(c not in prompt["user"] for c in ["{text}"]):
                raise ValueError(
                    "User message in PromptDown must contain `{text}` placeholder"
                )

            return prompt
        except Exception as e:
            # Falling through left `prompt` half-built and the next line
            # died on `prompt["user"]` with a KeyError traceback — after
            # the run had already printed that the file loaded. The pinned
            # promptdown reads the block form only; its table form (which
            # this repo's own prompt_md.prompt.md still uses) parses to a
            # conversation with no user message.
            raise ValueError(
                f"could not read the PromptDown file {prompt_arg}: {e}. "
                f"Write the conversation in block form -- a line reading "
                f"`**User:**` followed by the template, which must contain "
                f"`{{text}}`."
            ) from e

    # Existing parsing logic for JSON strings and other formats
    if not any(prompt_arg.endswith(ext) for ext in [".json", ".txt", ".md"]):
        try:
            # user can define prompt by passing a json string
            # eg: --prompt '{"system": "You are a professional translator who translates computer technology books", "user": "Translate \`{text}\` to {language}"}'
            prompt = json.loads(prompt_arg)
        except json.JSONDecodeError:
            # if not a json string, treat it as a template string
            prompt = {"user": prompt_arg}

    elif os.path.exists(prompt_arg):
        if prompt_arg.endswith(".txt"):
            # if it's a txt file, treat it as a template string
            with open(prompt_arg, encoding="utf-8") as f:
                prompt = {"user": f.read()}
        elif prompt_arg.endswith(".json"):
            # if it's a json file, treat it as a json object
            # eg: --prompt prompt_template_sample.json
            with open(prompt_arg, encoding="utf-8") as f:
                prompt = json.load(f)
    else:
        raise FileNotFoundError(f"{prompt_arg} not found")

    # Shape first, then contents. The `{text}` check used to run first and
    # subscript `prompt["user"]` before anything had established there was a
    # `user` key, so `--prompt '{"system": "be terse"}'` — a plausible typo,
    # the section being the only one someone might think stands alone — died
    # on a KeyError traceback instead of the sentence written for it below.
    if prompt is None or not isinstance(prompt, dict):
        raise ValueError("prompt must be an object carrying a `user` key")

    if not prompt.get("user"):
        raise ValueError("prompt must contain the key of `user`")

    if (prompt.keys() - {"user", "system", "style"}) != set():
        raise ValueError(
            "prompt can only contain the keys of `user`, `system` and `style`"
        )

    # if any(c not in prompt["user"] for c in ["{text}", "{language}"]):
    if "{text}" not in prompt["user"]:
        raise ValueError("prompt must contain `{text}`")

    if announce:
        print("prompt config:", prompt)
    return prompt


# The order sections are named in, so two runs describe the same prompt the
# same way.
PROMPT_SECTIONS = ("user", "system", "style")


def prompt_adoption_line(prompt_config, translate_model, api_format):
    """One line saying which `--prompt` sections this run adopted, and where.

    Returned rather than printed so it can be tested without a run, and None
    when there is nothing to say — a run that passed no `--prompt` must print
    nothing new, which the noise guard pins.

    The "where" is the point. A section with no native slot on this route is
    not dropped: it is appended to whatever the route does send (the user
    message on the API routes, the thread instructions on codex), and a run
    that is quietly translating under a style the endpoint never saw is
    exactly what this line exists to make impossible. A route that builds no
    prompt at all can carry none of it, and says so.
    """
    adopted = [s for s in PROMPT_SECTIONS if (prompt_config or {}).get(s)]
    if not adopted:
        return None
    slots = getattr(translate_model, "PROMPT_SECTION_SLOTS", {})
    target = getattr(translate_model, "PROMPT_APPEND_TARGET", "the user message")
    appended = [s for s in adopted if slots.get(s, "native") == "appended"]
    ignored = [s for s in adopted if slots.get(s, "native") == "none"]

    notes = []
    if appended:
        notes.append(f"{' and '.join(appended)} appended to {target} on this route")
    if ignored:
        notes.append(
            f"{' and '.join(ignored)} ignored — the {api_format} route "
            f"builds no prompt to carry it"
        )
    line = f"prompt: {'+'.join(adopted)} from --prompt"
    if notes:
        line += f" ({'; '.join(notes)})"
    return line


# Below this a window cannot hold even one paragraph with its translation, so
# every unit would trigger a paid handoff report.
MIN_COMPACT_BUDGET = 500


def compact_budget(value):
    """argparse type for --context-compact-at: a budget a window can work to."""
    try:
        budget = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if budget < MIN_COMPACT_BUDGET:
        raise argparse.ArgumentTypeError(
            f"a compact budget of {budget} is too small to be useful; use at "
            f"least {MIN_COMPACT_BUDGET} estimated tokens (a window that "
            f"short is a handoff report and little else)"
        )
    return budget


def glossary_file(value):
    """argparse type for --glossary / --terminology: a file that is there.

    Checked at parse time, before an endpoint is resolved or a byte of the
    book is read: a mistyped path is the one glossary failure that costs
    nothing to catch, and catching it later would mean a whole book
    translated without the pins the operator asked for.
    """
    path = Path(value)
    if not path.is_file():
        raise argparse.ArgumentTypeError(
            f"no glossary file at {value!r}; expected a text file of "
            f"'term -> translation' lines, one per line"
        )
    return value


class GlossaryPath(argparse.Action):
    """Store the path, and remember which of the two spellings was typed.

    `--glossary` and `--terminology` are one flag under two names, so a
    warning about it has to name the one the operator actually used.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        namespace.glossary_flag = option_string or "--glossary"


def glossary_auto_flag(value):
    """`--glossary-auto {on,off}` as the tri-state the translator wants.

    None means the operator said nothing, and the run defaults it: on where
    there is a session to learn from, off where there is not.
    """
    return {"on": True, "off": False}.get(value)


def batch_unit_cap(value):
    """argparse type for --max-batch-units: units one plan request may carry."""
    try:
        units = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if units < 1:
        raise argparse.ArgumentTypeError(
            f"a batch of {units} units is not a request; use at least 1 "
            f"(--accumulated_num 1 is how grouping is turned off)"
        )
    return units


class DeprecatedAlias(argparse.Action):
    """An old spelling of a flag: same dest, plus a record that it was typed.

    argparse has no notion of a deprecated *option string*, and giving one
    `add_argument` call both spellings would advertise both in `--help`. So
    the alias is its own `SUPPRESS`ed argument writing the same dest, and
    `main` prints the notice from the spelling recorded here.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_deprecated_flag", option_string)


def accumulated_tokens(value):
    """argparse type for --accumulated_num: a token budget, 1 being "off".

    0 and negatives used to land silently as "grouping off", which is what 1
    already documents. A number below the off switch is a typo, not a
    quieter way to say the same thing.
    """
    try:
        budget = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if budget < 1:
        raise argparse.ArgumentTypeError(
            f"a token budget of {budget} is not a request; use at least 1 "
            f"(--accumulated_num 1 is how grouping is turned off)"
        )
    return budget


def poetry_group(value):
    """argparse type for --poetry-group-size: lines one request may carry."""
    try:
        lines = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if lines < 1:
        raise argparse.ArgumentTypeError(
            f"a poetry group of {lines} is not a request; use at least 1 "
            f"(1 gives every short line its own request)"
        )
    return lines


# Above this, a plan that passes the coverage gate is the exception rather
# than the rule: running heads, page numbers and apparatus are exactly what
# the plan is for skipping, and they are rarely under a tenth of a book.
COVERAGE_WARN_ABOVE = 0.9

# The two plan-shaping flags default to None on the parser so that "typed"
# and "left alone" stay distinguishable (the no-op warning needs the
# difference); these are the values a run gets when they were left alone.
PLAN_MIN_COVERAGE_DEFAULT = 0.5
POETRY_GROUP_SIZE_DEFAULT = 8


def coverage_fraction(value):
    """argparse type for --plan-min-coverage: a fraction of the book, 0-1.

    A percentage typed as `50` used to be accepted and then abort every run
    after classification had been paid for; so did `1.5`, which no plan can
    ever satisfy. Both ends warn where the value is legal but self-defeating.
    """
    try:
        fraction = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}")
    if not 0 <= fraction <= 1:
        raise argparse.ArgumentTypeError(
            f"--plan-min-coverage is a fraction of the book between 0 and 1, "
            f"got {fraction} (50% is 0.5, not 50)"
        )
    if fraction == 0:
        print(
            "[yellow]--plan-min-coverage 0: coverage guard disabled; a plan "
            "that covers almost nothing will be translated without a "
            "word.[/yellow]"
        )
    elif fraction > COVERAGE_WARN_ABOVE:
        print(
            f"[yellow]--plan-min-coverage {fraction}: plans rarely cover more "
            f"than ~90% of a book (running heads, page numbers and apparatus "
            f"are what a plan skips); this will usually abort after "
            f"classification is paid for.[/yellow]"
        )
    return fraction


def resolve_context_mode(options):
    """`(context_flag, context_mode)` from the parsed `--use_context` value.

    `--use_context` used to be a bare switch and still may be: absent means no
    context, bare means the window mode it has always meant, and only an
    explicit `session` selects cached history.
    """
    mode = getattr(options, "context_mode", None)
    return (mode is not None), mode


# The plan is built from LLM verdicts on a pinned JSON Schema, so it is only
# worth entering automatically where the schema is known to be applied. That
# is established by the capability probe, which speaks the OpenAI wire format
# and grades one endpoint; every other route (anthropic, google, deepl, codex,
# ...) has no such verdict to offer and stays in tag mode.
PLAN_AUTO_FORMAT = "openai"

# Probe verdicts a plan may be built on, and the line each one prints.
# "strict" is the endpoint applying our schema; "shape" and "json" are
# weaker, but the classifier ladders down to a described schema and the
# batch contract checks its own alignment, so both are still enough to plan
# on (260905 eval). Everything else — `unsupported`, a rejected request, no
# verdict at all — stays in tag mode: the classifier would be asking a
# prose-only channel, and entering the plan on a prompt rung is its own
# decision, not this one.
PLAN_VERDICT_REASONS = {
    "strict": "endpoint verified strict JSON schema",
    "shape": "endpoint applies JSON schema shape, values unverified",
    "json": "endpoint verified JSON object mode, no schema applied",
}

# What is printed when a route with no usable JSON verdict plans anyway,
# because it can be asked for verdicts in a conversation instead. See
# `loader/classify/session.py` for what that costs and how it is checked.
PLAN_SESSION_REASON = "no structured output here; classifying over a plain session"


def resolve_plan_mode(
    book_type, api_format, translate_tags_given, probe, session=False
):
    """What `--plan-classify auto` means for this run: `(mode, reason)`.

    `mode` is "model" (plan the book on JSON verdicts), "session" (plan it
    on verdicts read out of a plain conversation) or "none" (translate the
    `--translate-tags` selection); `reason` is the one line the run prints
    about it. `probe` is called — at most once, and only when its answer can
    still change the outcome — for the endpoint's graded schema support; None
    means this translator has no probe. `session` says whether the route can
    hold a classifier conversation, which is what makes the difference
    between "no verdict, so no plan" and "no verdict, so ask another way".
    """
    if book_type != "epub":
        return "none", f"plan mode needs an epub; this is a {book_type} book"
    if translate_tags_given:
        return "none", "--translate-tags names what to translate"
    if api_format != PLAN_AUTO_FORMAT:
        # Only the openai wire format is *entered* on a verdict. A route
        # without one used to end here; one that can hold a conversation now
        # gets asked in the way it can answer.
        if probe is not None and session:
            # An OpenAI-shaped route at another vendor's address (groq, xai,
            # litellm, a gateway): it carries the same probe, and
            # `classify_plan` asks it at run time. Saying "no JSON-schema
            # verdict" here described the wrong run — the JSON path is used
            # on these whenever the probe holds. Message only: which mode
            # this run enters is unchanged.
            return "session", (
                f"the {api_format} route's JSON-schema support is probed at "
                f"run time: the JSON path where the probe holds, a plain "
                f"session otherwise"
            )
        if session:
            return "session", (
                f"the {api_format} route has no JSON-schema verdict, so "
                f"{PLAN_SESSION_REASON}"
            )
        return "none", f"the {api_format} route has no JSON-schema verdict"
    if probe is None:
        if session:
            return "session", PLAN_SESSION_REASON
        return "none", "this endpoint offers no JSON-schema verdict"
    try:
        verdict = probe()
    except ModelUnavailable:
        raise  # no model to fall back to; the message names it
    except Exception as e:
        # A probe that *failed* is not an endpoint found bare: the trouble is
        # as likely to be the transport, which a conversation would hit too.
        # Tag mode still works; the failure surfaces at the first translation
        # request.
        return "none", f"the JSON-schema probe failed: {redact(e)}"
    reason = PLAN_VERDICT_REASONS.get(verdict)
    if reason is None:
        if session:
            return "session", PLAN_SESSION_REASON
        return "none", (
            f"the endpoint produces no JSON the schema ladder can use "
            f"({verdict or 'no schema support'})"
        )
    return "model", reason


def resolve_classify_mode(options):
    """`(classify mode, still-auto)` from the two --plan-classify flags.

    Pure: it resolves what the command asked for and refuses nothing. The
    one contradiction the CLI stops on (a classifier model named alongside a
    mode that classifies nothing) is deliberately left as it was typed, so
    the refusal that owns that message is the one the run meets.
    """
    mode = options.plan_classify
    plan_auto = mode == "auto"
    if plan_auto:
        # tag mode until the endpoint's probe settles it
        mode = "none"
    if options.plan_classify_model:
        # naming a classifier is naming the mode it belongs to
        plan_auto = False
        if mode not in ("all", "agent"):
            mode = "model"
    return mode, plan_auto


def _route_can_session_classify(translate_model):
    """Whether this route's class can hold a classifier conversation.

    The class, not an instance: the compatibility pass runs before any
    translator is built. `can_session_classify` asks the same question of
    the same attribute, so the two answers cannot drift.
    """
    from book_maker.translator.base_translator import Base

    factory = getattr(translate_model, "classify_session", None)
    return factory is not None and factory is not Base.classify_session


def plan_mode_expected(facts):
    """Whether this command will translate a plan rather than a tag selection.

    Decidable from the command alone in every case but one: `auto` on an
    OpenAI-shaped route is settled by the capability probe, and a probe that
    *fails* drops back to tag mode. Answering True there is the right guess —
    a failing probe is a broken run, not a mode.
    """
    if facts.book_type != "epub":
        return False
    if facts.classify_mode != "none":
        return True
    if not facts.plan_auto or facts.translate_tags_given:
        return False
    if facts.api_format == PLAN_AUTO_FORMAT:
        return True
    return _route_can_session_classify(facts.translate_model)


# ---------------------------------------------------------- flag compatibility
#
# One table, one pass, run after the endpoint is resolved and before any
# translator is built or any request is made. A row is
# `CompatRule(id, level, when, say)`:
#
#   id    the audit row it enforces, for the tests to name; never printed
#   level "stop" (red, exit 1) or "warn" (yellow, the run continues)
#   when  a predicate over the resolved `RunFacts` — options plus everything
#         the CLI has already worked out (book type, wire format, translator
#         class, whether a plan is coming)
#   say   the operator's sentence, built from the same facts
#
# Rows that need state only the loader has (how many signatures a plan asks
# about, how many requests a --test slice becomes) are not here: they print
# where that number exists, in the loader.
#
# Stops win outright: when any fires, only the stops are printed, because a
# warning about a run that is not going to happen is noise.

CompatRule = namedtuple("CompatRule", "id level when say")


def session_run_expected(facts):
    """Whether this run's context will be one growing history.

    `--use_context session` says so outright. The codex route is one without
    being asked — its thread *is* the history, with no windowed shape to
    fall back to — and since it is billed like a session run, both budgets a
    session run derives are derived for it too. `SESSION_CONTEXT_ALWAYS_ON`
    on the translator is the single fact behind that, in the loader
    (`_session_run`) and here, so the rows that talk about a session cannot
    disagree with the run that has one.
    """
    if facts.options.context_mode == "session":
        return True
    return bool(getattr(facts.translate_model, "SESSION_CONTEXT_ALWAYS_ON", False))


def _session_run_source(facts):
    """What to call the session in a warning: the flag, or the route."""
    if facts.options.context_mode == "session":
        return "--use_context session"
    return f"the {facts.api_format} route's one growing thread"


def prompt_has_system(prompt_arg):
    """Whether `--prompt` carries a system message of its own.

    A user-only prompt loses nothing to `$OPENAI_API_SYS_MSG`: the two fill
    different halves of the request and both are honoured, so warning about
    an outranked system message there names a conflict that is not there.

    Read from the argument rather than the config because the config is
    built after this pass — and deliberately: a malformed `--prompt` is
    refused further down, by the message that explains it, and this row must
    not pre-empt that with a traceback. Announced nowhere, so the run still
    prints its prompt config exactly once.
    """
    if prompt_arg is None:
        return False
    try:
        return bool((parse_prompt_arg(prompt_arg, announce=False) or {}).get("system"))
    except Exception:
        return False


# Loaders that read the tag-selection flags. Markdown reads the exclusions
# and nothing else; everything outside epub ignores the styling flags, the
# worker count and the context switch.
TAG_AWARE_BOOK_TYPES = ("epub",)
EXCLUDE_AWARE_BOOK_TYPES = ("epub", "md", "markdown")
PARALLEL_AWARE_BOOK_TYPES = ("epub", "md", "markdown")

# Engines that detect the source language themselves, so `--source_lang`
# reaches nothing they send.
SOURCE_BLIND_FORMATS = ("google", "deepl", "deeplfree", "caiyun", "tencent")


# Formats that read `--source_lang` off the options and put it in the request.
SOURCE_LANG_FORMATS = ("qwen", "customapi")

# The tag exclusion every run gets when the flag is untouched.
DEFAULT_EXCLUDE_TRANSLATE_TAGS = "sup,code"

# Codex's classifier thread opens with the sidecar's own instructions before
# it is asked anything (measured 260905: ~16.9k input tokens).
CODEX_CLASSIFIER_PREAMBLE_TOKENS = "~17k"


def _c7_ignored_tag_flags(f):
    """Tag-selection flags this book type will not read."""
    flags = []
    if f.book_type not in TAG_AWARE_BOOK_TYPES:
        if f.translate_tags_given:
            flags.append("--translate-tags")
        if f.options.allow_navigable_strings:
            flags.append("--allow_navigable_strings")
    if f.book_type not in EXCLUDE_AWARE_BOOK_TYPES and f.exclude_translate_tags_given:
        flags.append("--exclude-translate-tags")
    return flags


def _c8_ignored_style_flags(f):
    return [
        flag
        for flag, value in (
            ("--translation_style", f.options.translation_style),
            ("--translation_color", f.options.translation_color),
        )
        if value
    ]


def _b12_given_compact_flags(f):
    return [
        flag
        for flag, value in (
            ("--context-compact-at", f.options.context_compact_at),
            ("--no-context-compact", f.options.no_context_compact),
            ("--glossary-auto", f.options.glossary_auto),
        )
        if value
    ]


def _glossary_flag(f):
    """The spelling the operator typed: `--glossary` or `--terminology`."""
    return getattr(f.options, "glossary_flag", "--glossary")


COMPAT_RULES = (
    # ---------------------------------------------------------------- stops
    CompatRule(
        "A1",
        "stop",
        lambda f: f.book_type == "epub"
        and (f.options.batch_flag or f.options.batch_use_flag),
        lambda f: (
            "--batch / --batch-use are broken on epub: queueing lives on a "
            "path the epub loader never takes, so the run translates the "
            "whole book live at full price and then submits an empty batch "
            "job — and --batch never writes the book at all. Drop the flag, "
            "or batch a txt/srt book."
        ),
    ),
    CompatRule(
        "A4",
        "stop",
        lambda f: f.book_type == "epub"
        and f.options.parallel_workers > 1
        and (f.options.accumulated_num or 0) > 1
        and f.options.resume,
        lambda f: (
            "--parallel-workers with --accumulated_num above 1 records no "
            "progress at all, so --resume has nothing to continue and an "
            "interrupted run pays for the whole book again. Drop one of the "
            "three: --parallel-workers, --accumulated_num or --resume."
        ),
    ),
    CompatRule(
        "A7",
        "stop",
        lambda f: len(f.model_names) > 1 and f.options.context_mode == "session",
        lambda f: (
            f"--model_list rotates a different model into every request "
            f"while --use_context session keeps one growing history. Prompt "
            f"caching is per model, so every request would re-read the whole "
            f"history at full price, and one conversation would be written "
            f"by {len(f.model_names)} different models. Name one model with "
            f"--model, or drop --use_context session."
        ),
    ),
    CompatRule(
        "A10",
        "stop",
        lambda f: f.book_type == "epub"
        and f.classify_mode == "model"
        and f.api_format not in LLM_FORMATS,
        lambda f: (
            f"{f.classify_flag} asks an LLM to rule on every plan "
            f"signature, and the "
            f"{f.api_format} format translates through one fixed engine with "
            f"no model to ask. The run would parse the whole book and write "
            f"a plan file nothing had decided before failing. Use "
            f"--plan-classify agent to decide the plan yourself, "
            f"--plan-classify all to translate the whole partition, or "
            f"translate through an LLM route."
        ),
    ),
    CompatRule(
        "C9",
        "stop",
        lambda f: bool(f.options.retranslate) and f.book_type != "epub",
        lambda f: (
            f"--retranslate is implemented by the epub loader only; on a "
            f"{f.book_type} book it would be accepted and do nothing."
        ),
    ),
    # ---------------------------------------------------------------- warns
    CompatRule(
        "A8",
        "warn",
        lambda f: session_run_expected(f)
        and not f.plan_mode
        and (f.options.accumulated_num or 1) <= 1,
        lambda f: (
            f"{_session_run_source(f)} outside plan mode leaves grouping "
            f"off, so every paragraph is its own request and each one "
            f"re-reads the whole history. Raise --accumulated_num to put "
            f"several paragraphs in one request; only plan mode derives that "
            f"budget for you."
        ),
    ),
    CompatRule(
        "A9",
        "warn",
        lambda f: prompt_has_system(f.options.prompt_arg)
        and bool(f.env.get("OPENAI_API_SYS_MSG"))
        and hasattr(f.translate_model, "_probe_verdict"),
        lambda f: (
            "$OPENAI_API_SYS_MSG is exported, and it outranks the system "
            "message from --prompt for the whole run. Unset it, or drop the "
            '"system" key from --prompt.'
        ),
    ),
    CompatRule(
        "A11",
        "warn",
        lambda f: f.api_format == "codex" and f.plan_mode,
        lambda f: (
            f"the codex route classifies the plan in a thread of its own, "
            f"and codex sends {CODEX_CLASSIFIER_PREAMBLE_TOKENS} tokens of "
            f"its own preamble before the first question is asked. "
            f"--plan-classify none skips that."
        ),
    ),
    CompatRule(
        "B6",
        "warn",
        lambda f: f.plan_auto
        and f.book_type == "epub"
        and not f.translate_tags_given
        and f.api_format in LLM_FORMATS
        and f.api_format != PLAN_AUTO_FORMAT
        and not _route_can_session_classify(f.translate_model),
        lambda f: (
            f"the {f.api_format} route does not plan automatically: it "
            f"offers no JSON-schema verdict and holds no classifier "
            f"conversation, so this run translates the --translate-tags "
            f"selection. Pass --plan-classify model to plan the whole book "
            f"here — the same book comes out quite differently with it."
        ),
    ),
    CompatRule(
        "B8",
        "warn",
        lambda f: f.book_type == "epub"
        and f.options.sentence_mode
        and (f.options.accumulated_num or 0) > 1,
        lambda f: (
            "--sentence_mode is ignored while --accumulated_num is above 1: "
            "the accumulating path never reaches the sentence one."
        ),
    ),
    CompatRule(
        "B9",
        "warn",
        lambda f: f.book_type == "epub"
        and f.options.block_size > 0
        and (f.options.accumulated_num or 0) > 1,
        lambda f: (
            "--block_size is ignored while --accumulated_num is above 1: the "
            "accumulating path never reaches block batching."
        ),
    ),
    CompatRule(
        "B10",
        "warn",
        lambda f: bool(f.options.only_filelist) and bool(f.options.exclude_filelist),
        lambda f: (
            "--only_filelist already names every document to translate, so "
            "--exclude_filelist is ignored when both are given."
        ),
    ),
    CompatRule(
        "B11",
        "warn",
        lambda f: f.options.context_paragraph_limit
        and f.options.context_mode == "session",
        lambda f: (
            "--context_paragraph_limit sizes the re-sent window of bare "
            "--use_context; session mode keeps one append-only history "
            "instead and ignores it."
        ),
    ),
    CompatRule(
        "B12",
        "warn",
        lambda f: f.api_format == "codex"
        and f.book_type not in CONTEXT_AWARE_BOOK_TYPES
        and bool(_b12_given_compact_flags(f)),
        lambda f: (
            f"{' and '.join(_b12_given_compact_flags(f))} reach the "
            f"translator for epub and markdown books only; on a "
            f"{f.book_type} book the codex thread keeps its own default "
            f"budget."
        ),
    ),
    CompatRule(
        "C1",
        "warn",
        lambda f: f.batch_units_given and not f.plan_mode,
        lambda f: (
            "--max-batch-units caps the units one plan request may carry; "
            "nothing reads it outside plan mode."
        ),
    ),
    CompatRule(
        "C2",
        "warn",
        lambda f: f.book_type != "epub" and f.accumulated_num_given,
        lambda f: (
            f"--accumulated_num is read by the epub loader only; a "
            f"{f.book_type} run groups with --batch_size."
        ),
    ),
    CompatRule(
        "C3",
        "warn",
        lambda f: f.book_type == "epub" and bool(f.options.batch_size),
        lambda f: (
            "--batch_size is not read by the epub loader; group with "
            "--accumulated_num (a token budget) instead."
        ),
    ),
    CompatRule(
        "C4",
        "warn",
        lambda f: f.book_type == "srt" and f.options.prompt_arg is not None,
        lambda f: (
            "--prompt is ignored for srt books: the subtitle loader sends a "
            "prompt of its own, written for timed lines."
        ),
    ),
    CompatRule(
        "C5",
        "warn",
        lambda f: f.options.context_mode == "window"
        and f.book_type not in CONTEXT_AWARE_BOOK_TYPES,
        lambda f: (
            f"--use_context is not forwarded by the {f.book_type} loader; no "
            f"earlier paragraph reaches the model, and it will be ignored."
        ),
    ),
    CompatRule(
        "C6",
        "warn",
        lambda f: f.options.parallel_workers > 1
        and f.book_type not in PARALLEL_AWARE_BOOK_TYPES,
        lambda f: (
            f"--parallel-workers is used by the epub and markdown loaders "
            f"only; a {f.book_type} run stays serial."
        ),
    ),
    CompatRule(
        "C7",
        "warn",
        lambda f: bool(_c7_ignored_tag_flags(f)),
        lambda f: (
            f"{', '.join(_c7_ignored_tag_flags(f))} select markup inside an "
            f"epub; a {f.book_type} book has none, and they will be ignored."
        ),
    ),
    CompatRule(
        "C8",
        "warn",
        lambda f: f.book_type != "epub" and bool(_c8_ignored_style_flags(f)),
        lambda f: (
            f"{' and '.join(_c8_ignored_style_flags(f))} style the "
            f"translation inside epub markup; {f.book_type} output carries "
            f"no styling and will ignore them."
        ),
    ),
    CompatRule(
        "C10",
        "warn",
        lambda f: bool(f.options.retranslate) and f.options.test,
        lambda f: (
            "--retranslate ignores --test / --test_num: it retranslates the "
            "whole range it was given, writes the book and stops."
        ),
    ),
    CompatRule(
        "C11",
        "warn",
        lambda f: f.options.quiet and f.book_type != "epub",
        lambda f: (
            f"--quiet is implemented by the epub loader only; a "
            f"{f.book_type} run prints its progress as usual."
        ),
    ),
    CompatRule(
        "C12",
        "warn",
        lambda f: f.api_format == "codex" and (f.api_base_given or f.key_given),
        lambda f: (
            f"{' and '.join(f.codex_ignored_flags)} "
            f"{'is' if len(f.codex_ignored_flags) == 1 else 'are'} ignored on "
            f"the codex route: it drives the local codex sidecar, which "
            f"reaches its own server and authenticates with your stored "
            f"ChatGPT session."
        ),
    ),
    CompatRule(
        "C16",
        "warn",
        lambda f: f.source_language is not None
        and f.api_format in SOURCE_BLIND_FORMATS,
        lambda f: (
            f"the {f.api_format} engine detects the source language itself, "
            f"so --source_lang ({f.source_language}) reaches nothing on this "
            f"route."
        ),
    ),
    CompatRule(
        "C18",
        "warn",
        lambda f: not f.plan_mode
        and (f.plan_min_coverage_given or f.poetry_group_size_given),
        lambda f: (
            f"{' and '.join(f.plan_only_flags)} "
            f"{'shapes' if len(f.plan_only_flags) == 1 else 'shape'} a plan; "
            f"this run translates the --translate-tags selection and will "
            f"ignore "
            f"{'it' if len(f.plan_only_flags) == 1 else 'them'}."
        ),
    ),
    CompatRule(
        "C19",
        "warn",
        lambda f: bool(f.options.glossary_path)
        and f.book_type in CONTEXT_AWARE_BOOK_TYPES
        and not getattr(f.translate_model, "SUPPORTS_GLOSSARY", False),
        lambda f: (
            f"{_glossary_flag(f)} is carried by the openai-shaped routes and "
            f"codex, which state the pins alongside the text they send. The "
            f"{f.api_format} route does not, so the file will be read and "
            f"then ignored."
        ),
    ),
    CompatRule(
        "C20",
        "warn",
        lambda f: f.options.glossary_auto == "on" and not session_run_expected(f),
        lambda f: (
            "--glossary-auto on learns renderings from the handoff report a "
            "session writes when it compacts, and this run keeps no session "
            "to compact. Pass --use_context session to get one; "
            f"{_glossary_flag(f)} pins terms on this path without needing "
            "one."
        ),
    ),
    CompatRule(
        "C21",
        "warn",
        lambda f: bool(f.options.glossary_path)
        and f.book_type not in CONTEXT_AWARE_BOOK_TYPES,
        lambda f: (
            f"{_glossary_flag(f)} is forwarded by the epub and markdown "
            f"loaders only; a {f.book_type} run sends the model no glossary "
            f"block, and the file will be ignored."
        ),
    ),
    CompatRule(
        "C22",
        "warn",
        lambda f: f.options.provenance and f.book_type != "epub",
        lambda f: (
            f"--provenance records the run in the package document, and only "
            f"an epub has one; on a {f.book_type} book it is accepted and "
            f"records nothing."
        ),
    ),
    CompatRule(
        "C23",
        "warn",
        lambda f: f.options.provenance
        and f.book_type == "epub"
        and not f.options.disclosure,
        lambda f: (
            "--no_disclosure silences everything the file says about the run, "
            "the machine record included, so --provenance records nothing. "
            "Drop one of the two."
        ),
    ),
    CompatRule(
        "C24",
        "warn",
        lambda f: f.options.glossary_auto == "on"
        and session_run_expected(f)
        and not getattr(f.translate_model, "SUPPORTS_GLOSSARY", False),
        lambda f: (
            f"--glossary-auto on learns renderings from the compact turn's "
            f"handoff report, and the {f.api_format} route never asks its "
            f"report for one; the setting is accepted and learns nothing."
        ),
    ),
)


def dry_run_plan_divergence(facts):
    """How the real run's plan will differ from this preview, or None.

    The preview is built the way a JSON-classified plan-mode run is built,
    and `--plan-dry-run` returns before there is an endpoint to contradict
    it. Two things can: the run may not be in plan mode at all, or it may
    plan over a plain conversation rather than a schema.

    Both are `resolve_plan_mode`'s own branches, mirrored here from the
    command alone — the preview has to mirror the runtime derivation, or it
    forecasts a run nobody makes. It forecast one until now: every
    non-OpenAI route was called "plan mode off", which stopped being true
    for the chat-capable ones when the session classifier landed. The codex
    route plans on every run.
    """
    api_format = facts.options.api_format or PLAN_AUTO_FORMAT
    translator = FORMAT_DICT.get(api_format)
    can_talk = translator is not None and _route_can_session_classify(translator)
    if (
        facts.translate_tags_given
        or facts.options.plan_classify == "none"
        or not (api_format == PLAN_AUTO_FORMAT or can_talk)
    ):
        return (
            "this preview describes a plan-mode run. --translate-tags, "
            "--plan-classify none and a route that can neither be asked for "
            "JSON nor hold a conversation each turn plan mode off, and the "
            "real run then translates the --translate-tags selection instead "
            "of this plan."
        )
    if api_format == PLAN_AUTO_FORMAT:
        return None
    if translator is not None and hasattr(translator, "_probe_verdict"):
        # groq, xai, litellm: the OpenAI shape at another address, so the
        # probe decides at run time which channel carries the questions
        return (
            f"the {api_format} route's JSON-schema support is probed at run "
            f"time: the real run classifies this partition on the JSON path "
            f"where the probe holds, and over a plain session otherwise. The "
            f"partition itself is the same either way."
        )
    return (
        f"the {api_format} route has no JSON-schema verdict, so the real run "
        f"classifies this partition over a plain session — three signatures "
        f"a turn, replies checked verbatim. The partition below is what it "
        f"will be asked about; which rows come back skipped can differ from "
        f"a schema-classified run."
    )


# `--plan-dry-run` returns before an endpoint is resolved (it needs no
# credentials and builds no translator), so its own rows are checked there,
# against what the command typed rather than what a run would resolve.
DRY_RUN_RULES = (
    CompatRule(
        "B2",
        "warn",
        lambda f: dry_run_plan_divergence(f) is not None,
        dry_run_plan_divergence,
    ),
    CompatRule(
        "B3",
        "warn",
        lambda f: True,
        lambda f: (
            f"this preview groups at --max-batch-units {f.batch_units} units per "
            f"request, and at the schema-verified token budget. An endpoint "
            f"that verifies JSON mode but not a strict schema carries half of "
            f"each, so the real run can make more requests than these."
        ),
    ),
)


def normalize_options(options, given=None):
    """Post-parse normalization, and a record of what the command typed.

    argparse cannot tell a typed default from silence, and several
    compatibility rows need exactly that difference — so every flag whose
    real default would hide it parses to None and is filled in here, with
    "it was typed" recorded alongside. Idempotent, and the only place the
    two facts are derived, so `main` and the table cannot disagree.
    """
    options.context_flag, options.context_mode = resolve_context_mode(options)
    given = given or SimpleNamespace()
    given.accumulated_num = options.accumulated_num is not None
    given.batch_units = options.batch_units is not None
    given.plan_min_coverage = options.plan_min_coverage is not None
    given.poetry_group_size = options.poetry_group_size is not None
    # A named tag selection is an opt-out from the automatic plan; the
    # parser's None is the only way to tell one from the "p" default.
    given.translate_tags = options.translate_tags is not None
    given.exclude_translate_tags = (
        options.exclude_translate_tags != DEFAULT_EXCLUDE_TRANSLATE_TAGS
    )
    # `--api_base` and `--key` are filled in from --provider and the format's
    # own address later; whether the *command* named them is knowable only here.
    given.api_base = bool(options.api_base)
    given.key = bool(options.key)
    if not given.translate_tags:
        options.translate_tags = "p"
    if not given.plan_min_coverage:
        options.plan_min_coverage = PLAN_MIN_COVERAGE_DEFAULT
    if not given.poetry_group_size:
        options.poetry_group_size = POETRY_GROUP_SIZE_DEFAULT
    return given


def deprecation_notices(options, given):
    """One line for every deprecated flag the command actually typed.

    A list rather than prints, so a test can ask what a command line earns
    without running one, and so the wording sits next to the flags it names.
    """
    notices = []
    typed = getattr(options, "batch_units_deprecated_flag", None)
    if typed:
        notices.append(
            f"{typed} is now --max-batch-units. The old spelling still works "
            f"and means exactly the same thing."
        )
    if given.poetry_group_size:
        notices.append(
            "--poetry-group-size: general grouping and the session handoff "
            "cover poetry now, and the units cap is --max-batch-units. It "
            "still shapes verse windows, but it is on its way out."
        )
    return notices


def run_facts(options, given, **resolved):
    """Everything the table asks about, in one object.

    `options` as parsed and normalized, `given` from `normalize_options`,
    and `resolved` for what the CLI has since worked out — the book type,
    the wire format, the translator class, the classify mode.
    """
    facts = SimpleNamespace(
        options=options,
        env=env,
        book_type="",
        api_format="",
        translate_model=None,
        model_names=(),
        classify_mode="none",
        plan_auto=False,
        translate_tags_given=given.translate_tags,
        exclude_translate_tags_given=given.exclude_translate_tags,
        accumulated_num_given=given.accumulated_num,
        batch_units_given=given.batch_units,
        plan_min_coverage_given=given.plan_min_coverage,
        poetry_group_size_given=given.poetry_group_size,
        api_base_given=given.api_base,
        key_given=given.key,
        source_language=source_evidence(options.source_lang),
        batch_units=GENERAL_GROUP_MAX_UNITS,
    )
    facts.__dict__.update(resolved)
    facts.plan_mode = plan_mode_expected(facts)
    facts.classify_flag = (
        "--plan-classify-model"
        if options.plan_classify_model
        else f"--plan-classify {facts.classify_mode}"
    )
    facts.codex_ignored_flags = [
        flag
        for flag, given in (
            ("--api_base", facts.api_base_given),
            ("--key", facts.key_given),
        )
        if given
    ]
    facts.plan_only_flags = [
        flag
        for flag, given in (
            ("--plan-min-coverage", facts.plan_min_coverage_given),
            ("--poetry-group-size", facts.poetry_group_size_given),
        )
        if given
    ]
    return facts


def check_compatibility(facts, rules=COMPAT_RULES):
    """Run the table. Prints; exits 1 if any row stops the run."""
    tripped = [rule for rule in rules if rule.when(facts)]
    stops = [rule for rule in tripped if rule.level == "stop"]
    if stops:
        # Only the stops: the run is over, and advice about a run that is
        # not going to happen is noise in front of the reason it isn't.
        for rule in stops:
            print(f"[bold red]Error: {rule.say(facts)}[/bold red]")
        raise SystemExit(1)
    for rule in tripped:
        print(f"[bold yellow]Warning:[/bold yellow] {rule.say(facts)}")


def build_parser():
    translate_format_list = list(FORMAT_DICT.keys())
    # No prefix abbreviation: `--model` must not resolve to `--model_list`.
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="path of the book/source file to be translated",
    )
    ########## ENDPOINT ##########
    parser.add_argument(
        "--key",
        "--api_key",
        dest="key",
        type=str,
        default="",
        help="API key for the endpoint (--api_key is the same flag). Several "
        "comma-separated keys are rotated to go beyond per-key rate limits. "
        "Falls back to $BBM_API_KEY, then to the format's conventional "
        "variable ($OPENAI_API_KEY, $ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        help="only the first 10 paragraphs will be translated, for testing",
    )
    parser.add_argument(
        "--test_num",
        dest="test_num",
        type=int,
        default=10,
        help="how many paragraphs will be translated for testing",
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default=None,
        metavar="MODEL",
        help="model id, exactly as the endpoint names it (e.g. gpt-5-mini, "
        "claude-sonnet-4-6, or a namespaced openai/gpt-5-mini). One value "
        "names a route instead of a model: 'orcarouter' sends the run to the "
        "OrcaRouter gateway. Old alias values, 'codex' among them, are "
        "translated to their format or model with a note; prefer "
        "'--api_format codex'. Defaults to gpt-5.6-luna on the openai format; "
        "the anthropic format needs an id",
    )
    parser.add_argument(
        "--api_format",
        dest="api_format",
        type=str,
        default=None,
        choices=translate_format_list,
        metavar="FORMAT",
        help="wire format the endpoint speaks, available: {%(choices)s}. "
        "Inferred from --api_base when omitted (anthropic hosts -> anthropic, "
        "everything else -> openai)",
    )
    parser.add_argument(
        "--language",
        type=language_arg,
        choices=LanguageChoices(
            sorted(LANGUAGES.keys()) + sorted([k.title() for k in TO_LANGUAGE_CODE])
        ),
        default="zh-hans",
        metavar="LANGUAGE",
        help="target language: a tag (zh-hant), a name (Traditional "
        "Chinese), or TAG:NAME to state both when the tables miss the "
        'language (--language "zh-hant:Traditional Chinese"). The tag is '
        "stamped on the output and names the structured field; the name is "
        "what the model is asked for. Available: {%(choices)s}",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="if program stop unexpected you can use this to resume",
    )
    parser.add_argument(
        "-p",
        "--proxy",
        dest="proxy",
        type=str,
        default="",
        help="use proxy like http://127.0.0.1:7890",
    )
    # The endpoint. Everything else about the route is inferred from it.
    parser.add_argument(
        "--api_base",
        metavar="API_BASE_URL",
        dest="api_base",
        type=str,
        help="endpoint to translate against, e.g. https://api.openai.com/v1, "
        "https://api.anthropic.com, a gateway, or http://localhost:11434/v1 "
        "for ollama. Defaults to the format's official host",
    )
    parser.add_argument(
        "--provider",
        dest="provider",
        type=str,
        default="",
        help="named endpoint from bbm_providers.json (this directory) or "
        "~/.bbm/providers.json: its base_url, api_style, default_models and "
        "env_key stand in for --api_base, --api_format, --model and the key. "
        "Anything you pass explicitly wins",
    )
    parser.add_argument(
        "--exclude_filelist",
        dest="exclude_filelist",
        type=str,
        default="",
        help="if you have more than one file to exclude, please use comma to split them, example: --exclude_filelist 'nav.xhtml,cover.xhtml'",
    )
    parser.add_argument(
        "--only_filelist",
        dest="only_filelist",
        type=str,
        default="",
        help="if you only have a few files with translations, please use comma to split them, example: --only_filelist 'nav.xhtml,cover.xhtml'",
    )
    parser.add_argument(
        "--translate-tags",
        dest="translate_tags",
        type=str,
        # None, not "p": a typed selection opts out of the automatic plan,
        # even `--translate-tags p`. Normalized to "p" after parsing.
        default=None,
        help="which tags to translate, example --translate-tags p,blockquote "
        "(default: p). Ignored in plan mode — see --plan-classify",
    )
    parser.add_argument(
        "--plan-dry-run",
        dest="plan_dry_run",
        action="store_true",
        default=False,
        help="build and print the translation plan (per-signature coverage "
        "table), write <book>_plan.json, and exit without translating "
        "(epub only)",
    )
    parser.add_argument(
        "--plan-min-coverage",
        dest="plan_min_coverage",
        type=coverage_fraction,
        # None is "not typed"; PLAN_MIN_COVERAGE_DEFAULT is filled in after
        # parsing, so the no-op warning can tell silence from the default
        default=None,
        help="in plan mode, abort if the plan covers less than this fraction "
        "of the book's text, between 0 and 1 (default 0.5)",
    )
    parser.add_argument(
        "--poetry-group-size",
        dest="poetry_group_size",
        type=poetry_group,
        default=None,
        help="deprecated: general grouping and the session handoff cover "
        "poetry now, and the units cap is --max-batch-units. Still honoured "
        "-- plan mode gives consecutive short lines one translation request, "
        "at most this many per request (default 8; ~500 characters per "
        "request either way) -- but it warns, and it is on its way out",
    )
    parser.add_argument(
        "--plan-classify",
        dest="plan_classify",
        # "most" is the old name of "all", still parsed and mapped in main()
        # with a notice; the metavar keeps it out of --help.
        choices=["auto", "none", "all", "model", "agent", "most"],
        metavar="{auto,none,all,model,agent}",
        default="auto",
        help="coverage-complete plan mode (epub only): partition the whole "
        "book, then decide which tag signatures are worth translating. "
        "'auto' (default): plan the book as 'model' when it is an epub — "
        "over structured output where the endpoint is verified to apply a "
        "strict JSON schema, and otherwise over a plain conversation "
        "(exact skip/translate/unsure replies; unsure and anything "
        "unparsable translate) on any route that can hold one, the codex "
        "route included. Only a route with no conversation at all falls "
        "back to the --translate-tags selection; a plan that cannot be "
        "completed falls back to that selection too. "
        "'none': no plan — translate the --translate-tags "
        "selection as usual. "
        "'all': translate the whole partition, no classification, no plan "
        "file. "
        "'model': an LLM rules on every undecided signature, then the run "
        "continues and translates the book; unresolved rows stop it instead. "
        "'agent': write the plan JSON with samples, print instructions to "
        "paste into a coding-agent session, and stop before translating — "
        "rerun the same command afterwards to translate",
    )
    parser.add_argument(
        "--plan-classify-model",
        dest="plan_classify_model",
        type=str,
        default="",
        help="model for plan-signature classification (default: the "
        "translating model). When set explicitly, a classification failure "
        "aborts the run instead of falling back to the heuristic plan",
    )
    parser.add_argument(
        "--exclude-translate-tags",
        dest="exclude_translate_tags",
        type=str,
        default=DEFAULT_EXCLUDE_TRANSLATE_TAGS,
        help="Exclude content within specified HTML tags from translation. Use comma to separate multiple tags. Default: sup,code. Example: --exclude-translate-tags code,pre",
    )
    parser.add_argument(
        "--allow_navigable_strings",
        dest="allow_navigable_strings",
        action="store_true",
        default=False,
        help="allow NavigableStrings to be translated",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_arg",
        type=str,
        metavar="PROMPT_ARG",
        help="customize the prompt: a template string, a JSON string, or a "
        "path to a .json, .txt or .md file (.md is read as PromptDown). The "
        "JSON keys are `user` (the template, required, and it must contain "
        "`{text}`; `{language}` is substituted too), `system`, and `style` "
        "(a note on register and voice, handed on verbatim to every window "
        "in session mode). A bare string or a .txt file is the `user` "
        "template.",
    )
    parser.add_argument(
        "--accumulated_num",
        dest="accumulated_num",
        type=accumulated_tokens,
        default=None,
        help="""Wait for how many tokens have been accumulated before starting the translation.
gpt3.5 limits the total_token to 4090.
For example, if you use --accumulated_num 1600, maybe openai will output 2200 tokens
and maybe 200 tokens for other messages in the system messages user messages, 1600+2200+200=4000,
So you are close to reaching the limit. You have to choose your own value, there is no way to know if the limit is reached before sending.
In EPUB plan mode this is a per-request token budget: consecutive units of any
length share one request up to this many tokens (at most --max-batch-units units
per request; half that when the endpoint verifies JSON mode but not a strict
schema).
Untyped, every plan run derives a default from the run's own prompt overhead:
1600 with the stock prompts, up to 2000 under a fat custom --prompt, and half
that (floor 800) per request on an endpoint without a strict-schema verdict —
the same margin that halves the unit cap there. Session runs (codex included)
keep the un-halved value: 1600-2000 is their measured default. The run
narrates the number and the route class it chose; pass 1 to turn grouping
off. Minimum 1.
""",
    )
    parser.add_argument(
        "--max-batch-units",
        dest="batch_units",
        type=batch_unit_cap,
        default=None,
        help="EPUB plan mode only: the most units --accumulated_num's token "
        f"budget may put in one request. Default {GENERAL_GROUP_MAX_UNITS}, "
        "half the level a fault-emergence eval measured content faults at; "
        "lower it for a weaker model. An endpoint that verifies JSON mode "
        "but not a strict schema carries half this many.",
    )
    # The old spelling, kept working and kept out of --help; see DeprecatedAlias.
    parser.add_argument(
        "--batch_units",
        dest="batch_units",
        type=batch_unit_cap,
        action=DeprecatedAlias,
        default=None,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--translation_style",
        dest="translation_style",
        type=str,
        help="""ex: --translation_style "color: #808080; font-style: italic;" """,
    )
    parser.add_argument(
        "--translation_color",
        dest="translation_color",
        type=str,
        help="color for translated text, e.g. --translation_color '#1e90ff' or --translation_color 'red'",
    )
    parser.add_argument(
        "--batch_size",
        dest="batch_size",
        type=int,
        help="how many text units will be translated by aggregated translation for supported loaders",
    )
    parser.add_argument(
        "--pdf_layout",
        dest="pdf_layout",
        choices=["none", "top-bottom", "side-by-side", "all"],
        default="none",
        help="PDF output layout for PDF inputs: top-bottom, side-by-side, all, or none",
    )
    parser.add_argument(
        "--retranslate",
        dest="retranslate",
        nargs=4,
        type=str,
        help="""--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"
        Retranslate from start_str through end_str. All four arguments are required;
        pass an empty end_str ('') to retranslate only the start_str tag, and an empty
        file_name_in_epub ('') to find the internal filename automatically.
""",
    )
    parser.add_argument(
        "--single_translate",
        action="store_true",
        help="output translated book, no bilingual",
    )
    parser.add_argument(
        "--sentence_mode",
        action="store_true",
        help="translate sentence by sentence within each paragraph instead of the whole paragraph at once",
    )
    parser.add_argument(
        "--no_disclosure",
        dest="disclosure",
        action="store_false",
        help="do not mark the epub as a machine translation (translator credit, description line and the closing translation note); the model id is recorded verbatim",
    )
    parser.add_argument(
        "--provenance",
        dest="provenance",
        action="store_true",
        help="record how the file was made, invisibly — the full record (build, model, endpoint host, sanitized command line, languages, date) is a bbm_provenance.json in the book, with three bbm: metas beside it as a marker; never the key or the --prompt text. Plan-mode and session runs record it by themselves; this is the tag-mode opt-in. --no_disclosure silences it too",
    )
    parser.add_argument(
        "--use_context",
        dest="context_mode",
        nargs="?",
        const="window",
        default=None,
        choices=("window", "session"),
        help="carry earlier paragraphs into each request for narrative "
        "consistency. Bare (or 'window'): re-send the last few "
        "source/translation pairs, costing ~200 extra tokens per request. "
        "'session': keep one append-only history instead, so an endpoint "
        "with prompt caching re-reads it at its cache rate and the context "
        "can grow to chapter length for less money -- compacted into a "
        "handoff report at --context-compact-at",
    )
    parser.add_argument(
        "--context-compact-at",
        dest="context_compact_at",
        type=compact_budget,
        default=None,
        help=f"estimated-token budget for a rolling history. In session mode "
        f"the history is compacted into a translator handoff report at this "
        f"size; when unset every session run — grouped or not, the codex "
        f"route included, --use_context or not — uses "
        f"{DEFAULT_COMPACT_BUDGET}, printed at start. It also bounds the "
        f"plan classifier's own conversation on endpoints that classify over "
        f"a plain session (which restarts there, no handoff). An explicit "
        f"value always wins; minimum 500",
    )
    parser.add_argument(
        "--no-context-compact",
        dest="no_context_compact",
        action="store_true",
        help="session mode only: never ask for a handoff report. The window "
        "still rolls over when it reaches the budget, but the next one starts "
        "empty instead of inheriting a summary",
    )
    parser.add_argument(
        "--glossary",
        "--terminology",
        dest="glossary_path",
        action=GlossaryPath,
        type=glossary_file,
        default=None,
        help="a file of 'term -> translation' lines (one per line, '#' starts "
        "a note or a comment) that this run must render that way. "
        "--terminology is the same flag. Only the terms that occur in a "
        "request are sent with it, so a long file costs nothing on the "
        "paragraphs it does not touch. A term pinned here says what the "
        "translation says, so pin only renderings you can stand behind. Read "
        "by the openai- and codex-shaped routes for epub and markdown books",
    )
    parser.add_argument(
        "--glossary-auto",
        dest="glossary_auto",
        choices=("on", "off"),
        default=None,
        help="whether a session run also keeps the renderings its own handoff "
        "reports establish, so recurring names stay unified across a context "
        "window seam. On by default wherever a session runs (--use_context "
        "session, and the codex route's one thread); 'off' asks the compact "
        "turn for a summary only. Learned terms live in this run and in "
        "<book>_handoff.md, and nowhere else",
    )
    parser.add_argument(
        "--context_paragraph_limit",
        dest="context_paragraph_limit",
        type=int,
        default=0,
        help="window mode only: how many paragraph pairs to re-send",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="sampling temperature, on the formats that take one. The "
        "anthropic format always sends it; the openai format leaves it out "
        "when it equals the API default and when the model rejects an "
        "explicit one (gpt-5.x, the o-series); the codex format has no "
        "such setting and ignores it",
    )
    parser.add_argument(
        "--source_lang",
        type=str,
        default="auto",
        help="source language, stated rather than detected. Named in the "
        "prompt on every LLM route, sent as a request field on the "
        f"{' and '.join(SOURCE_LANG_FORMATS)} routes, and recorded by "
        "--provenance (default: auto-detect, which states nothing)",
    )
    parser.add_argument(
        "--block_size",
        type=int,
        default=-1,
        help="merge multiple paragraphs into one block, may increase accuracy "
        "and speed up the process, but disturb the original format. Ignored "
        "while --accumulated_num is above 1",
    )
    parser.add_argument(
        "--model_list",
        type=str,
        dest="model_list",
        help="several model IDs to rotate across, comma-separated, to spread "
        "rate limits. Kept for compatibility with older commands; a single "
        "model belongs in --model",
    )
    parser.add_argument(
        "--batch",
        dest="batch_flag",
        action="store_true",
        help="Enable batch translation using ChatGPT's batch API for improved efficiency",
    )
    parser.add_argument(
        "--batch-use",
        dest="batch_use_flag",
        action="store_true",
        help="Use pre-generated batch translations to create files. Run with --batch first before using this option",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.01,
        help="seconds to wait between requests, e.g. 0.1 for 100ms. Only the "
        "gemini format paces itself with it; every other route ignores it. "
        "Default: 0.01",
    )
    parser.add_argument(
        "--parallel-workers",
        dest="parallel_workers",
        type=int,
        default=1,
        help="translate several EPUB chapters (or Markdown batches and "
        "sections) at once; 2-4 is the useful range. Default: 1. Refused "
        "with --use_context session, whose one history cannot be shared, "
        "and on the codex format, whose one thread cannot. Note that a "
        "parallel run cannot be stopped promptly: every chapter is "
        "dispatched before the first one finishes",
    )
    parser.add_argument(
        "--extra_body",
        dest="extra_body",
        type=str,
        default="",
        help="JSON object of extra fields to add to every request body, on "
        "the openai and anthropic routes. It reaches the capability probe "
        "and the JSON rungs as well as the translate calls, so the endpoint "
        "is graded on the request the run actually makes. Merged over the "
        "named parameters, so a field here beats the flag for it (a "
        "temperature in --extra_body beats --temperature). Examples: "
        '\'{"chat_template_kwargs": {"enable_thinking": false}}\' on the '
        'openai route, \'{"thinking": {"type": "disabled"}}\' on anthropic',
    )
    parser.add_argument(
        "--extra_headers",
        dest="extra_headers",
        type=str,
        default="",
        help="JSON object of extra HTTP headers to send with every request, "
        "on the openai and anthropic routes. Set on the client, so the "
        "capability probe, the model check and the model listing carry them "
        "too. Example: --extra_headers "
        '\'{"HTTP-Referer": "https://example.com", "X-Title": "bbm"}\'',
    )
    parser.add_argument(
        "--quiet",
        dest="quiet",
        action="store_true",
        help="suppress progress bars and per-paragraph translation echoes "
        "(for log files and non-interactive runs; reports and errors still "
        "print). Currently epub only.",
    )
    # Which spelling of the glossary flag was typed, so a warning can name it.
    # Set by `GlossaryPath`; this is the value when neither was.
    parser.set_defaults(glossary_flag="--glossary")
    # Set only by `DeprecatedAlias`; this is the value when the current
    # spelling (or nothing) was typed.
    parser.set_defaults(batch_units_deprecated_flag=None)
    return parser


def parse_args(argv):
    return build_parser().parse_args(argv)


def main():
    # Old command lines are rewritten into the endpoint surface before the
    # parser sees them; see book_maker/legacy_cli.py.
    legacy = translate_legacy_argv(sys.argv[1:])
    for notice in legacy.notices:
        print(f"[yellow]deprecated:[/yellow] {escape(notice)}")

    options = parse_args(legacy.argv)
    # None is "not typed": --accumulated_num keeps its explicitness (plan
    # mode defaults the budget by context mode, and an explicit 1 must still
    # mean grouping off), and --max-batch-units falls back to the measured cap.
    given = normalize_options(options)
    accumulated_num_given = given.accumulated_num
    translate_tags_given = given.translate_tags
    batch_units = (
        GENERAL_GROUP_MAX_UNITS if options.batch_units is None else options.batch_units
    )
    if options.plan_classify == "most":
        print("[yellow]--plan-classify most is now --plan-classify all[/yellow]")
        options.plan_classify = "all"
    for notice in deprecation_notices(options, given):
        print(f"[yellow]deprecated:[/yellow] {escape(notice)}")

    # Said once, here rather than beside the loader, so a --plan-dry-run and
    # a paid run both get it before anything else happens.
    guidance = language_guidance(parse_language_spec(options.language))
    if guidance:
        print(guidance)

    if not options.book_name:
        print("Error: please provide the path of your book using --book_name <path>")
        exit(1)
    if not os.path.isfile(options.book_name):
        print(f"Error: the book {options.book_name!r} does not exist.")
        exit(1)

    if options.plan_dry_run:
        # No translation happens, so no credentials are needed: build the
        # plan straight from the file, honoring the file filters.
        if get_book_type(options.book_name) != "epub":
            print("[bold red]--plan-dry-run only works with epub books[/bold red]")
            exit(1)
        from ebooklib import epub as _epub

        from book_maker.loader.plan import build_plan, is_fixed_layout

        from book_maker.loader.epub_loader import check_file_filters_against

        book = _epub.read_epub(options.book_name)
        # a typo in a filter is answerable here too, before a plan that
        # would silently not honor it is written
        check_file_filters_against(
            book, options.only_filelist, options.exclude_filelist
        )
        if is_fixed_layout(book):
            print(
                "[bold yellow]warning: this is a fixed-layout (pre-paginated) "
                "EPUB — its text boxes are sized for the original words, so "
                "translated text may overflow or misplace.[/bold yellow]"
            )
        # The preview must group the way the run will: an explicit
        # --accumulated_num wins (1 turning grouping off), and an untyped one
        # takes the derived default exactly as _plan_token_budget does. Since
        # 260906 that default applies to every plan run, not only a session
        # one. `--use_context session` is knowable here and settles the route
        # on its own; anything else splits on a probe verdict there is no
        # endpoint to ask for, so the preview groups at the schema-verified
        # derivation — the larger of the two, so its request count is the
        # optimistic one — and the notice names both numbers.
        from book_maker.loader.plan import derived_token_budget, plan_budget_notice

        dry_route = "session" if options.context_mode == "session" else None
        if accumulated_num_given:
            # 0, not None: an explicit 1 turns every grouping rule off, and
            # None would preview the short-run grouping the run won't do
            dry_budget = options.accumulated_num if options.accumulated_num > 1 else 0
        else:
            # No translator exists on a dry run, so nothing can measure the
            # prompt overhead: None, and the floor stands.
            dry_budget = derived_token_budget(None, dry_route or "schema")
            print(plan_budget_notice(None, dry_route))
            if (
                options.prompt_arg
                or os.environ.get("BBM_CHATGPTAPI_USER_MSG_TEMPLATE")
                or os.environ.get("BBM_CHATGPTAPI_SYS_MSG")
                # counted by prompt_overhead_tokens like any other system
                # message, and it outranks --prompt's own
                or os.environ.get("OPENAI_API_SYS_MSG")
            ):
                # The real run measures its own prompt; a fat custom one —
                # flag or environment — can raise the budget past the floor
                # and group differently.
                print(
                    f"note: the preview assumes the stock prompt overhead "
                    f"(budget {dry_budget}); a large custom prompt can "
                    f"raise the real run's budget, up to 2000"
                )
        plan = build_plan(
            book,
            exclude_tags=tuple(
                t for t in options.exclude_translate_tags.split(",") if t
            ),
            poetry_group_size=options.poetry_group_size,
            only_files=set(f for f in options.only_filelist.split(",") if f) or None,
            exclude_files=set(f for f in options.exclude_filelist.split(",") if f)
            or None,
            token_budget=dry_budget,
            batch_units=batch_units,
        )
        # samples are book text: rich would eat "[Seven] warriors [they were]"
        print(escape(plan.report()))
        # Parity with the run: same sentence, same source, so the preview
        # cannot promise a window the run does not use.
        if options.context_mode == "session" and not options.no_context_compact:
            print(compact_budget_notice(options.context_compact_at))
        # What this preview cannot know: whether the real run will be in plan
        # mode at all, and how far the endpoint will be trusted with one
        # request. Both change the numbers just printed.
        check_compatibility(
            run_facts(options, given, book_type="epub", batch_units=batch_units),
            rules=DRY_RUN_RULES,
        )
        plan_path = f"{os.path.splitext(options.book_name)[0]}_plan.json"
        if os.path.exists(plan_path):
            print(
                f"existing plan {plan_path} kept (may carry your edits); "
                f"delete it to regenerate"
            )
        else:
            plan.save_json(plan_path, book_path=options.book_name)
            print(
                f"plan written to {plan_path} — every row is a question: decide "
                f'it by setting "action", "decided_by" and "content_type"'
            )
            # classification needs credentials, which a dry run must not
            print(
                "note: nothing here is decided yet. A later --plan-classify "
                "model run rules on every row still null, an agent run hands "
                "them to a coding agent, and rows you decide yourself are "
                "left alone by both"
            )
        return

    PROXY = options.proxy
    if PROXY != "":
        os.environ["http_proxy"] = PROXY
        os.environ["https_proxy"] = PROXY

    model_names, api_format, endpoint_env_keys = resolve_endpoint(options)
    route = ROUTE_DICT.get(model_names[0]) if len(model_names) == 1 else None
    translate_model = route or FORMAT_DICT.get(api_format)
    assert translate_model is not None, f"unsupported api format: {api_format}"

    # Refusals the endpoint alone decides, before a key is read, a book is
    # parsed or the codex sidecar is started.

    # Batch translation is OpenAI's Batch API. The codex format has no such
    # thing, and reached it anyway: `AttributeError: batch_init` partway into
    # a run that had already spent plan quota.
    if (options.batch_flag or options.batch_use_flag) and not getattr(
        translate_model, "SUPPORTS_BATCH_API", False
    ):
        print(
            f"[bold red]Error: --batch / --batch-use are the OpenAI Batch "
            f"API, which the {api_format} format does not have. Drop the "
            f"flag, or translate through an OpenAI-shaped endpoint.[/bold red]"
        )
        exit(1)

    # A format that does not implement session mode used to accept the flag
    # and translate as though it had never been passed — the run cost more
    # attention than a window run and bought nothing.
    if options.context_mode == "session" and not getattr(
        translate_model, "SUPPORTS_SESSION_CONTEXT", False
    ):
        print(
            f"[bold red]Error: --use_context session is not implemented for "
            f"the {api_format} format; it would be accepted and ignored. Use "
            f"bare --use_context for a re-sent window of paragraph "
            f"pairs.[/bold red]"
        )
        exit(1)

    # One codex thread is the route's whole context; workers would interleave
    # chapters into it.
    if options.parallel_workers > 1 and api_format == "codex":
        print(
            "[bold red]Error: --parallel-workers is not supported on the codex "
            "format: one thread is the context, and workers would interleave "
            "chapters into it. Drop --parallel-workers.[/bold red]"
        )
        exit(1)

    # Session mode is one growing history. Workers cannot share it, and one
    # each is window mode at session prices.
    if options.parallel_workers > 1 and options.context_mode == "session":
        print(
            "[bold red]Error: --parallel-workers is not supported with "
            "--use_context session: one history is the context, and a worker "
            "cannot share it. Use bare --use_context to keep the workers, or "
            "drop --parallel-workers to keep the session.[/bold red]"
        )
        exit(1)

    # Parallel workers each get a clone carrying their own chapter context.
    # A format that keeps no re-sendable window has nothing to clone, and the
    # run died reading a context attribute it never set — after the chapters
    # were already dispatched.
    if (
        options.parallel_workers > 1
        and options.context_mode is not None
        and not getattr(translate_model, "SUPPORTS_PARALLEL_CONTEXT", False)
    ):
        print(
            f"[bold red]Error: --parallel-workers is not supported with "
            f"--use_context on the {api_format} format, which keeps no "
            f"per-chapter context for a worker to carry. Drop "
            f"--parallel-workers, or drop --use_context for this "
            f"run.[/bold red]"
        )
        exit(1)

    book_type = get_book_type(options.book_name)
    support_type_list = list(BOOK_LOADER_DICT.keys())
    if book_type not in support_type_list:
        raise Exception(
            f"now only support files of these formats: {','.join(support_type_list)}",
        )

    # Which mode the two --plan-classify flags asked for. Resolved here
    # because the compatibility table asks about it; the one contradiction
    # between them is still refused further down, where its message lives.
    classify_mode, plan_auto = resolve_classify_mode(options)

    # The compatibility table: every combination that would be paid for and
    # then wasted, degraded or ignored. After the endpoint is resolved (the
    # answers depend on the route) and before any translator is built.
    check_compatibility(
        run_facts(
            options,
            given,
            book_type=book_type,
            api_format=api_format,
            translate_model=translate_model,
            model_names=model_names,
            classify_mode=classify_mode,
            plan_auto=plan_auto,
            batch_units=batch_units,
        )
    )

    # A codex run's context is the thread, and a thread does not survive the
    # process. The handoff report on disk is written, never read back.
    if api_format == "codex" and options.resume:
        print(
            "[bold yellow]Note:[/bold yellow] a resumed codex run starts a "
            "new thread. Nothing already translated is paid for again, but "
            "the earlier thread's terminology and register are not carried "
            "into it."
        )
    API_KEY = resolve_api_key(
        api_format,
        options.key,
        options.api_base,
        endpoint_env_keys + legacy.env_keys,
    )

    # Read before the book is opened: a glossary that will not parse is the
    # operator's typo, and finding it after the first paid request would mean
    # translating with pins they did not get.
    glossary = Glossary()
    if options.glossary_path:
        try:
            glossary = Glossary.from_file(options.glossary_path)
        except (OSError, ValueError) as err:
            raise SystemExit(f"Could not read {options.glossary_flag}: {err}")
        print(f"[green]Glossary: {len(glossary)} pinned terms loaded[/green]")

    # Compaction flags act on a session history, or on the codex thread,
    # which is always one. Anywhere else they do nothing, and say so.
    if options.context_mode != "session" and api_format != "codex":
        for flag, value in (
            ("--context-compact-at", options.context_compact_at),
            ("--no-context-compact", options.no_context_compact),
        ):
            if value:
                print(
                    f"[bold yellow]Warning:[/bold yellow] {flag} only applies "
                    f"to --use_context session; ignoring it."
                )

    book_loader = BOOK_LOADER_DICT.get(book_type)
    assert book_loader is not None, "unsupported loader"
    # `--language zh-hant:Traditional Chinese`: the tag is stamped on the
    # output and names the structured field, the name is what the model is
    # asked for. A bare value resolves the way it always has.
    target = parse_language_spec(options.language)
    language = target.name
    # The source is evidence for the model and nothing else; `--source_lang`
    # is where it is stated, and "auto" states nothing.
    source_language = source_evidence(options.source_lang)

    # None lets each SDK use its own official host.
    model_api_base = options.api_base

    loader_kwargs = {}
    if book_type in CONTEXT_AWARE_BOOK_TYPES:
        loader_kwargs.update(
            context_mode=options.context_mode,
            context_compact_at=options.context_compact_at,
            no_context_compact=options.no_context_compact,
            glossary=glossary,
            glossary_auto=glossary_auto_flag(options.glossary_auto),
        )
    elif options.context_mode == "session":
        # txt, srt and pdf never hand context to the model, so a session
        # budget would quietly do nothing at all.
        print(
            f"[bold yellow]Warning:[/bold yellow] --use_context session is "
            f"not supported for {book_type} books; it will be ignored."
        )
    if book_type == "pdf":
        loader_kwargs["pdf_layout"] = options.pdf_layout
    # `--provenance` has no warning of its own here: the two ways it can be
    # asked for and do nothing — a non-epub book, and `--no_disclosure`
    # beside it — are rows C22 and C23 of COMPAT_RULES, said before the
    # endpoint is resolved with everything else that does not fit together.
    if book_type == "epub":
        # The tag, not the prose: epub is the only route that stamps one, on
        # the markup it inserts and on the first dc:language of the output.
        loader_kwargs["language_tag"] = target.tag
        loader_kwargs["disclose"] = options.disclosure
        loader_kwargs["provenance"] = options.provenance
    elif not options.disclosure:
        print(
            "[bold yellow]Warning:[/bold yellow] --no_disclosure is ignored for "
            f"{book_type} books; only epub output carries the translation note."
        )

    # Parsed once, here, so the run can say what it adopted before it spends
    # anything. (`parse_prompt_arg` prints its own "prompt config:" echo on
    # this call; the compat pass reads the same flag with announce=False.)
    prompt_config = parse_prompt_arg(options.prompt_arg)
    adoption = prompt_adoption_line(prompt_config, translate_model, api_format)
    if adoption:
        print(adoption)

    e = book_loader(
        options.book_name,
        translate_model,
        API_KEY,
        options.resume,
        language=language,
        model_api_base=model_api_base,
        is_test=options.test,
        test_num=options.test_num,
        prompt_config=prompt_config,
        single_translate=options.single_translate,
        context_flag=options.context_flag,
        context_paragraph_limit=options.context_paragraph_limit,
        temperature=options.temperature,
        source_lang=options.source_lang,
        parallel_workers=options.parallel_workers,
        **loader_kwargs,
    )
    if options.glossary_path:
        # The provenance record embeds the operator's file (never a derived
        # glossary), and the loader only knows about it through this
        # attribute — without it `--glossary … --provenance` recorded a run
        # with no glossary at all.
        e.glossary_path = options.glossary_path
    if getattr(e, "translate_model", None) is not None:
        if source_language:
            # Reaches the prompt/system message and the schema field
            # descriptions. Never a gate: a book whose source is not what
            # the flag says still translates, it just says so in one
            # sentence.
            e.translate_model.source_language = source_language
        if target.pinned:
            # Only a tag the operator wrote themselves overrides the field
            # name; a bare --language keeps the name derived from the prose,
            # which is what every result file already on disk was written
            # under.
            e.translate_model.language_field_tag = target.tag
    price_table = getattr(options, "price_table", None)
    if price_table is not None and hasattr(e.translate_model, "usage"):
        # the bar shows what was spent instead of token counts
        e.translate_model.usage.prices = price_table
    # Request extras, on the routes that build a request these can join.
    # Setting an arbitrary attribute on the others used to print success and
    # then silently drop the fields.
    if options.extra_body or options.extra_headers:
        given = [
            flag
            for flag, value in (
                ("--extra_body", options.extra_body),
                ("--extra_headers", options.extra_headers),
            )
            if value
        ]
        if not translate_model.SUPPORTS_REQUEST_EXTRAS:
            # Named by capability, not by format: `groq`, `xai`, `litellm`
            # and `orcarouter` are the openai request path and do take them,
            # and naming the format would have told those runs otherwise.
            print(
                f"[bold yellow]Warning:[/bold yellow] "
                f"{' and '.join(given)} "
                f"{'is' if len(given) == 1 else 'are'} ignored by the "
                f"{api_format} route, which builds no request they could "
                f"join; the run continues without them."
            )
        else:
            extras = {}
            for flag, dest in (
                ("--extra_body", "extra_body"),
                ("--extra_headers", "extra_headers"),
            ):
                raw = getattr(options, dest)
                if not raw:
                    continue
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError as ex:
                    print(f"[bold red]Error:[/bold red] invalid JSON in {flag}: {ex}")
                    exit(1)
                if not isinstance(parsed, dict):
                    # A list or a bare string would be accepted by the SDK
                    # and rejected by the endpoint, one paid request later.
                    print(
                        f"[bold red]Error:[/bold red] {flag} must be a JSON "
                        f"object, not {type(parsed).__name__}."
                    )
                    exit(1)
                extras[dest] = parsed
            if "extra_headers" in extras and not all(
                isinstance(v, str) for v in extras["extra_headers"].values()
            ):
                # httpx raises on a non-string header value, deep in the
                # first request rather than here.
                print(
                    "[bold red]Error:[/bold red] --extra_headers values must "
                    "all be strings."
                )
                exit(1)
            e.translate_model.set_request_extras(**extras)
            if "extra_body" in extras:
                # Through redact(): a body field is not where a credential
                # belongs, but a gateway that wants the key in the body gets
                # it repeated here, and the echo must not print it either.
                print(
                    f"[bold blue]--extra_body:[/bold blue] "
                    f"{escape(redact(str(extras['extra_body'])))}"
                )
            if "extra_headers" in extras:
                # Names only. A header is where a credential goes —
                # Authorization, X-API-Key — and echoing the value would put
                # it in every log and CI artifact the run touches.
                names = ", ".join(sorted(extras["extra_headers"]))
                print(
                    f"[bold blue]--extra_headers:[/bold blue] {escape(names)} "
                    f"(values not shown)"
                )
    # other options
    if options.sentence_mode:
        e.sentence_mode = True
    if options.allow_navigable_strings:
        e.allow_navigable_strings = True
    # --plan-classify-model names a classifier, which only makes sense in
    # model mode; asking for it alongside a no-classification mode is a
    # contradiction, not a preference to resolve silently. (The modes
    # themselves are resolved by resolve_classify_mode above, which leaves
    # this combination as typed so this refusal still owns it.)
    if options.plan_classify_model and classify_mode in ("all", "agent"):
        reason = (
            "agent mode makes no API call"
            if classify_mode == "agent"
            else "all mode skips classification"
        )
        print(
            f"[bold red]Error:[/bold red] --plan-classify-model cannot be "
            f"combined with --plan-classify {classify_mode} ({reason})"
        )
        exit(1)
    # Plan mode is epub-only, and 'agent' in particular promises to stop
    # before spending anything; silently translating a txt/md book instead
    # would be the exact opposite of what was asked.
    if classify_mode != "none" and book_type != "epub":
        print(
            f"[bold red]Error:[/bold red] --plan-classify {classify_mode} "
            f"requires an epub book (plan mode is epub-only); got a "
            f"{book_type} book"
        )
        exit(1)

    if options.translate_tags:
        e.translate_tags = options.translate_tags
    # Any classification choice is a choice to have a plan, and the plan
    # partitions the whole book — a tag selection has nothing left to select.
    if classify_mode != "none":
        if options.translate_tags != "p":
            # "p" is argparse's default, so an untouched flag stays quiet;
            # a real selection being discarded deserves a line
            print(
                f"note: --plan-classify {classify_mode} plans the whole book; "
                f"ignoring --translate-tags {options.translate_tags}"
            )
        e.plan_mode = True
        e.translate_tags = "auto"
    # `--exclude-translate-tags ""` is the documented way to exclude nothing
    # (README). Testing for truthiness swallowed it and left the sup,code
    # default standing, with nothing printed to say so.
    if options.exclude_translate_tags is not None:
        e.exclude_translate_tags = options.exclude_translate_tags
    if hasattr(e, "plan_min_coverage"):
        e.plan_min_coverage = options.plan_min_coverage
        e.poetry_group_size = options.poetry_group_size
        # plan mode is triggered by translate_tags == "auto"; the classify
        # entry reaches the loader as chosen. "all" in particular must stay
        # distinguishable from "no plan": it is the deliberate
        # translate-everything decision, and the loader has to know it was
        # made rather than infer it from the absence of one.
        e.plan_classify = classify_mode
        e.plan_classify_model = options.plan_classify_model or None
    if options.quiet and hasattr(e, "quiet"):
        e.quiet = True
        # The translator prints echoes of its own — handoff reports, window
        # rollovers — and cannot see the loader's flag.
        if hasattr(getattr(e, "translate_model", None), "quiet"):
            e.translate_model.quiet = True
    if options.exclude_filelist:
        e.exclude_filelist = options.exclude_filelist
    if options.only_filelist:
        e.only_filelist = options.only_filelist
    # Both lists name documents inside the book, which is already open. A
    # typo is answerable here and nowhere cheaper — before any model setup,
    # so a sidecar boot or a context-window lookup cannot precede it.
    if hasattr(e, "check_file_filters"):
        e.check_file_filters()
    if accumulated_num_given:
        # 1 keeps the loader's own default and turns grouping off; the flag
        # having been typed is recorded either way, because plan mode's
        # session default has to know the difference between "1" and silence.
        if hasattr(e, "accumulated_num_given"):
            e.accumulated_num_given = True
        if options.accumulated_num > 1:
            e.accumulated_num = options.accumulated_num
    if hasattr(e, "batch_units"):
        e.batch_units = batch_units
    if options.batch_units is not None and hasattr(
        e.translate_model, "substrict_batch_cap"
    ):
        # the translator enforces the same cap per request when the endpoint
        # is below strict decoding, and it is halved there too
        e.translate_model.substrict_batch_cap = max(1, batch_units // 2)
    if options.translation_color:
        e.translation_style = f"color: {options.translation_color};"
    if options.translation_style:
        # --translation_style is the whole declaration block, so it replaces
        # the colour rather than merging with it. Losing a flag the user
        # typed is worth a line.
        if options.translation_color:
            print(
                f"[bold yellow]Warning:[/bold yellow] --translation_style "
                f"replaces --translation_color; the colour "
                f"{options.translation_color!r} is ignored. Put it in the "
                f"style instead."
            )
        e.translation_style = options.translation_style
    if options.batch_size:
        e.batch_size = options.batch_size
    if options.block_size > 0:
        e.block_size = options.block_size
    # Note: Default block_size is now 1 (delimiter-based translation) for better quality
    if options.retranslate:
        e.retranslate = options.retranslate
    if api_format in LLM_FORMATS:
        if not model_names and api_format not in MODEL_OPTIONAL_FORMATS:
            raise SystemExit(
                f"--model is required for the {api_format} format. Pass the "
                f"model id the endpoint uses, e.g. --model "
                f"{MODEL_EXAMPLES.get(api_format, 'gpt-5-mini')}"
            )
        # Only the gemini route paces itself between requests; --interval
        # is described as ignored everywhere else, so it is not offered
        # to a translator that would silently drop it.
        if api_format == "gemini":
            e.translate_model.set_interval(options.interval)
        if route is None:  # a route's class names its own model
            try:
                e.translate_model.set_model_list(model_names)
            except Exception as ex:
                print(f"[red]Error: {ex}[/red]")
                exit(1)
        # Settled before the first paid request: the codex sidecar is up and
        # signed in.
        if hasattr(e.translate_model, "preflight"):
            try:
                e.translate_model.preflight()
            except Exception as err:
                if not getattr(err, "user_facing", False):
                    raise
                print(f"[bold red]{escape(redact(err))}[/bold red]")
                exit(1)
    elif model_names:
        # These formats translate through a fixed engine and take no model, so
        # honoring the flag is impossible; saying so beats ignoring it.
        print(
            f"[bold red]Error: the {api_format} format has no model to "
            f"choose, so --model is not supported by it.[/bold red]"
        )
        exit(1)
    if options.block_size > 0:
        e.block_size = options.block_size
    if options.batch_flag:
        e.batch_flag = options.batch_flag
    if options.batch_use_flag:
        e.batch_use_flag = options.batch_use_flag

    if plan_auto:
        # the verdict is cached, so the first translation does not pay again
        try:
            mode, reason = resolve_plan_mode(
                book_type,
                api_format,
                translate_tags_given,
                getattr(e.translate_model, "_probe_verdict", None),
                session=can_session_classify(e.translate_model),
            )
        except Exception as err:
            # a model the endpoint will not serve is refused here; the
            # message is the whole explanation
            if not getattr(err, "user_facing", False):
                raise
            print(f"[bold red]{escape(redact(err))}[/bold red]")
            exit(1)
        if mode in ("model", "session"):
            # Both are the `model` classify mode as the loader knows it: an
            # LLM rules on the rows. Which channel carries the question is
            # the endpoint's business, settled again in classify_plan.
            print(f"plan mode: on ({reason})")
            e.plan_mode = True
            e.plan_auto = True
            e.plan_fallback_tags = options.translate_tags
            e.translate_tags = "auto"
            e.plan_classify = "model"
        else:
            print(f"plan mode: off ({reason})")

    try:
        e.make_bilingual_book()
    except PlanLedgerError as err:
        # The plan JSON is the one file this workflow asks a person (or an
        # agent) to hand-edit, so its lint errors are the failure a user is
        # most likely to see — print them like every other plan failure,
        # not as a traceback.
        print(f"[bold red]{escape(redact(err))}[/bold red]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
