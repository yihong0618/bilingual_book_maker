"""The run's extra endpoints: the image model and the classify model.

A run has one endpoint of its own (`--api_base`, `--model`, `--key`, or a
`--provider` entry). Two kinds of step may want another one (owner, 260923):

- the steps that look at a page image (today the PDF route's region-role
  pass) want a vision model, named by `--img-model` / `--img-base-url` /
  `--img-key` or the provider entry's `img_*` fields;
- every classification step (plan mode's unit classifier; the PDF route has
  no classification step yet) wants a classifier -- a JSON-schema-capable
  model, or a Jev-compatible one -- named by
  `--classify-model` / `--classify-base-url` / `--classify-key` or the entry's
  `classify_*` fields.

Resolution, one chain each:

    image     --img-model  ->  provider img_model       ->  off
    classify  --classify-model  ->  provider classify_model  ->  the run's own

The image chain never falls back to the run's model (owner ruling 260923
22:30): a vision step runs only when a model was designated for it, so no
default needs a model that can read images. `--img-model none` turns a
provider entry's image model off for one run.

A choice with no address of its own is the run's endpoint with another
model: the same format, base and key. A choice with its own address speaks
the format that address resolves to (`infer_api_format`); anything but the
OpenAI shape is refused before anything is paid for, because the image and
schema channels exist only there. A key is bound to an address (lead ruling
260923, Codex review): `--img-key` / `--classify-key`; else the run's key
when the choice calls the run's effective address (after any `--api_base`);
else the entry's key variable when the choice calls the address that entry
names; else what that address's format reads from the environment.

`jev` (TypeSafe's System One classifier) is a classify endpoint of its own
kind, and so is any server that speaks its wire format (packet J, 260924:
Featherless's Simple Jev, a gateway in front of TypeSafe). The same three
flags reach them all; `is_jev_wire` says which (model, base) pairs speak it,
and with no base the official endpoint is called. Key variables are read
implicitly only at the hosts that own them (`JEV_API_KEY` /
`TYPESAFE_API_KEY` at typesafe.ai, `FEATHERLESS_API_KEY` at featherless.ai);
the documented keyless Simple Jev demo takes none; at any other address (a
gateway) the key is passed explicitly.

Nothing here builds a network client at import, and nothing imports the CLI
at module level (the CLI imports this module).
"""

from dataclasses import dataclass, replace
from os import environ as env
from typing import Callable, Optional
from urllib.parse import urlparse

SOURCE_CLI = "cli"
SOURCE_PROVIDER = "provider"
SOURCE_RUN = "run"
SOURCE_OFF = "off"

# The lead's text, verbatim (packet F, 260923): the six flags' help.
HELP_IMG_MODEL = "Vision model for the steps that look at a page image (today: correcting the layout detector's region roles on the PDF route). Resolution: this flag, else the provider entry's img_model, else off; 'none' turns a provider entry's image model off. The run's own model is never used for images unless named here."
HELP_IMG_BASE_URL = "Endpoint for --img-model when it is not the run's endpoint (OpenAI-compatible only)."
HELP_IMG_KEY = "API key for --img-base-url. Default: the run's key when the endpoint is the run's own; else the provider entry's img_env_key when the endpoint is the entry's own; else the key the endpoint's format reads from the environment. A key is never sent to an address it was not given for."
HELP_CLASSIFY_MODEL = "Model for every classification step: plan mode's unit classifier (the PDF route has no classification step yet). Resolution: this flag, else the provider entry's classify_model, else the run's own model. --plan-classify-model is the old name of this flag. Asked over a JSON schema where its endpoint verifies one, else over a plain conversation. 'jev' asks TypeSafe's Jev classifier (default host api.typesafe.ai, key JEV_API_KEY); a Jev-compatible server such as Simple Jev is reached by its URL in --classify-base-url."
HELP_CLASSIFY_BASE_URL = "Base URL for --classify-model: an OpenAI-compatible endpoint, or a Jev-compatible classifier's URL (a path ending in /systemone or /classifier is used as is)."
HELP_CLASSIFY_KEY = "API key for --classify-base-url; same default rule as --img-key. A Jev host's own variable (JEV_API_KEY or TYPESAFE_API_KEY at typesafe.ai, FEATHERLESS_API_KEY at featherless.ai) is read only at that host."
# The lead's text, verbatim (packet H item 14, owner ruling 260924).
HELP_CLASSIFY_MIN_CONFIDENCE = "Confidence gate for a Jev-compatible classifier, 0 to 1: a 'skip' whose probability is below it becomes 'translate'; 'translate' is never gated. Default 0.95, measured 260924 on the epub3-samples corpus against gpt-5.6-luna with a lost skip costing ten extra translates. On two options the probability is never below 0.5, so a lower value turns the gate off. BBM_JEV_MIN_CONFIDENCE sets it for a run without the flag."

