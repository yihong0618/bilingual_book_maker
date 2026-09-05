import itertools
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from rich import print
from rich.markup import escape

from ..redaction import redact, remember

from ..structured import (
    extract_json_object,
    prompt_with_schema,
    run_rungs,
    schema_required_keys,
    unwrap_schema_echo,
)

# Special delimiter for batch translation - UUID-based token unlikely to appear in any text
BATCH_DELIMITER = "\n\n@@\n\n"


class BatchMismatch(Exception):
    """A batch reply cannot be aligned with the texts that were sent.

    The one contract every LLM route's `translate_list` keeps: exactly
    `len(texts)` aligned items, or this. Nobody repairs it where it is
    raised — the loader's `_translate_texts_aligned` ladder halves the chunk
    and asks again, which costs about twice the batch instead of the N
    single requests a per-route fallback used to pay. Retrying the same
    group is not among the options either: a model that miscounted once
    usually miscounts again, and each retry re-pays the whole group.
    """


def short_count(n):
    """12345 -> '12.3k', 1234567 -> '1.23M': a progress bar has no room for digits."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.2f}M"


def _count(value):
    """A token count as an int; anything a gateway sends that is not one is 0.

    The meter is a readout: a field reported as a string, None or a nested
    object costs the number on the bar, never the run.
    """
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CNY": "¥", "JPY": "¥"}


class PriceTable:
    """A provider entry's `prices`: what a model charges per million tokens.

    Looked up by the model id the run asked for: exactly, then by the part
    after a router's `vendor/` prefix, then by the longest listed id the
    model's name starts with — so `gpt-5.6-luna-2026-07-30` is priced as
    `gpt-5.6-luna`. A model none of that finds has no price, and the meter
    says so instead of guessing.
    """

    def __init__(self, prices, currency="USD"):
        self.prices = dict(prices or {})
        self.currency = currency or "USD"

    def price_for(self, model):
        if not model:
            return None
        if model in self.prices:
            return self.prices[model]
        tail = model.rsplit("/", 1)[-1]
        if tail in self.prices:
            return self.prices[tail]
        prefixes = [key for key in self.prices if model.startswith(key)]
        if prefixes:
            return self.prices[max(prefixes, key=len)]
        return None

    def cost(self, model, prompt=0, completion=0, cached=0):
        """What one request cost, or None when its model has no price."""
        price = self.price_for(model)
        if price is None:
            return None
        cached = min(cached or 0, prompt or 0)
        cached_rate = price.get("cached_input", price["input"])
        return (
            (prompt - cached) * price["input"]
            + cached * cached_rate
            + (completion or 0) * price["output"]
        ) / 1_000_000

    def money(self, amount):
        symbol = CURRENCY_SYMBOLS.get(self.currency.upper())
        digits = 4 if amount < 0.01 else 3 if amount < 1 else 2
        figure = f"{amount:.{digits}f}"
        return f"{symbol}{figure}" if symbol else f"{figure} {self.currency}"


class UsageMeter:
    """Tokens billed so far, summed over every request that reported them.

    With a `PriceTable` in `prices` the meter also keeps what those tokens
    cost, and shows that instead: a number the operator can weigh against
    the book. A request for a model the table does not price makes the
    cost unknowable, so the display falls back to tokens and the summary
    names the model.

    `cached` is the part of `prompt` the endpoint read back from its cache.
    That is the number session mode lives on: a history that is re-read at
    full price every request costs more than window mode, and nothing in
    the output says so — only this does. It is shown on the progress bar
    rather than judged: whether the ratio is worth it is the operator's
    call, made while watching.
    """

    def __init__(self):
        self.prompt = 0
        self.completion = 0
        self.cached = 0
        self.requests = 0
        self.prices = None
        self.spent = 0.0
        self.unpriced = set()
        self._lock = threading.Lock()

    def note(self, prompt=0, completion=0, cached=0, model=None):
        prompt, completion, cached = _count(prompt), _count(completion), _count(cached)
        with self._lock:
            self.prompt += prompt
            self.completion += completion
            self.cached += cached
            self.requests += 1
            if self.prices is not None:
                cost = self.prices.cost(model, prompt, completion, cached)
                if cost is None:
                    self.unpriced.add(model or "(unnamed model)")
                else:
                    self.spent += cost

    def _priced(self):
        return self.prices is not None and not self.unpriced

    def postfix(self):
        """What the progress bar shows — nothing until a request reported usage."""
        if not self.requests:
            return None
        if self._priced():
            return {"spent": self.prices.money(self.spent)}
        return {
            "in": short_count(self.prompt),
            "out": short_count(self.completion),
            "cached": short_count(self.cached),
        }

    def summary(self):
        if not self.requests:
            return None
        tokens = (
            f"in {short_count(self.prompt)}, out "
            f"{short_count(self.completion)}, cached {short_count(self.cached)} "
            f"({self.requests} request{'s' if self.requests != 1 else ''})"
        )
        if self._priced():
            return f"spent {self.prices.money(self.spent)} — tokens: {tokens}"
        line = f"tokens: {tokens}"
        if self.unpriced:
            line += (
                f"; no price for {', '.join(sorted(self.unpriced))} in the "
                f"provider entry, so spent is not shown"
            )
        return line


class AsyncTranslationUnsupported(NotImplementedError):
    """Raised when a provider has no native asynchronous implementation."""


@dataclass(frozen=True)
class TranslationContext:
    """Immutable translation history passed explicitly between requests."""

    source_texts: tuple[str, ...] = ()
    translated_texts: tuple[str, ...] = ()

    def __post_init__(self):
        if len(self.source_texts) != len(self.translated_texts):
            raise ValueError("Source and translated context lengths must match")

    def append(
        self, source_text: str, translated_text: str, limit: int
    ) -> "TranslationContext":
        if limit <= 0:
            return self
        return type(self)(
            (self.source_texts + (source_text,))[-limit:],
            (self.translated_texts + (translated_text,))[-limit:],
        )


@dataclass(frozen=True)
class TranslationResult:
    text: str
    context: TranslationContext


@dataclass(frozen=True)
class BatchTranslationResult:
    texts: tuple[str, ...]
    context: TranslationContext


def service_name(translator):
    """The `--api_format` or `--provider` key a translator is registered
    under, or its class name for one registered nowhere.

    Imported lazily: the registries live in the package `__init__`, which
    imports every translator, which imports this module.
    """
    from . import FORMAT_DICT, ROUTE_DICT

    cls = type(translator)
    for registry in (FORMAT_DICT, ROUTE_DICT):
        for key, registered in registry.items():
            if registered is cls:
                return key
    return cls.__name__


class Base(ABC):
    # Default values for fatal error handling - subclasses can override
    TRANSLATION_ERROR_MARKER = None

    # Does this format implement `--use_context session` — one append-only
    # history, compacted into a handoff report at --context-compact-at? A
    # format that does not gets the flag refused rather than accepting it
    # and translating as if it had never been passed.
    SUPPORTS_SESSION_CONTEXT = False

    # Is this format a session whether or not anyone asked for one? True
    # where the route has no other shape: the codex sidecar's thread IS the
    # history, so a run on it is billed the way a session run is billed —
    # by request count against a growing conversation — and the budgets a
    # session run derives have to be derived there too.
    SESSION_CONTEXT_ALWAYS_ON = False

    # Does this format survive `--parallel-workers` with `--use_context`?
    # Each worker is handed a clone carrying its own chapter context, which
    # a format that keeps no re-sendable window cannot provide.
    SUPPORTS_PARALLEL_CONTEXT = False

    # Whether a system message can be borrowed for the length of one request.
    # False where it is not sent per request at all: the codex route folds it
    # into a thread's base instructions when the thread opens, and the thread
    # outlives the window, so a batch system message set there would not
    # describe one group of paragraphs — it would tell every later unit to
    # come back in "@@"-separated segments. Those routes carry the batch
    # contract in the prompt instead, which does ride with the request.
    BATCH_SYS_MSG_PER_REQUEST = True

    # Whether a batch's context is recorded as one pair per line. Window-style
    # context wants that, for the reason `_do_batch_translate` gives. A history
    # replayed verbatim wants the opposite: a grouped request was one exchange,
    # and recording it as several pairs leaves the history no longer matching
    # what was sent, which is a broken cache prefix.
    BATCH_CONTEXT_PER_LINE = True

    # Does this route implement the OpenAI Batch API — `--batch` to submit a
    # book and `--batch-use` to collect it? Only the OpenAI translator does.
    # A route that does not gets the flag refused: the loader calls
    # batch_init/add_to_batch_translate_queue/is_completed_batch on the
    # translator, so accepting it would be an AttributeError partway in.
    SUPPORTS_BATCH_API = False

    # Does this route carry --extra_body / --extra_headers? True where the
    # request is built here from an SDK that takes them. The codex route is
    # a subprocess with no request to merge into, and the fixed-endpoint MT
    # services speak their own protocol; both refuse the flags rather than
    # printing success and dropping the fields.
    SUPPORTS_REQUEST_EXTRAS = False

    def set_request_extras(self, extra_body=None, extra_headers=None):
        """Fields and headers to add to every request this route makes.

        Overridden where `SUPPORTS_REQUEST_EXTRAS` is True. The base refuses
        rather than storing attributes nothing reads.
        """
        raise NotImplementedError(
            f"the {service_name(self)} route builds no request these could "
            f"be added to"
        )

    # --extra_body / --extra_headers, rebound by `set_request_extras` and
    # never mutated in place. Class-level so a route that skipped __init__
    # still answers, and so the default is stated once.
    extra_body = {}
    extra_headers = {}

    # The source half of `--language src:tgt`, when one was given. Class-level
    # so every route answers, including the ones a test builds without
    # __init__; None means "the model works it out from the text", which is
    # what every run did before the pair form existed.
    source_language = None

    # Said only to requests that carry markers. A model told to preserve
    # tokens in a text that has none is being taught to invent them.
    MARKER_INSTRUCTION = (
        "The text contains placeholder tokens written like ⟦code1⟧. Reproduce "
        "every one of them exactly as written, each in the place the content "
        "it stands for belongs in your translation. Never translate a token, "
        "never change its spelling, and never invent one."
    )

    def _source_language_note(self):
        """The sentence that names the source language, or ""."""
        if not self.source_language:
            return ""
        return (
            f"Translate from {self.source_language} into "
            f"{self.language or 'the target language'}."
        )

    @staticmethod
    def _carries_markers(text):
        # Imported here: `book_maker.loader` pulls in the loaders, which
        # import this module.
        from ..loader.markers import MARKER_RE

        return bool(MARKER_RE.search(text or ""))

    def _augment_system_content(self, sys_content):
        """The system message plus what is true for the whole run.

        Only the source-language note, which `--language src:tgt` fixes once
        and every request then repeats verbatim. Nothing per-request may go
        here: a system message that changes between requests moves the
        prefix session mode caches, and every later request re-reads the
        whole accumulated history at full input price. The marker contract
        is exactly such a per-request thing — it rides in the user message,
        see `_marker_preamble`.
        """
        note = self._source_language_note()
        if not note:
            return sys_content
        return " ".join([(sys_content or "").strip(), note]).strip()

    def _marker_preamble(self, request_text):
        """The marker contract as a user-message prefix, or "".

        Said only to requests that carry markers, and said in the user turn
        so the system message stays byte-identical across a run that mixes
        marker-bearing and plain units.
        """
        if not self._carries_markers(request_text):
            return ""
        return f"{self.MARKER_INSTRUCTION}\n\n"

    def warn_if_extras_refused(self, error):
        """Say so when a request carrying the run's extras was refused.

        The structured ladder answers a refusal by demoting to the next rung
        and carrying on, which is right when the endpoint simply does not do
        schemas — and wrong when the operator's own `--extra_body` or
        `--extra_headers` is what the endpoint objected to, because then the
        run degrades quietly and the endpoint's own words are never seen.
        """
        if not (self.extra_body or self.extra_headers):
            return
        message = redact(error)
        print(
            f"[bold yellow]Warning:[/bold yellow] the endpoint refused a "
            f"request carrying your --extra_body/--extra_headers, and the "
            f"run fell back to a simpler request shape. It said: "
            f"{escape(message)}"
        )

    # Refusals of one rung, by one model, before we stop offering it.
    RUNG_REFUSAL_THRESHOLD = 2

    def __init__(self, key, language) -> None:
        # Registered before the first request: an endpoint that refuses a key
        # routinely quotes it back, and this run prints an endpoint's words
        # in several places.
        remember(*key.split(","))
        self.keys = itertools.cycle(key.split(","))
        self.language = language
        self._fatal_error_detected = False
        # {model: {rung name: refusal count}} — see _retire_rung. Dict writes
        # are atomic under the GIL, which is all the synchronization this
        # needs: the worst a race costs is one repeated rejection, never a
        # wrong answer.
        self._rung_refusals = {}
        # Filled by the translators that get usage back from the endpoint;
        # the loader reads it for the progress bar and the closing line.
        self.usage = UsageMeter()

    @property
    def model_name(self):
        """The model id this translator runs with, for the record an output
        file keeps of how it was made.

        Every LLM translator here settles on `self.model` — the endpoint
        needs a model id to call, so the attribute is not optional for
        them. The ones that call a single fixed service (Google, DeepL,
        Caiyun, TranSmart) have no model to name, and the name the service
        is selected by — its `--api_format` or `--provider` key — is the
        honest answer: it says which service, and claims no more.
        """
        return getattr(self, "model", None) or service_name(self)

    def usage_postfix(self):
        return self.usage.postfix()

    def usage_summary(self):
        return self.usage.summary()

    @abstractmethod
    def rotate_key(self):
        pass

    @abstractmethod
    def translate(self, text):
        pass

    # ---------------------------------------------------------------- JSON
    # Plan classification asks a translator one structured question. It needs
    # a JSON object carrying legal values — not an endpoint that honors the
    # `json_schema` request field. So the bottom rung, defined here, is a
    # plain prompt: every LLM-backed translator can classify, whatever its
    # provider supports.

    def _chat_completion(self, prompt, model=None):
        """Answer one arbitrary prompt in one turn; return the raw text.

        The single primitive classification needs. Translators that speak to
        an LLM implement it and get classification for free. Dedicated MT
        engines (google, deepl, caiyun, tencent transmart, qwen-mt, a custom
        translate endpoint) cannot: their only channel *translates* what it is
        handed instead of answering it, so asking would return a translated
        copy of the question. They keep failing loudly instead.
        """
        raise NotImplementedError(
            f"{type(self).__name__} has no arbitrary-prompt channel"
        )

    def classify_session(self, model=None):
        """A fresh conversation for plan classification, or None.

        The session entry (`loader/classify/session.py`) asks for verdicts
        over an append-only conversation instead of asking for JSON, which
        is the only way to classify on a route that produces none. It needs
        three things a single-turn prompt channel cannot give it: a place to
        put the instruction trunk once, turns that extend that prefix rather
        than replacing it, and the budget at which the history is worth
        starting over.

        A route that has no such conversation answers None and keeps the
        JSON path. Overriding this method is also what advertises the
        capability — `can_session_classify` compares the attribute with this
        one rather than calling it, because opening a session can cost a
        request and the question is asked before plan mode spends anything.

        The returned object implements:

            start(trunk)  begin a new conversation carrying `trunk`
            ask(text)     one turn; returns the reply text
            budget()      estimated tokens a session may carry
        """
        return None

    def supports_structured_json(self):
        """Whether this translator can be asked a question at all.

        Derived from the implementation rather than a per-class flag, so a new
        translator cannot advertise a capability it never implemented, or
        implement one it forgot to advertise. Either half counts: the prompt
        primitive, or a `structured_json` of the provider's own.
        """
        cls = type(self)
        return (
            cls._chat_completion is not Base._chat_completion
            or cls.structured_json is not Base.structured_json
        )

    def structured_rungs(self, prompt, schema, model=None):
        """(name, callable) pairs, most constrained first.

        Providers prepend their native structured-output mechanisms; the
        prompt rung is the floor and is always last.
        """
        return [("prompt", lambda: self._prompt_rung(prompt, schema, model))]

    def _prompt_rung(self, prompt, schema, model=None):
        """Schema described in the prompt, answer recovered from free text."""
        text = self._chat_completion(prompt_with_schema(prompt, schema), model=model)
        return unwrap_schema_echo(
            extract_json_object(text, schema_required_keys(schema))
        )

    def structured_json(self, prompt, schema, model=None, accept=None):
        """One structured question, over whatever rungs this provider has.

        Returns the first object `accept` approves, else the last object any
        rung parsed (the caller lints it: a partial answer beats a discarded
        page), else raises `StructuredJSONFailed`. Value constraints are never
        guaranteed — the caller owns validation.
        """
        target = model or getattr(self, "model", None)
        rungs = self.structured_rungs(prompt, schema, model)
        refusals = self._rung_refusals.get(target, {})
        live = [
            (name, rung)
            for name, rung in rungs
            if refusals.get(name, 0) < self.RUNG_REFUSAL_THRESHOLD
        ]
        # The floor is always asked. Callers divide a failed request and ask
        # again with less; handing them an empty ladder would answer that
        # smaller request without making a single call.
        floor = rungs[-1] if rungs else None
        if not live and floor is not None:
            live = [floor]
        return run_rungs(
            live,
            accept=accept,
            on_reject=lambda name: self._retire_rung(name, target, floor[0]),
        )

    def _retire_rung(self, name, model, floor):
        """Stop paying for a rung the endpoint keeps refusing.

        Two deliberate limits on this:

        - **Never the floor.** A plain prompt has no simpler form to fall
          back to, and an endpoint refusing one is not saying anything about
          our schema.
        - **Never on the first refusal.** A 400 is as often about *this
          request* — too long, too many properties — as about the shape, and
          those are exactly the failures the caller recovers from by dividing
          and asking again. Retiring on one refusal would confiscate the rung
          the retry needs. Same threshold idiom as the translation-side
          demotion in `chatgptapi_translator`.
        """
        if name == floor:
            return
        counts = self._rung_refusals.setdefault(model, {})
        counts[name] = counts.get(name, 0) + 1
        if counts[name] == self.RUNG_REFUSAL_THRESHOLD:
            print(
                f"[yellow]ℹ '{model}' keeps refusing the {name} request "
                f"shape; using a simpler one from here on[/yellow]"
            )

    def translate_list(self, text_list):
        """
        Translate a list of texts. Default implementation translates one by one.
        Subclasses can override for batch efficiency.
        """
        return [self.translate(t) for t in text_list]

    @staticmethod
    def _check_batch(texts, replies):
        """Raise `BatchMismatch` unless `replies` aligns with `texts`.

        Two symptoms, one check, used by every carrier:

        *Wrong count* — the reply cannot be zipped with the source at all.

        *An empty slot for a non-empty source* — count is not alignment. A
        model that merges two source lines into one slot (routine on verse:
        one sentence spans two pādas) keeps the count by shifting the rest
        and padding with "", and that empty slot is the only unambiguous
        symptom of the shift.

        A wrong alignment at the right count with no empty slot is not
        detected, by design: there is no signal to detect it by.
        """
        if len(replies) != len(texts):
            raise BatchMismatch(
                f"expected {len(texts)} translations, got {len(replies)}"
            )
        empty = [
            i
            for i, (src, out) in enumerate(zip(texts, replies))
            if not str(out).strip() and str(src).strip()
        ]
        if empty:
            raise BatchMismatch(
                f"empty translation for non-empty paragraph(s) {empty}: "
                f"batch alignment lost"
            )
        return None

    async def translate_async(
        self, text: str, *, context: TranslationContext | None = None
    ) -> TranslationResult:
        raise AsyncTranslationUnsupported(
            f"{type(self).__name__} does not implement native async translation"
        )

    async def translate_list_async(
        self,
        text_list: Sequence[str],
        *,
        context: TranslationContext | None = None,
    ) -> BatchTranslationResult:
        """Translate a list sequentially while threading explicit context."""
        current_context = context or TranslationContext()
        translations = []
        for text in text_list:
            result = await self.translate_async(text, context=current_context)
            translations.append(result.text)
            current_context = result.context
        return BatchTranslationResult(tuple(translations), current_context)

    async def close_async(self) -> None:
        """Release provider-specific asynchronous resources."""

    def _build_batch_prompt(
        self, text_list, prompt_template, system_content, default_prompt
    ):
        """
        Build batch translation prompt and system message.

        Args:
            text_list: List of texts to translate
            prompt_template: User's custom prompt template (can be None)
            system_content: User's custom system message (can be None)
            default_prompt: Default prompt template to use if prompt_template is None

        Returns:
            Tuple of (batch_prompt, batch_sys_msg, batch_text)
        """
        plist_len = len(text_list)
        if plist_len == 0:
            return None, None, None

        if plist_len == 1:
            return None, None, None  # Signal to use single translation

        # Build stripped texts list once
        stripped_texts = [str(t).strip() for t in text_list]
        batch_text = BATCH_DELIMITER.join(stripped_texts)

        # Build batch instruction
        batch_instruction = (
            f"Translate the following {plist_len} text segments to {{language}}. "
            f"Separate each translation with '{BATCH_DELIMITER}'. "
            f"Output EXACTLY {plist_len} translations.\n\n"
        )

        # Use the user's custom prompt template, or fall back to default
        user_prompt = prompt_template if prompt_template else default_prompt
        batch_prompt = batch_instruction + user_prompt

        # Preserve user's system message, adding batch-specific context
        if system_content:
            batch_sys_msg = (
                f"{system_content} Input has {plist_len} segments separated by '{BATCH_DELIMITER}'. "
                f"Output {plist_len} translations with '{BATCH_DELIMITER}' between each."
            )
        else:
            batch_sys_msg = (
                f"Professional translator. Input has {plist_len} segments separated by '{BATCH_DELIMITER}'. "
                f"Output {plist_len} translations with '{BATCH_DELIMITER}' between each."
            )

        return batch_prompt, batch_sys_msg, batch_text

    def _extract_paragraphs(self, text, paragraph_count):
        """
        Extract paragraphs from translated text, ensuring paragraph count is preserved.

        Args:
            text: Translated text containing multiple paragraphs
            paragraph_count: Expected number of paragraphs

        Returns:
            List of extracted paragraphs
        """
        result_list = []

        # First try to extract by paragraph numbers (1), (2), etc.
        for i in range(1, paragraph_count + 1):
            pattern = rf"\({i}\)\s*(.*?)(?=\s*\({i + 1}\)|\Z)"
            match = re.search(pattern, text, re.DOTALL)
            if match:
                result_list.append(match.group(1).strip())

        # If exact pattern matching failed, try another approach
        if len(result_list) != paragraph_count:
            pattern = r"\((\d+)\)\s*(.*?)(?=\s*\(\d+\)|\Z)"
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                # Sort by paragraph number
                matches.sort(key=lambda x: int(x[0]))
                result_list = [match[1].strip() for match in matches]

        # Fallback: try splitting by BATCH_DELIMITER with flexible whitespace
        if len(result_list) != paragraph_count:
            # Extract the core delimiter (e.g., '@@' from BATCH_DELIMITER)
            core_delimiter = BATCH_DELIMITER.strip()
            # Split by the core delimiter with any surrounding whitespace/newlines
            parts = re.split(r"\s*" + re.escape(core_delimiter) + r"\s*", text)
            # Filter out empty strings
            result_list = [p.strip() for p in parts if p.strip()]

        # There used to be a last rung here: split on every non-blank line.
        # It manufactured a plausible *wrong* count out of a perfectly correct
        # multi-line reply — a four-line stanza answered as one paragraph of
        # four lines came back as four "translations", each a fragment of the
        # first source line. A reply carrying no delimiter now yields one
        # item, which is a mismatch, which the loader's ladder divides.
        return result_list

    def _do_batch_translate(
        self, text_list, prompt_template, system_content, default_prompt, translate_func
    ):
        """
        Send one delimiter-separated request for a whole group.

        Args:
            text_list: List of texts to translate
            prompt_template: User's custom prompt template
            system_content: User's custom system message
            default_prompt: Default prompt template
            translate_func: Function to call for actual translation (single or batch)

        Returns:
            List of exactly `len(text_list)` translations.

        Raises:
            BatchMismatch: the reply did not come back in `len(text_list)`
                aligned pieces. Nothing is repaired here — the loader's
                ladder halves the chunk and asks again, which is one place
                instead of one per route, and costs ~2x the batch rather
                than N singles.
        """
        plist_len = len(text_list)

        if plist_len == 0:
            return []

        if plist_len == 1:
            return [translate_func(str(text_list[0]).strip())]

        # Build batch prompt
        batch_prompt, batch_sys_msg, batch_text = self._build_batch_prompt(
            text_list, prompt_template, system_content, default_prompt
        )

        # Store original values
        original_prompt = prompt_template
        original_sys_msg = system_content

        # Detect which attribute names this translator uses
        # ChatGPT uses prompt_template/system_content, Gemini uses prompt/prompt_sys_msg
        prompt_attr = (
            "prompt_template" if hasattr(self, "prompt_template") else "prompt"
        )
        sys_msg_attr = (
            "system_content" if hasattr(self, "system_content") else "prompt_sys_msg"
        )

        # --use_context must see one pair per paragraph. translate() saves
        # whatever it was handed, so letting it run on the joined batch would
        # store a single entry full of "@@" markers and evict three real
        # paragraphs of context. Suppress it here; save per pair on success,
        # the way the structured batch path already does.
        #
        # A history that is replayed verbatim rather than windowed wants the
        # joined exchange saved exactly as it was sent, and says so through
        # BATCH_CONTEXT_PER_LINE; there translate() is left to do its own
        # saving and nothing is suppressed.
        context_flag = getattr(self, "context_flag", False)
        per_line = context_flag and self.BATCH_CONTEXT_PER_LINE
        # A replayed history records the batch as the one exchange it was —
        # but only once the reply is known to be usable. A misaligned
        # exchange left in the prefix is worse than the cache miss its
        # absence costs, so the recording happens here, after the check,
        # while the batch prompt is still installed: `_save_session_context`
        # re-derives the user content from it and must derive exactly what
        # was sent.
        session_batch = (
            context_flag
            and not self.BATCH_CONTEXT_PER_LINE
            and getattr(self, "session", None) is not None
        )
        translated_paragraphs = None

        try:
            # Set batch values
            setattr(self, prompt_attr, batch_prompt)
            if (
                batch_sys_msg
                and self.BATCH_SYS_MSG_PER_REQUEST
                and hasattr(self, sys_msg_attr)
            ):
                setattr(self, sys_msg_attr, batch_sys_msg)
            if per_line or session_batch:
                self.context_flag = False

            translated_text = translate_func(batch_text)
            if translated_text:
                translated_paragraphs = self._extract_paragraphs(
                    translated_text, plist_len
                )
                self._check_batch(text_list, translated_paragraphs)
                if session_batch:
                    self._save_session_context(batch_text, translated_text)
        finally:
            # Restore original values
            setattr(self, prompt_attr, original_prompt)
            # Restored even when it was None. A translator that carries no
            # system message of its own — codex, whose voice lives in the
            # thread instructions — would otherwise keep the batch one, and
            # describe "@@"-separated segments to every later request.
            if hasattr(self, sys_msg_attr):
                setattr(self, sys_msg_attr, original_sys_msg)
            if per_line or session_batch:
                self.context_flag = True

        # Handle None or empty response
        if not translated_text:
            print(
                f"[bold red]Error: Translation API returned empty response for batch request.[/bold red]"
            )
            raise Exception("Translation API returned empty response")

        if per_line:
            for original, translated in zip(text_list, translated_paragraphs):
                self.save_context(str(original).strip(), translated)

        return translated_paragraphs
