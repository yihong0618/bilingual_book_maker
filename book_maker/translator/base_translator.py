import itertools
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from rich import print

from ..structured import (
    extract_json_object,
    prompt_with_schema,
    run_rungs,
    unwrap_schema_echo,
)

# Special delimiter for batch translation - UUID-based token unlikely to appear in any text
BATCH_DELIMITER = "\n\n@@\n\n"


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


class Base(ABC):
    # Default values for fatal error handling - subclasses can override
    TRANSLATION_ERROR_MARKER = None

    # Refusals of one rung, by one model, before we stop offering it.
    RUNG_REFUSAL_THRESHOLD = 2

    def __init__(self, key, language) -> None:
        self.keys = itertools.cycle(key.split(","))
        self.language = language
        self._fatal_error_detected = False
        # {model: {rung name: refusal count}} — see _retire_rung. Dict writes
        # are atomic under the GIL, which is all the synchronization this
        # needs: the worst a race costs is one repeated rejection, never a
        # wrong answer.
        self._rung_refusals = {}

    @abstractmethod
    def rotate_key(self):
        pass

    @abstractmethod
    def translate(self, text):
        pass

    def set_deployment_id(self, deployment_id):
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
        return unwrap_schema_echo(extract_json_object(text))

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

        # Final fallback: split by double newlines if still not matching
        if len(result_list) != paragraph_count:
            lines = text.splitlines()
            result_list = [line.strip() for line in lines if line.strip() != ""]

        return result_list

    def _do_batch_translate(
        self, text_list, prompt_template, system_content, default_prompt, translate_func
    ):
        """
        Perform batch translation with fallback to one-by-one translation.

        Args:
            text_list: List of texts to translate
            prompt_template: User's custom prompt template
            system_content: User's custom system message
            default_prompt: Default prompt template
            translate_func: Function to call for actual translation (single or batch)

        Returns:
            List of translated texts
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
        context_flag = getattr(self, "context_flag", False)

        try:
            # Set batch values
            setattr(self, prompt_attr, batch_prompt)
            if batch_sys_msg and hasattr(self, sys_msg_attr):
                setattr(self, sys_msg_attr, batch_sys_msg)
            if context_flag:
                self.context_flag = False

            translated_text = translate_func(batch_text)
        finally:
            # Restore original values
            setattr(self, prompt_attr, original_prompt)
            if original_sys_msg is not None and hasattr(self, sys_msg_attr):
                setattr(self, sys_msg_attr, original_sys_msg)
            if context_flag:
                self.context_flag = True

        # Handle None or empty response
        if not translated_text:
            print(
                f"[bold red]Error: Translation API returned empty response for batch request.[/bold red]"
            )
            raise Exception("Translation API returned empty response")

        translated_paragraphs = self._extract_paragraphs(translated_text, plist_len)

        # Fallback to one-by-one translation if paragraph count doesn't match
        if len(translated_paragraphs) != plist_len:
            print(
                f"Warning: Expected {plist_len} translations, got {len(translated_paragraphs)}. Falling back to one-by-one translation."
            )
            print(f"\n[Debug] Input text_list ({plist_len} items):")
            stripped_texts = [str(t).strip() for t in text_list]
            for i, t in enumerate(stripped_texts, 1):
                print(f"  [{i}] {t!r}")
            print(f"\n[Debug] Model response ({len(translated_text)} chars):")
            print(translated_text)
            print(f"\n[Debug] Split result ({len(translated_paragraphs)} items):")
            for i, p in enumerate(translated_paragraphs, 1):
                print(f"  [{i}] {p!r}")
            print()
            # context_flag is restored here, so each single call saves its own
            # pair — no extra bookkeeping needed on this path
            return [translate_func(t) for t in stripped_texts]

        if context_flag:
            for original, translated in zip(text_list, translated_paragraphs):
                self.save_context(str(original).strip(), translated)

        return translated_paragraphs