# The run's closing usage line for a classifier on an endpoint of its own
# (packet F: "Classifier ({model} at {base}): ..."); the image model's is
# `pipeline.messages.IMAGE_MODEL_USAGE`.
CLASSIFIER_USAGE = "Classifier ({model} at {base}): {summary}"

# The lead's text, verbatim (packet F, 260923).
IMG_ENDPOINT_UNSUPPORTED = (
    "--img-model needs an OpenAI-compatible endpoint; {base} resolves to the "
    "{api_format} format."
)
CLASSIFY_ENDPOINT_UNSUPPORTED = (
    "--classify-model needs an OpenAI-compatible endpoint; {base} resolves to "
    "the {api_format} format."
)
IMG_ENDPOINT_UNVERIFIED = (
    "{model} at {base} did not read the probe image ({verdict}); image steps "
    "are skipped this run."
)
# Not the lead's text: a base given without the model it is for.
IMG_BASE_WITHOUT_MODEL = (
    "--img-base-url names where --img-model is served, and no --img-model "
    "was given. Name the model too, or drop --img-base-url."
)
CLASSIFY_BASE_WITHOUT_MODEL = (
    "--classify-base-url names where --classify-model is served, and no "
    "--classify-model was given. Name the model too, or drop "
    "--classify-base-url."
)

IMG_OFF = "none"

# TypeSafe's System One models (docs.typesafe.ai/models, read 260923): the
# aliases `jev-latest` (-> jev-1.13.0) and `jev-preview`, and versioned ids
# such as `jev-1.13.0`, all at one endpoint. The literal `jev` means the
# default alias; any `jev-*` id, or a gateway's namespaced id whose last
# segment is one (`typesafe-ai/jev`), is passed through verbatim.
JEV_FORMAT = "jev"
JEV_ALIAS = "jev"
JEV_DEFAULT_MODEL = "jev-latest"
JEV_DEFAULT_BASE = "https://api.typesafe.ai"
JEV_HOST_SUFFIX = "typesafe.ai"
# Never BBM_API_KEY or a vendor's variable: those are translation keys, and
# sending one to TypeSafe would hand a credential to a host that never
# issued it. TYPESAFE_API_KEY is the name TypeSafe's own SDK reads.
JEV_ENV_KEYS = ("JEV_API_KEY", "TYPESAFE_API_KEY")
# The request path TypeSafe serves (docs.typesafe.ai/api, read 260924).
JEV_PATH = "/v1/systemone"
# Paths a base may already end at: the request is posted there verbatim.
JEV_WIRE_PATHS = ("/systemone", "/classifier")
# Featherless's Simple Jev (docs/260924-jev-alternative-format.md): an open
# reimplementation of the Jev interface at `https://api.featherless.ai/v1/
# classifier`, ids such as `featherless-ai/Qwen3.8-27B-classifier`, and a
# public demo host that needs no key.
FEATHERLESS_HOST_SUFFIX = "featherless.ai"
FEATHERLESS_ENV_KEYS = ("FEATHERLESS_API_KEY",)
SIMPLE_JEV_DEMO_HOST = "simple-jev-demo-api.featherless.ai"
# Where a `featherless-ai/...-classifier` id is asked when no base is given.
FEATHERLESS_NAMESPACE = "featherless-ai/"
FEATHERLESS_DEFAULT_BASE = "https://api.featherless.ai/v1/classifier"
# A `-classifier` id no default address is known for (lead-accepted 260924).
CLASSIFIER_WITHOUT_BASE = (
    "{model} is a Jev-compatible classifier with no known default address; "
    "name its endpoint with --classify-base-url."
)


