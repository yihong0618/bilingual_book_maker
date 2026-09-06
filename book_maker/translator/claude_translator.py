import re
from pathlib import Path
from urllib.parse import urlparse

from rich import print
from rich.markup import escape
from anthropic import (
    Anthropic,
    APIStatusError,
    BadRequestError,
    UnprocessableEntityError,
)

from .base_translator import Base
from ..config import config
from ..session_context import (
    HandoffReport,
    SessionHistory,
    compact_budget_for,
    handoff_prompt,
)
from ..redaction import remember
from ..structured import RungRejected

# The window this route falls back to when the CLI hands it a limit of 0.
# Borrowed from the openai route deliberately: how many pairs are worth
# re-sending is a property of the mode, not of the endpoint serving it, and
# two numbers for one question is how they drift apart.
DEFAULT_CONTEXT_PARAGRAPH_LIMIT = config["translator"]["chatgptapi"][
    "context_paragraph_limit"
]

# What an endpoint says when the history itself is what it refused. Matched on
# the text as well as the status, because the gateways that serve this shape do
# not all raise the SDK's own error types.
_TOO_LONG = re.compile(
    r"prompt is too long|context[ _-]?length|context window|"
    r"too many tokens|maximum context|request too large",
    re.IGNORECASE,
)


def _history_too_long(reason) -> bool:
    """Whether a failed compact is plausibly this window's size, not the weather.

    That distinction is what decides whether the failure is worth retrying. A
    rate limit, a dropped connection or a 5xx clears on its own, and throwing
    away a book's accumulated context over one is precisely what the retry
    exists to prevent. A history that does not fit clears never, and it takes
    the whole run with it: the retry only happens on the next paragraph, and
    that paragraph's request carries this same history plus the paragraph, so
    it is refused first and the retry is never reached. Anything else the
    endpoint refuses outright is read the same way, because the next request
    is this one with more in it.
    """
    status = getattr(reason, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        # 408 and 429 are the two 4xx that mean "later", not "no".
        return 400 <= status < 500 and status not in (408, 429)
    return bool(_TOO_LONG.search(str(reason)))


def _sdk_base_url(api_base):
    """Trim the request path the SDK is going to add back.

    `Anthropic(base_url=...)` appends `/v1/messages` itself, so a URL copied
    from a gateway's docs (`https://host/v1`, or the whole
    `https://host/v1/messages`) produces `/v1/v1/messages` and a 403 whose
    text — "HTTP node only allows access to inference API paths" — points
    nowhere near the cause.
    """
    if not api_base:
        return None
    base = api_base.strip().rstrip("/")
    trimmed = False
    if base.endswith("/messages"):
        base = base[: -len("/messages")].rstrip("/")
        trimmed = True
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
        trimmed = True
    if trimmed:
        print(f"[dim]using anthropic base_url {base} (the SDK adds /v1)[/dim]")
    return base


def _text_blocks(message):
    """The text of a reply: its `text` blocks joined, other blocks skipped.

    A model with extended thinking answers with a `thinking` block before
    its text, so `content[0]` is not the reply. Empty when no block is text.
    """
    return "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )


# A reply whose whole content sits inside one symmetric pair of triple-backtick
# fences. The opening fence may carry a markdown info string ("```zh"), but only
# when it owns its line — otherwise "```Animal Farm```" would read "Animal" as a
# language tag and lose the first word of the translation. `(?P=fence)` pins the
# closing run to the same length as the opening one, and the trailing `\s*\Z`
# makes it the *last* fence in the reply, not the first one found.
_OUTER_FENCE = re.compile(
    r"""\A\s*
        (?P<fence>`{3,})
        (?:(?P<tag>[A-Za-z0-9_+.#-]*)[ \t]*\r?\n)?
        (?P<body>.*?)
        (?P=fence)\s*\Z
    """,
    re.DOTALL | re.VERBOSE,
)


def _strip_outer_fence(text):
    """Unwrap a translation the model handed back inside a code fence.

    This route's DEFAULT_PROMPT delimits the source with triple backticks
    ("...the text within triple backticks..."), and models mirror the
    delimiter back around their answer. Nothing downstream removes it, so the
    fences were written into the book: `<p>```动物庄园```</p>` on every
    paragraph of a default anthropic run (seen live 260905, plain and
    `--use_context session` alike).

    The prompt is upstream's contract and users write `--prompt` text against
    it, so the fix is here, on the reply. Only a *symmetric outer* wrap is
    removed:

    * no wrap, or an opening fence with no closing one, and the text comes
      back byte-identical — a half-fenced reply is a damaged reply, and
      quietly half-repairing it hides that;
    * a fence run inside the body means the reply is a document with code
      blocks in it rather than a wrapped translation, so it is left alone;
    * a wrap around nothing is left alone too, rather than manufacturing an
      empty translation out of a garbage reply.
    """
    match = _OUTER_FENCE.match(text or "")
    if not match:
        return text
    body = match.group("body")
    # Anything as long as the outer fence, inside it, and this is not a wrap.
    if match.group("fence") in body:
        return text
    body = body.strip()
    return body if body else text


def _reply_text(message):
    """`_text_blocks`, but a reply with no text at all is an error, not ""."""
    text = _text_blocks(message)
    if not text:
        kinds = ", ".join(getattr(b, "type", "?") for b in message.content) or "none"
        raise ValueError(f"the reply carried no text block (blocks: {kinds})")
    return text


class Claude(Base):
    DEFAULT_PROMPT = (
        "Help me translate the text within triple backticks into {language} "
        "and provide only the translated result.\n```{text}```"
    )

    SUPPORTS_SESSION_CONTEXT = True
    SUPPORTS_PARALLEL_CONTEXT = True
    SUPPORTS_REQUEST_EXTRAS = True
    # Compact attempts before giving up on a summary and starting clean. More
    # than one so a transient error does not cost the accumulated context;
    # bounded so a broken endpoint cannot grow the history forever.
    COMPACT_ATTEMPTS = 3

    # Session-mode state, declared here so the window-mode path is well
    # defined on any instance — including the test fixtures that build one
    # without running __init__. `session is None` means window mode
    # everywhere in this class.
    session = None
    handoff_path = None
    context_compact_at = None
    no_context_compact = False
    context_mode = "window"

    # Set by the CLI from --quiet. Suppresses this class's own echoes.
    quiet = False
    style_note = None

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        temperature=1.0,
        context_flag=False,
        context_paragraph_limit=5,
        context_mode="window",
        context_compact_at=None,
        no_context_compact=False,
        style_note=None,
        handoff_path=None,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        base_url = _sdk_base_url(api_base)
        self.api_url = base_url or "https://api.anthropic.com"
        # One key, not the whole comma-separated list; rotate_key advances it.
        self.client = Anthropic(base_url=base_url, api_key=next(self.keys), timeout=20)
        self.model = "claude-haiku-4-5-20251001"  # default it for now
        self.language = language
        self.prompt_template = prompt_template or self.DEFAULT_PROMPT
        self.prompt_sys_msg = prompt_sys_msg or ""
        self.temperature = temperature
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        # The CLI's default is 0, and 0 here meant the window kept nothing at
        # all: save_context appended a pair and popped it again on the same
        # call, so --use_context bought a request-shaped no-op.
        self.context_paragraph_limit = (
            context_paragraph_limit
            if context_paragraph_limit > 0
            else DEFAULT_CONTEXT_PARAGRAPH_LIMIT
        )
        # Session mode replaces the window entirely; `session is None` is the
        # single test for "are we in window mode" everywhere below.
        self.context_mode = context_mode or "window"
        self.session = (
            SessionHistory()
            if context_flag and self.context_mode == "session"
            else None
        )
        self.context_compact_at = context_compact_at
        self.no_context_compact = no_context_compact
        self.style_note = style_note
        self.handoff_path = Path(handoff_path) if handoff_path else None
        self._compact_failures = 0

    # Both of these turn off exactly what session mode cannot afford, and both
    # are the same question — is a byte-stable prefix being maintained? — so
    # they answer it the same way. Window mode is untouched by either.

    @property
    def BATCH_SYS_MSG_PER_REQUEST(self):
        """False while a session is open: the system message is part of the prefix.

        Anthropic caches the system message together with the history, so
        borrowing `prompt_sys_msg` for the length of one grouped request moves
        the prefix for that request and leaves the next one no longer extending
        it. Every poetry group would then cost a full-price re-read of the
        whole accumulated history — the one expense session mode exists to
        avoid. The batch contract is not lost by this: `_build_batch_prompt`
        also puts it at the head of the user prompt, and that rides with the
        request rather than in front of it.
        """
        return self.session is None

    @property
    def BATCH_CONTEXT_PER_LINE(self):
        """False while a session is open: the history replays what was sent.

        A window keeps paragraphs, so it wants one pair per line. A session
        keeps requests, and the grouped request was a single exchange — split
        into per-line pairs, the history stops matching what the endpoint
        actually saw, and the prefix breaks a second way. Off, `context_flag`
        is left alone for the batch request and `translate` records the
        joined exchange itself — verbatim by construction, since it saves
        the very content it just sent.
        """
        return self.session is None

    def set_request_extras(self, extra_body=None, extra_headers=None):
        """See `Base.set_request_extras`. Headers ride on the client."""
        self.extra_body = extra_body or {}
        self.extra_headers = extra_headers or {}
        remember(*self.extra_headers.values())
        if self.extra_headers:
            self.client = self.client.with_options(default_headers=self.extra_headers)

    def rotate_key(self):
        """Advance to the next key, as the comma-separated form promises.

        `Anthropic.api_key` is writable, so this needs no new client. Without
        it a multi-key run sent the literal string "a,b" as the credential
        and failed authentication.
        """
        self.client.api_key = next(self.keys)

    def set_model_list(self, model_list):
        """Take the model to use. Any id the endpoint serves is accepted.

        Claude has no model rotation, so when several are named the first
        wins — announced, not silently.
        """
        models = [m.strip() for m in model_list if m and m.strip()]
        if not models:
            raise ValueError("--model_list is empty")
        if len(models) > 1:
            print(
                f"[yellow]ℹ claude uses one model per run; taking "
                f"'{models[0]}' and ignoring {len(models) - 1} more[/yellow]"
            )
        self.model = models[0]

    def _explain_wrong_shape(self, error):
        """Re-raise `error`, naming the fix when a gateway does not serve this shape.

        A 404 or 405 from a host other than Anthropic's own usually means the
        endpoint speaks the OpenAI shape only. On api.anthropic.com a 404 is
        about the model, so it is passed through untouched.
        """
        host = (urlparse(self.api_url).hostname or "").lower()
        status = getattr(error, "status_code", None)
        official = host == "anthropic.com" or host.endswith(".anthropic.com")
        if official or status not in (404, 405):
            raise error
        raise RuntimeError(
            f"{self.api_url} does not answer the anthropic shape at "
            f"/v1/messages ({error}). If it is an OpenAI-compatible endpoint, "
            f"pass --api_format openai."
        ) from error

    def _user_content(self, text):
        """The user message for one unit.

        Deterministic for a given text, which is what lets session mode store
        exactly what it sent without threading the string around — the marker
        preamble included, since it is a function of the text too.
        """
        return self._marker_preamble(text) + self.prompt_template.format(
            text=text, language=self.language
        )

    def create_messages(self, text, intermediate_messages=None):
        """Create messages for the current translation request"""
        current_msg = {"role": "user", "content": self._user_content(text)}

        messages = []
        if intermediate_messages:
            messages.extend(intermediate_messages)
        messages.append(current_msg)

        return messages

    def create_context_messages(self):
        """Create a message pair containing all context paragraphs"""
        if self.session is not None:
            # Session mode: the whole append-only history is the prefix. The
            # per-unit window below does not apply — that is the mode this
            # one replaces.
            return self.session.messages()
        if not self.context_flag or not self.context_list:
            return []

        # Create a single message pair for all previous context
        return [
            {
                "role": "user",
                "content": self.prompt_template.format(
                    text="\n\n".join(self.context_list),
                    language=self.language,
                ),
            },
            {"role": "assistant", "content": "\n\n".join(self.context_translated_list)},
        ]

    def save_context(self, text, t_text):
        """Save the current translation pair to context"""
        if not self.context_flag:
            return

        if self.session is not None:
            self._save_session_context(text, t_text)
            return

        self.context_list.append(text)
        self.context_translated_list.append(t_text)

        # Keep only the most recent paragraphs within the limit
        if len(self.context_list) > self.context_paragraph_limit:
            self.context_list.pop(0)
            self.context_translated_list.pop(0)

    # ---- session mode -----------------------------------------------------

    def _cache_kwargs(self):
        """The cache breakpoint session mode is built on, or nothing.

        Anthropic caches nothing unless it is asked to: without this the
        history is re-read at full input price on every request, which is
        strictly worse than the window mode session mode replaces. The
        top-level breakpoint marks the last block of the request, so each
        request writes the pair it just added and reads everything before it.

        Window mode asks for none — its prefix is three paragraphs and would
        only pay the write premium.
        """
        if self.session is None:
            return {}
        return {"cache_control": {"type": "ephemeral"}}

    def _note_usage(self, message, model=None):
        """Add what the endpoint billed for this request to the meter.

        Anthropic's `input_tokens` leaves the cached part out, so the prompt
        total is the three input counts together; `cache_read_input_tokens`
        is the number a gateway that drops `cache_control` never reports.
        """
        try:
            usage = getattr(message, "usage", None)
            if usage is None:
                return
            read = getattr(usage, "cache_read_input_tokens", 0) or 0
            written = getattr(usage, "cache_creation_input_tokens", 0) or 0
            self.usage.note(
                (getattr(usage, "input_tokens", 0) or 0) + read + written,
                getattr(usage, "output_tokens", 0),
                read,
                model=model or self.model,
            )
        except Exception:
            # a readout, not a gate: a usage record shaped strangely by a
            # gateway is not a reason to stop a paid run
            return

    def _session_budget(self):
        """How large a window may grow before it rolls over."""
        if self.context_compact_at is None:
            return compact_budget_for(self.model)
        return self.context_compact_at

    def _save_session_context(self, text, t_text):
        # Store what was *sent*, not the bare source. The next request replays
        # this message verbatim, so any difference — the prompt template, say —
        # would make the newest pair a cache miss, and the run would re-read a
        # paragraph at full input price every request.
        self._record_session_exchange(self._user_content(text), t_text)

    def _record_session_exchange(self, user_content, reply_text):
        """Append one exchange, given the strings the wire actually carried."""
        self.session.append(user_content, reply_text)
        if not self.session.should_compact(self._session_budget()):
            return
        if self.no_context_compact:
            self._start_empty_window()
        else:
            self._compact_session()

    def _compact_session(self):
        """Ask for a handoff report, then start the next window seeded with it.

        The report is requested on top of the existing history — that is the
        one turn where the whole window is worth re-reading, because it is
        being condensed into what replaces it.
        """
        budget = self._session_budget()
        messages = [
            *self.session.messages(),
            {
                "role": "user",
                "content": handoff_prompt(with_style=not self.style_note),
            },
        ]
        try:
            r = self.client.messages.create(
                max_tokens=4096,
                messages=messages,
                system=self.prompt_sys_msg,
                temperature=self.temperature,
                model=self.model,
                extra_body=self.extra_body or None,
                **self._cache_kwargs(),
            )
            self._note_usage(r)
            report_text = _text_blocks(r)
        except Exception as e:
            self._compact_failed(e, budget)
            return
        if not report_text.strip():
            # A 200 carrying no text at all — an empty or tool-only content
            # list, which a gateway can answer with. Nothing was raised, so
            # this used to count as a successful compaction: the window was
            # reset and seeded with the empty string, throwing away the whole
            # accumulated context and buying nothing for it. It is a compact
            # that produced no report, so it takes the failure path, and it is
            # not a report, so it is not printed as one.
            self._compact_failed("the endpoint returned an empty report", budget)
            return
        self._compact_failures = 0

        report = HandoffReport(
            window=self.session.windows,
            # A style the user fixed is handed on verbatim, so it cannot be
            # eroded window by window by a model re-describing it.
            style_note=self.style_note,
            summary=report_text.strip(),
        )
        self._show_handoff(report)
        if self.handoff_path:
            try:
                report.append_to(self.handoff_path)
            except OSError as e:
                # The paragraph is already translated and billed. Failing here
                # would lose it over a file that is not what was asked for.
                print(
                    f"[yellow]ℹ could not write {self.handoff_path} ({e}); "
                    f"the run continues without a saved handoff[/yellow]"
                )
        self.session.reset(seed=report.seed_text())

    def _compact_failed(self, reason, budget):
        """A compact that came back with no usable report: retry, or start clean.

        Keeping the window is the default, and the reason the retry exists:
        one rate-limited or dropped request is not grounds for throwing away a
        book's worth of accumulated context, and the budget stays exceeded, so
        the next unit simply tries again.
        """
        self._compact_failures += 1
        # Give up on attempts, once the window has outgrown its budget badly
        # enough that retrying is the wrong bet, or as soon as the endpoint
        # says the history is what it refused — that last one cannot be
        # retried at all, because the retry is deferred to a paragraph whose
        # request carries this same history and is refused before it.
        give_up = (
            _history_too_long(reason)
            or self._compact_failures >= self.COMPACT_ATTEMPTS
            or self.session.estimated_tokens() > 2 * budget
        )
        print(
            f"[yellow]ℹ handoff report failed ({reason}); "
            + (
                "starting the next context window without a summary"
                if give_up
                else "keeping the current context and retrying on the next paragraph"
            )
            + "[/yellow]"
        )
        if give_up:
            # Bounded: without this the history would grow past the budget
            # forever on a persistently failing endpoint.
            self._compact_failures = 0
            self.session.reset(seed="")

    def _start_empty_window(self):
        """Roll over with no handoff report, because the user asked for none.

        Continuity across the seam is what the report buys, and
        `--no-context-compact` declines to buy it — so this is a plain reset,
        not a cheaper summary.
        """
        self.session.reset(seed="")
        if self.quiet:
            return
        print(
            f"[bold cyan]— context window {self.session.windows}, started "
            f"empty (--no-context-compact) —[/bold cyan]"
        )

    def _show_handoff(self, report):
        """Print the report the next window will inherit.

        `escape` is not optional: rich reads square brackets as markup, and
        these reports genuinely contain things like "[PGA]", which would be
        swallowed or raise on an unclosed tag.
        """
        if self.quiet:
            # --quiet suppresses echoes like this one; warnings and errors
            # still print.
            return
        print(
            f"[bold cyan]— handoff report, window {report.window} —[/bold cyan]\n"
            + escape(report.render())
        )

    def _chat_completion(self, prompt, model=None):
        """One question, one answer — the channel plan classification needs.

        No native structured-output rung on purpose: nobody here runs the
        official Anthropic API, so its schema feature cannot be tested, and
        the gateways that serve the anthropic shape drop schema fields
        anyway. Measured 260807 on api.b.ai: Claude ignores `response_format`
        entirely, and still answers 12 of 12 signatures with legal verdicts
        through this rung. The lint is what establishes that, not the request.

        Deliberately outside the translation flow: no context pairs, no
        prompt template, no saved history.
        """
        try:
            r = self.client.messages.create(
                max_tokens=4096,
                model=model or self.model,
                messages=[{"role": "user", "content": prompt}],
                extra_body=self.extra_body or None,
            )
        except (BadRequestError, UnprocessableEntityError) as e:
            self.warn_if_extras_refused(e)
            raise RungRejected(e) from e
        except APIStatusError as e:
            self._explain_wrong_shape(e)
        self._note_usage(r, model)
        return _reply_text(r)

    def translate(self, text):
        self.rotate_key()

        # Create messages with context
        messages = self.create_messages(text, self.create_context_messages())

        try:
            r = self.client.messages.create(
                max_tokens=4096,
                messages=messages,
                system=self._augment_system_content(self.prompt_sys_msg),
                temperature=self.temperature,
                model=self.model,
                extra_body=self.extra_body or None,
                **self._cache_kwargs(),
            )
        except APIStatusError as e:
            self._explain_wrong_shape(e)
        self._note_usage(r)
        # Blocks first, fences second: the strip has to see the final joined
        # text, or a reply split across two text blocks ("```" + "译文```")
        # keeps its wrapper. Everything downstream — the batch splitter, the
        # session history, the book — then sees the same unwrapped string.
        t_text = _strip_outer_fence(_reply_text(r))

        if self.context_flag:
            self.save_context(text, t_text)

        return t_text

    def translate_list(self, text_list):
        """Translate a group of paragraphs in one request.

        Plan mode hands whole batches of short units here, and the batch is
        the point: consecutive short lines only survive if they are
        translated together, with their neighbours in view. The inherited
        default loops over `translate` and dissolves the group into isolated
        lines, which is the one thing `--poetry-group-size` exists to
        prevent.

        The delimiter contract and the count check come from the base, so
        this route and the openai one agree on what a batch looks like — and
        on raising `BatchMismatch` rather than repairing a bad reply here.
        The loader's ladder owns the repair.
        """
        return self._do_batch_translate(
            text_list,
            self.prompt_template,
            self.prompt_sys_msg,
            self.DEFAULT_PROMPT,
            self.translate,
        )