@dataclass(frozen=True)
class EndpointChoice:
    """One endpoint as resolved: what to call, where, with which key.

    `source` is where the choice came from (cli, provider, run, off);
    `own_base` whether the address is the choice's own rather than the
    run's. `key` is None when the caller asked not to resolve one (a dry
    run, which needs no credentials).
    """

    model: str
    api_base: str
    key: str
    api_format: str
    source: str
    own_base: bool = False

    def where(self):
        """The address to print: the base, or the format's own host."""
        return self.api_base or f"the {self.api_format} endpoint's default host"

    def describe(self):
        return f"{self.model} at {self.where()} ({self.source})"


def run_choice(model, api_base, key, api_format):
    """The run's own endpoint, as the other two chains fall back to it."""
    return EndpointChoice(
        model=model or "",
        api_base=api_base or "",
        key=key,
        api_format=api_format,
        source=SOURCE_RUN,
    )


def _host(api_base):
    return (urlparse(api_base or "").hostname or "").lower()


def _host_in(api_base, suffix):
    """Whether `api_base`'s host is `suffix` or a subdomain of it."""
    host = _host(api_base)
    return host == suffix or host.endswith("." + suffix)


def _base_path(api_base):
    return (urlparse(api_base or "").path or "").rstrip("/").lower()


def _is_jev_id(model):
    """A TypeSafe Jev id: the last segment is `jev` or starts with `jev-`
    (a gateway namespaces it: `typesafe-ai/jev`)."""
    last = (model or "").strip().lower().rsplit("/", 1)[-1]
    return last == JEV_ALIAS or last.startswith(JEV_ALIAS + "-")


def _is_featherless_classifier_id(model):
    name = (model or "").strip().lower()
    return name.startswith(FEATHERLESS_NAMESPACE) and name.endswith("-classifier")


@dataclass(frozen=True)
class JevHost:
    """A host known to serve the Jev wire, and what is known about it.

    `host` is matched exactly, or with its subdomains when `subdomains`.
    `owns_id` picks the model ids whose default address is `default_base`.
    `path` is the request path appended after `/v1` (`jev_request_url`),
    `env_keys` the variables read implicitly for this host alone,
    `keyless` whether it takes no key at all, and `wire` whether the host
    alone makes a (model, base) pair Jev.
    """

    host: str
    subdomains: bool
    owns_id: Optional[Callable[[str], bool]]
    default_base: Optional[str]
    path: str
    env_keys: tuple
    keyless: bool
    wire: bool


# Most specific first: the keyless demo is a featherless.ai host, and any
# other featherless.ai address also serves an OpenAI-compatible chat API, so
# it is not Jev by its host alone (lead 260924).
JEV_HOSTS = (
    JevHost(SIMPLE_JEV_DEMO_HOST, False, None, None, "/classifier", (), True, True),
    JevHost(
        JEV_HOST_SUFFIX,
        True,
        _is_jev_id,
        JEV_DEFAULT_BASE,
        "/systemone",
        JEV_ENV_KEYS,
        False,
        True,
    ),
    JevHost(
        FEATHERLESS_HOST_SUFFIX,
        True,
        _is_featherless_classifier_id,
        FEATHERLESS_DEFAULT_BASE,
        "/classifier",
        FEATHERLESS_ENV_KEYS,
        False,
        False,
    ),
)


def _jev_host(api_base):
    """The `JEV_HOSTS` row `api_base` calls, or None."""
    host = _host(api_base)
    for row in JEV_HOSTS:
        if host == row.host or (row.subdomains and host.endswith("." + row.host)):
            return row
    return None


def is_jev_wire(model, api_base=""):
    """Whether (model, base) speaks the Jev wire format (packet J, 260924).

    The model id's last segment is `jev` or starts with `jev-` (a gateway
    namespaces it: `typesafe-ai/jev`), or the id ends with `-classifier`
    (Simple Jev's `featherless-ai/Qwen3.8-27B-classifier`); or the base's
    host is typesafe.ai (or a subdomain) or exactly the keyless Simple Jev
    demo; or the base's path ends at `/systemone` or `/classifier`. Any
    other featherless.ai address is not Jev by its host alone (lead 260924):
    Featherless also serves an OpenAI-compatible chat API there, so
    `--classify-base-url https://api.featherless.ai/v1` with a chat model
    stays a chat model.
    """
    if _is_jev_id(model) or (model or "").strip().lower().endswith("-classifier"):
        return True
    row = _jev_host(api_base)
    if row is not None and row.wire:
        return True
    return _base_path(api_base).endswith(JEV_WIRE_PATHS)


def jev_default_base(model):
    """The address a Jev-wire id is asked at when no base is given, or None.

    A `jev`/`jev-*` id (a gateway's `typesafe-ai/jev` included) is the
    official Jev; a `featherless-ai/...-classifier` id is Featherless's
    Simple Jev; any other `-classifier` id has no default (lead 260924,
    packet J fix round: it is never sent to typesafe.ai).
    """
    for row in JEV_HOSTS:
        if row.owns_id is not None and row.owns_id(model):
            return row.default_base
    return None


def jev_request_url(api_base):
    """Where a Jev-wire request is posted (lead 260924, packet J fix round).

    No base: the official endpoint. A base already ending at `/systemone`
    or `/classifier`: verbatim. Otherwise the server's own path is
    appended -- `/classifier` on a featherless.ai host (Simple Jev), else
    `/systemone` -- after `/v1`, which is added unless the base already
    ends there (`.../v1` -> `.../v1/classifier`, a bare host ->
    `/v1/classifier`).
    """
    base = (api_base or JEV_DEFAULT_BASE).strip().rstrip("/")
    path = _base_path(base)
    if path.endswith(JEV_WIRE_PATHS):
        return base
    row = _jev_host(base)
    tail = row.path if row is not None else "/systemone"
    return base + (tail if path.endswith("/v1") else "/v1" + tail)


def jev_env_keys(api_base):
    """The key variables read implicitly for a Jev-wire address: only the
    ones its host owns, none for the keyless demo or any other host."""
    row = _jev_host(api_base)
    return row.env_keys if row is not None else ()


def jev_keyless(api_base):
    """Whether the address is the documented keyless Simple Jev demo."""
    row = _jev_host(api_base)
    return row is not None and row.keyless


def _address(api_base, api_format):
    from book_maker.cli import _entry_address

    return _entry_address(api_base, api_format)


def _same_address(base_a, format_a, base_b, format_b):
    """Whether two (base, format) pairs call one host (see `_entry_address`).

    A written base is compared as the OpenAI shape normalises it (the
    `/chat/completions` tail is noise); an empty one stands for its
    format's own host."""
    from book_maker.cli import normalize_api_base

    def where(base, api_format):
        return _address(normalize_api_base(base, "openai") if base else "", api_format)

    return where(base_a, format_a) == where(base_b, format_b)


def _bound_env_key(provider, model, sidecar_base, env_key):
    """`(env_key, base, format)`: the entry's key variable and the address
    it belongs to, read from the entry alone -- its own `*_base_url`, else
    the address its model resolves to without one (jev's default host),
    else the entry's `base_url` (lead ruling 260923, Codex review: a key is
    bound to an address, and a run's `--api_base` does not move it)."""
    if not env_key or provider is None:
        return None
    from book_maker.cli import infer_api_format

    if sidecar_base:
        return env_key, sidecar_base, infer_api_format(sidecar_base, model)
    default = jev_default_base(model)
    if default:
        return env_key, default, JEV_FORMAT
    return env_key, provider.api_base, provider.api_format


def _key(explicit, bound, choice, run, with_key, flag):
    """The key for `choice`; never one resolved for another host.

    Lead ruling 260923 (Codex review, finding 1): the flag's key; else the
    run's key when the choice calls the run's *effective* address (after
    any `--api_base`); else the provider entry's key variable when the
    choice calls the address that variable belongs to (`bound`); else what
    the choice's format reads from the environment. For the Jev wire
    (finding 2, extended by packet J): `JEV_API_KEY` / `TYPESAFE_API_KEY`
    are read *implicitly* only at a typesafe.ai host and
    `FEATHERLESS_API_KEY` only at a featherless.ai host, and never the
    run's key; the keyless Simple Jev demo needs none (no header is sent);
    anywhere else the key is named explicitly -- by the flag, or by a
    provider entry whose `*_env_key` names the variable for the entry's own
    address (a gateway entry naming `JEV_API_KEY` for its gateway is that
    explicit naming, and is honoured: lead 260924, Codex re-verify).
    """
    if not with_key:
        return None
    if explicit:
        return explicit
    bound_here = bound is not None and _same_address(
        choice.api_base, choice.api_format, bound[1], bound[2]
    )
    if choice.api_format == JEV_FORMAT:
        names = ((bound[0],) if bound_here else ()) + jev_env_keys(choice.api_base)
        found = next((env[n] for n in names if env.get(n)), "")
        if found:
            return found
        if jev_keyless(choice.api_base):
            return ""
        where = (
            f"Pass {flag}, or set one of: {', '.join(names)}."
            if names
            else f"Pass {flag}: {choice.api_base} is not a typesafe.ai "
            f"address, so {' and '.join(JEV_ENV_KEYS)} are not sent there."
        )
        raise SystemExit(
            f"No API key for the jev classifier at {choice.api_base}. {where}"
        )
    if run.key and _same_address(
        choice.api_base, choice.api_format, run.api_base, run.api_format
    ):
        return run.key
    from book_maker.cli import resolve_api_key

    try:
        return resolve_api_key(
            choice.api_format,
            None,
            choice.api_base,
            (bound[0],) if bound_here else (),
        )
    except SystemExit as err:
        raise SystemExit(
            f"{err} For {choice.model} at {choice.where()}, pass {flag}."
        ) from None


def _choose(model, base, run, source, *, image):
    """The endpoint (format and base) for `model`, before the key."""
    from book_maker.cli import (
        FORMAT_DEFAULT_BASES,
        LLM_FORMATS,
        infer_api_format,
        normalize_api_base,
    )

    unsupported = IMG_ENDPOINT_UNSUPPORTED if image else CLASSIFY_ENDPOINT_UNSUPPORTED
    # An address of its own speaks the format it resolves to, jev included
    # (lead ruling 260923, Codex finding 2): a jev id at an Anthropic-shaped
    # address is refused like any other model there.
    api_format = infer_api_format(base, model) if base else None
    if base and api_format != "openai":
        raise SystemExit(unsupported.format(base=base, api_format=api_format))
    if is_jev_wire(model, base):
        if image:
            raise SystemExit(
                unsupported.format(base=base or JEV_DEFAULT_BASE, api_format=JEV_FORMAT)
            )
        if not base:
            base = jev_default_base(model)
            if base is None:
                raise SystemExit(CLASSIFIER_WITHOUT_BASE.format(model=model))
        if model.strip().lower() == JEV_ALIAS:
            model = JEV_DEFAULT_MODEL
        base, api_format, own = base.rstrip("/"), JEV_FORMAT, True
    elif base:
        base, own = normalize_api_base(base, api_format), True
    elif not image and run.api_format not in LLM_FORMATS:
        # A run on a fixed engine (google, deepl ...) has no model to ask, so
        # a classify model named without an address speaks the format its id
        # implies, at that format's own host (lead ruling 260923, Codex
        # finding 4: `--api_format google --classify-model gpt-5.6-luna`
        # classifies on OpenAI). Only the OpenAI shape carries the schema
        # channel.
        api_format = infer_api_format("", model)
        base = FORMAT_DEFAULT_BASES.get(api_format, "")
        if api_format != "openai":
            raise SystemExit(
                unsupported.format(
                    base=base or f"the {api_format} endpoint", api_format=api_format
                )
            )
        own = True
    else:
        # The run's endpoint with another model. Its format has to have the
        # channel the step asks through: images only on the OpenAI shape;
        # classification on any route that can be asked a question.
        if image and not _reads_images(run.api_format):
            raise SystemExit(
                unsupported.format(base=run.where(), api_format=run.api_format)
            )
        base, api_format, own = run.api_base, run.api_format, False
    return EndpointChoice(
        model=model,
        api_base=base,
        key=None,
        api_format=api_format,
        source=source,
        own_base=own,
    )


def _reads_images(api_format):
    from book_maker.translator import FORMAT_DICT

    cls = FORMAT_DICT.get(api_format)
    return cls is not None and hasattr(cls, "structured_json_with_image")


def resolve_image_endpoint(options, run, provider, *, with_key=True):
    """The image endpoint, or None when image steps are off.

    `options` carries `img_model`, `img_base_url`, `img_key`; `run` is the
    run's `EndpointChoice`; `provider` the `ProviderRoute` or None.
    """
    return _resolve("img", options, run, provider, with_key)


def resolve_classify_endpoint(options, run, provider, *, with_key=True):
    """The classify endpoint: cli, else the provider entry, else the run's.

    Never None: the run's own translator is the last link, as it always
    was. `options.classify_model` is either spelling of the flag
    (`--plan-classify-model` is the old name).
    """
    return _resolve("classify", options, run, provider, with_key)


def _resolve(kind, options, run, provider, with_key):
    """One chain (`kind` "img" or "classify"): the `--{kind}-*` flags, else
    the provider entry's `{kind}_*` fields, else off (img) or the run's own
    endpoint (classify)."""
    image = kind == "img"
    model = (getattr(options, f"{kind}_model", None) or "").strip()
    base = (getattr(options, f"{kind}_base_url", None) or "").strip()
    explicit_key = getattr(options, f"{kind}_key", None) or ""
    if image and model.lower() == IMG_OFF:
        return None
    if base and not model:
        raise SystemExit(
            IMG_BASE_WITHOUT_MODEL if image else CLASSIFY_BASE_WITHOUT_MODEL
        )
    bound = None
    if model:
        source = SOURCE_CLI
    elif provider is not None and getattr(provider, f"{kind}_model"):
        model = getattr(provider, f"{kind}_model")
        base = getattr(provider, f"{kind}_base_url")
        env_key = getattr(provider, f"{kind}_env_key")
        bound = _bound_env_key(provider, model, base, env_key)
        source = SOURCE_PROVIDER
    else:
        return None if image else run
    choice = _choose(model, base, run, source, image=image)
    key = _key(explicit_key, bound, choice, run, with_key, f"--{kind}-key")
    return replace(choice, key=key)


def request_extras(options):
    """`{"extra_body": {...}, "extra_headers": {...}}` from the run's
    `--extra_body` / `--extra_headers`, each a JSON object; SystemExit
    naming the flag for anything else (bad JSON, not an object, a header
    value that is not a string)."""
    import json

    extras = {}
    for flag, dest in (
        ("--extra_body", "extra_body"),
        ("--extra_headers", "extra_headers"),
    ):
        raw = getattr(options, dest, None)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as ex:
            raise SystemExit(f"invalid JSON in {flag}: {ex}")
        if not isinstance(parsed, dict):
            # A list or a bare string would be accepted by the SDK and
            # rejected by the endpoint, one paid request later.
            raise SystemExit(
                f"{flag} must be a JSON object, not {type(parsed).__name__}."
            )
        extras[dest] = parsed
    if "extra_headers" in extras and not all(
        isinstance(v, str) for v in extras["extra_headers"].values()
    ):
        # httpx raises on a non-string header value, deep in the first
        # request rather than here.
        raise SystemExit("--extra_headers values must all be strings.")
    return extras


def apply_run_extras(translator, options, *, extras=True):
    """The run's request options on `translator`, each where the route
    carries it: the price table (the bar shows what was spent), `--no-
    thinking`, and, with `extras`, `--extra_body` / `--extra_headers`
    (`request_extras`, whose SystemExit propagates). Returns the extras
    set, {} when none were."""
    prices = getattr(options, "price_table", None)
    if prices is not None and hasattr(translator, "usage"):
        translator.usage.prices = prices
    carries = getattr(translator, "SUPPORTS_REQUEST_EXTRAS", False)
    if getattr(options, "no_thinking", False) and carries:
        translator.no_thinking = True
    if not (extras and carries):
        return {}
    found = request_extras(options)
    if found:
        translator.set_request_extras(**found)
    return found


def build_translator(choice, options, language, prompt_config=None):
    """A translator instance for `choice`, built the way the loaders build one.

    The same constructor arguments a loader passes that matter outside
    translation (temperature, source language, the prompt sections, the
    session budget a classifier conversation rolls over at), the model list
    of the one model, and the run's request options (`apply_run_extras`):
    its prices and `--no-thinking` where the route carries it, and
    `--extra_body` / `--extra_headers` only on the run's own address: a
    header block is where a gateway's credential goes, and it must not
    travel to another host.
    """
    from book_maker.translator import FORMAT_DICT
    from book_maker.utils import prompt_config_to_kwargs

    cls = FORMAT_DICT[choice.api_format]
    translator = cls(
        choice.key or "",
        language,
        api_base=choice.api_base or None,
        context_compact_at=getattr(options, "context_compact_at", None),
        no_context_compact=getattr(options, "no_context_compact", False),
        temperature=getattr(options, "temperature", 1.0),
        source_lang=getattr(options, "source_lang", "auto"),
        **prompt_config_to_kwargs(prompt_config),
    )
    apply_run_extras(translator, options, extras=not choice.own_base)
    if getattr(options, "quiet", False) and hasattr(translator, "quiet"):
        translator.quiet = True
    if choice.model:
        translator.set_model_list([choice.model])
    return translator


def build_classifier(choice, run_translator, options, language, prompt_config=None):
    """The run's `Classifier`, from its classify choice.

    The run's own choice asks the run's translator, as plan mode always did.
    A named model gets a translator of its own (`build_translator`), so its
    requests are metered apart and reported on their own line. `jev` has
    only its own backend.
    """
    from book_maker.classifier import Classifier, JevBackend

    if choice is None or choice.source == SOURCE_RUN:
        return Classifier(
            run_translator,
            None,
            source=SOURCE_RUN,
            base=getattr(choice, "api_base", None) or None,
        )
    if choice.api_format == JEV_FORMAT:
        return Classifier(
            None,
            choice.model,
            backends=[
                JevBackend(
                    choice.model,
                    choice.key,
                    choice.api_base,
                    min_confidence=getattr(options, "classify_min_confidence", None),
                )
            ],
            source=choice.source,
            base=choice.api_base,
            separate=True,
        )
    translator = build_translator(choice, options, language, prompt_config)
    return Classifier(
        translator,
        choice.model or None,
        source=choice.source,
        base=choice.api_base or None,
        separate=True,
    )
