"""Translate through a ChatGPT/Codex subscription instead of API credits.

The `codex` format drives a `codex app-server` sidecar (see
`book_maker/codex_client.py`), so the run is billed to the user's ChatGPT plan
and there is no `--openai_key`.

Threading is the whole design here. A fresh Codex thread costs roughly 17k
input tokens of preamble before our first paragraph — measured, and not
reducible through `thread/start`'s config overrides — so one thread per
paragraph would spend more on preamble than on the book. Instead a thread is
opened once and reused for every unit, which is also what makes it a context
window: the thread accumulates the translation as it goes, exactly like
`--use_context session` on the API path. At the compact budget the thread is
asked for a translator handoff report and a fresh thread is started with that
report as its instructions.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from threading import Lock

from rich import print

from ..codex_client import (
    CodexAppServer,
    CodexError,
    CodexQuotaExhausted,
    CodexTurnFailed,
)
from ..glossary import Glossary
from ..session_context import (
    HandoffReport,
    compact_budget_for,
    estimate_tokens,
    handoff_prompt,
    strip_handoff_glossary,
)
from .base_translator import Base

BASE_INSTRUCTIONS = (
    "You are a professional book translator. Your job is to translate the "
    "given text into {language}. Return {language} translation only, don't "
    "append, don't miss, don't summarize. Also, keep the source's paragraph "
    "structure and any inline markup exactly as given."
)

# The prompt a batched window rides on when the user named none. A bare
# `{text}` on purpose: `_build_batch_prompt` prepends the segment count and
# the delimiter, and the thread instructions already carry the "translate
# this, reply with nothing else" half, so anything more here would repeat an
# instruction the thread has.
BATCH_PROMPT = "{text}"

# Codex's own preamble dwarfs anything we can save per turn, so the warning
# threshold is about the user's 5-hour window, not about tokens.
QUOTA_WARN_PERCENT = 90

# Used when --model is omitted. Naming one beats letting Codex pick: the
# compact budget is looked up by model id, so an unknown default would fall
# back to the conservative 8000 instead of this model's own 17000.
DEFAULT_MODEL = "gpt-5.6-luna"

# A minute past the reset, because the server's clock and ours are not the
# same and coming back a second early just burns another failed turn.
RESET_GRACE_SECONDS = 60

# One window is 5 hours; a weekly limit resets far too late to sit out. Past
# this, say when it clears and stop rather than hang for days.
MAX_WAIT_SECONDS = 6 * 60 * 60

# Depletion is expected to clear on the first wait. Allowing a couple more
# covers a reset that lands late; beyond that something else is wrong.
MAX_WAITS_PER_TURN = 3


class ClassifierThread:
    """Plan classification on a thread of its own.

    The thread *is* the append-only history here, so the trunk goes into its
    base instructions and a turn carries nothing but the next signatures —
    which is the shape this route wants anyway: a fresh thread costs ~16.9k
    input tokens of Codex's own preamble, and the classifier asks a book's
    worth of questions.

    Separate from the translation thread (a question in it would pollute the
    context the next paragraph inherits) and from the `_question_threads`
    cache, whose turns are self-contained prompts rather than a conversation
    with a trunk.
    """

    def __init__(self, translator, model=None):
        self.translator = translator
        self.model = model or translator.model
        self._trunk = ""
        self._thread_id = None

    def budget(self):
        """`--context-compact-at`, else this thread's *own* model's default.

        Its own, not the translation thread's: `--plan-classify-model` puts
        a different model on this thread, and the window it rolls over
        against is that model's.
        """
        if self.translator.context_compact_at is None:
            return compact_budget_for(self.model)
        return self.translator.context_compact_at

    def start(self, trunk):
        """Open a fresh conversation. The thread is created on the first
        turn, so a session nobody asks anything of costs nothing.

        The demonstration the openai-shaped route seeds as a real message
        pair is folded into the instructions here instead: a thread's turns
        are the model's own, and there is no way to hand it a reply nobody
        made. It degrades to text rather than being dropped — the same rule
        the prompt sectioning follows when a route has no system channel.
        """
        from ..loader.classify.session import trunk_with_inline_example

        self._trunk = trunk_with_inline_example(trunk)
        self._thread_id = None

    def _open(self):
        self._thread_id = self.translator._ensure_server().start_thread(
            model=self.model,
            base_instructions=self._trunk,
        )

    def ask(self, text):
        if self._thread_id is None:
            self._open()
        try:
            return self.translator._run_turn(self._thread_id, text)
        except CodexTurnFailed:
            # The sidecar dropped the thread: the cached id is dead and no
            # retry on it can work. Opening a new one re-sends the trunk as
            # its instructions, and a verdict depends on nothing that was
            # said earlier, so the lost turns cost nothing but themselves.
            self._open()
            return self.translator._run_turn(self._thread_id, text)


class Codex(Base):
    """A translator backed by the Codex app-server."""

    # It reaches its own sidecar, not `self.openai_client`, so the endpoint
    # capability probe would ask the wrong server.
    SUPPORTS_STRUCTURED_OUTPUTS = False

    # The thread is the history: a turn is appended to it and the window
    # rolls over into a handoff turn like any other session route.
    SUPPORTS_SESSION_CONTEXT = True

    # And it is one whether or not `--use_context session` was passed: there
    # is no windowed shape to fall back to here. A run on this route is
    # billed like a session run, so it derives a session run's budgets.
    SESSION_CONTEXT_ALWAYS_ON = True

    # A turn carries whatever we put in it, so a pinned block rides with the
    # unit here exactly as it does on the API path.
    SUPPORTS_GLOSSARY = True

    # A turn carries no system message of its own; `prompt_sys_msg` is read
    # once, when a thread opens, and the thread outlives any one window.
    BATCH_SYS_MSG_PER_REQUEST = False

    # A thread has no system slot, so `--prompt`'s system section is appended
    # to the thread instructions rather than replacing them: those base
    # instructions are what keep a turn behaving like a completion instead of
    # an agent turn (see `_instructions`). Style joins it there.
    PROMPT_SECTION_SLOTS = {
        "user": "native",
        "system": "appended",
        "style": "appended",
    }
    PROMPT_APPEND_TARGET = "the thread instructions"

    # Set by the CLI from --quiet. Suppresses this class's own echoes.
    quiet = False
    style_note = None

    def __init__(
        self,
        key,
        language,
        server=None,
        binary="codex",
        context_compact_at=None,
        no_context_compact=False,
        glossary=None,
        glossary_auto=None,
        style_note=None,
        handoff_path=None,
        prompt_template=None,
        prompt_sys_msg=None,
        **kwargs,
    ) -> None:
        # `key` is accepted and ignored: this format authenticates through
        # codex's stored ChatGPT session.
        super().__init__(key or "", language)
        self.server = server or CodexAppServer(binary=binary)
        self._started = server is not None
        self.model = DEFAULT_MODEL
        self.model_list = None
        self.context_compact_at = context_compact_at
        self.no_context_compact = no_context_compact
        # `pinned` is the operator's --glossary file and never changes.
        # `learned` accumulates what compacts establish. `glossary` is the two
        # combined, pins on top, and is what rides with each unit.
        self.pinned = glossary or Glossary()
        self.learned = Glossary()
        self.glossary = self.pinned
        self.glossary_auto = glossary_auto
        self.handoff_path = Path(handoff_path) if handoff_path else None
        self.prompt_sys_msg = prompt_sys_msg
        self.prompt_template = prompt_template
        self.style_note = style_note
        self._thread_id = None
        # The question thread, kept apart from the translation thread and
        # reused across questions. Keyed by model because --plan-classify-model
        # can name a different one than the book is translated with.
        self._question_threads = {}
        self._window = 1
        self._window_tokens = 0
        self._turn_lock = Lock()
        self._last_remaining = None
        self._sleep = kwargs.pop("sleeper", time.sleep)
        self._now = kwargs.pop("clock", time.time)

    # ---- lifecycle --------------------------------------------------------

    def rotate_key(self):
        """No keys here; the sidecar owns the credentials."""

    # `--model codex` names the format, not a model. Treated as "unset" so it
    # does not reach the sidecar as a model id that does not exist.
    FORMAT_ALIASES = ("codex",)

    def set_model_list(self, model_list):
        names = [
            name
            for name in model_list
            if name and name.strip().lower() not in self.FORMAT_ALIASES
        ]
        # Codex resolves its own default when none is named, unlike the API
        # formats where a missing model has nothing to fall back on.
        self.model_list = names
        self.model = names[0] if names else DEFAULT_MODEL

    def _ensure_server(self):
        if not self._started:
            self.server.start()
            self._started = True
        return self.server

    def preflight(self):
        """Confirm a login and say how much of the window is already spent."""
        limits = self._ensure_server().ensure_logged_in()
        if limits is None:
            return None
        plan = f" ({limits.plan_type} plan)" if limits.plan_type else ""
        self._last_remaining = limits.remaining_percent
        if self.quiet and limits.used_percent < QUOTA_WARN_PERCENT:
            # --quiet keeps warnings and errors; a healthy window is neither.
            return limits
        if limits.used_percent >= QUOTA_WARN_PERCENT:
            print(
                f"[bold yellow]Warning:[/bold yellow] only "
                f"{limits.remaining_percent:g}% of your Codex window remains"
                f"{self._reset_phrase(limits)}. A long book may exhaust it; the "
                f"run waits for the reset rather than stopping."
            )
        else:
            print(
                f"[green]Codex: signed in{plan}, "
                f"{limits.remaining_percent:g}% of the window remaining[/green]"
            )
        return limits

    @staticmethod
    def _reset_phrase(limits):
        reset = limits.blocking_reset
        if not reset:
            return ""
        when = datetime.fromtimestamp(reset).strftime("%H:%M")
        return f", resetting at {when}"

    def _report_quota(self):
        """Print the remaining share whenever it moves."""
        if self.quiet:
            # It moves on nearly every unit, so this was a line per
            # paragraph in every log a --quiet run wrote.
            return
        limits = self.server.latest_rate_limits()
        if limits is None:
            return
        remaining = limits.remaining_percent
        if remaining == self._last_remaining:
            return
        self._last_remaining = remaining
        print(
            f"[green]Codex: {remaining:g}% of the window remaining"
            f"{self._reset_phrase(limits)}[/green]"
        )

    def _wait_out_reset(self, limits):
        """Sleep until the blocking window clears. Returns False if pointless."""
        if limits is None or not limits.waitable:
            return False
        seconds = limits.blocking_reset - self._now() + RESET_GRACE_SECONDS
        if seconds <= 0:
            return True  # already past it; just retry
        if seconds > MAX_WAIT_SECONDS:
            # A weekly limit. Sitting it out is not an option, so the run
            # ends here — but it ends saying when the allowance comes back,
            # which is the one fact needed to decide when to rerun.
            when = datetime.fromtimestamp(limits.blocking_reset).strftime(
                "%Y-%m-%d %H:%M"
            )
            raise CodexQuotaExhausted(
                f"the Codex plan allowance is spent and does not reset until "
                f"{when} ({seconds / 3600:.0f} h away), which is too long to "
                f"wait out. Rerun with --resume once it has."
            )
        when = datetime.fromtimestamp(limits.blocking_reset).strftime("%Y-%m-%d %H:%M")
        print(
            f"[bold yellow]Codex quota spent.[/bold yellow] Waiting "
            f"{seconds / 60:.0f} min for the window to reset at {when}, then "
            f"continuing. Ctrl+C stops; the run is resumable."
        )
        self._sleep(seconds)
        try:
            self.server.rate_limits()  # refresh the snapshot after the wait
        except CodexError:
            pass
        return True

    def _run_turn(self, thread_id, payload):
        """One turn, sitting out a spent quota window rather than failing."""
        for attempt in range(MAX_WAITS_PER_TURN + 1):
            limits = self.server.latest_rate_limits()
            # Proactive: a pushed update may already say we are out.
            if limits is not None and limits.depleted and attempt < MAX_WAITS_PER_TURN:
                if self._wait_out_reset(limits):
                    continue
            try:
                return self.server.run_turn(thread_id, payload)
            except CodexTurnFailed:
                # Only treat this as a quota stop if the quota says so —
                # a failed turn has many other causes.
                limits = self.server.latest_rate_limits()
                if (
                    attempt >= MAX_WAITS_PER_TURN
                    or limits is None
                    or not limits.depleted
                    or not self._wait_out_reset(limits)
                ):
                    raise
        raise CodexTurnFailed(
            "the codex quota was still spent after waiting for its reset"
        )

    def close(self):
        if self._started:
            self.server.close()
            self._started = False

    # ---- threads ----------------------------------------------------------

    def _instructions(self, seed=""):
        """Thread instructions: ours, then the user's, then any handoff seed.

        `--prompt`'s system message is *appended* rather than substituted. On
        this path the base instructions are what keep a turn behaving like a
        completion instead of an agent turn — replacing them wholesale would
        let the model answer or summarize the passage instead of translating
        it. The user's voice comes after, where it wins on anything the two
        both speak to.
        """
        parts = [BASE_INSTRUCTIONS.format(language=self.language, crlf="\n")]
        # `--source_lang`: fixed for the run, so it rides with the
        # thread's standing instructions rather than each turn's text.
        note = self._source_language_note()
        if note:
            parts.append(note)
        if self.prompt_sys_msg:
            parts.append(self.fill_optional(self.prompt_sys_msg))
        # The same standing line every API route puts on its system channel,
        # so a style reads identically whichever route carries it. Here the
        # thread instructions *are* that channel, and they are written once
        # when the thread opens — which is where a style belongs.
        if self.style_section():
            parts.append(self.style_section())
        if seed:
            parts.append(seed)
        return "\n\n".join(parts)

    def _ensure_thread(self, seed=""):
        if self._thread_id is None:
            self._thread_id = self._ensure_server().start_thread(
                model=self.model,
                base_instructions=self._instructions(seed),
            )
        return self._thread_id

    @property
    def glossary_auto_on(self):
        """Whether this run learns renderings from its own handoff reports.

        The thread is always the history here, so there is always a compact
        turn to learn from: this route is on unless `--glossary-auto off`.
        """
        return self.glossary_auto is not False

    def _budget(self):
        """How many estimated tokens a thread may carry before it rolls over."""
        if self.context_compact_at is None:
            return compact_budget_for(self.model)
        return self.context_compact_at

    def _compact_window(self):
        """Condense the thread into a handoff report and open the next one.

        The report is asked of the thread that is about to be discarded — the
        one turn where re-reading the whole window earns its cost, because it
        is being turned into the thing that replaces it.
        """
        try:
            report_text = self._run_turn(
                self._thread_id,
                handoff_prompt(
                    with_glossary=self.glossary_auto_on,
                    with_style=not self.style_note,
                ),
            )
        except CodexTurnFailed as e:
            print(
                f"[yellow]ℹ handoff report failed ({e}); starting the next "
                f"codex thread without a summary[/yellow]"
            )
            report_text = ""

        glossary_lines = self._learn_from_handoff(report_text)

        report = HandoffReport(
            window=self._window,
            style_note=self.style_note,
            # Same as the API path: the renderings block is parsed into
            # `glossary_lines`, so keeping it in the prose too would write
            # every term twice.
            summary=(
                strip_handoff_glossary(report_text)
                if self.glossary_auto_on
                else report_text.strip()
            ),
            glossary_lines=glossary_lines,
        )
        if report_text:
            self._show_handoff(report)
        if self.handoff_path and report_text:
            report.append_to(self.handoff_path)

        self._window += 1
        self._window_tokens = 0
        self._thread_id = None
        self._ensure_thread(seed=report.seed_text() if report_text else "")

    def _start_empty_thread(self):
        """Roll over with no handoff report, because the user asked for none.

        Continuity across the seam is what the report buys, and
        `--no-context-compact` declines to buy it — this is Codex's `/new`,
        not a cheaper summary.
        """
        self._window += 1
        self._window_tokens = 0
        self._thread_id = None
        self._ensure_thread(seed="")
        if self.quiet:
            return
        print(
            f"[bold cyan]— codex thread {self._window}, started empty "
            f"(--no-context-compact) —[/bold cyan]"
        )

    def _unit_text(self, text):
        """What the turn carries.

        Bare source by default — the thread instructions already say to
        translate whatever arrives, so wrapping every paragraph in "please
        translate" would repeat an instruction the thread has. A user's
        `--prompt` template is honored when given, since it may say more than
        that.
        """
        if not self.prompt_template:
            return text
        return self.prompt_template.format(text=text, language=self.language, crlf="\n")

    # ---- translation ------------------------------------------------------

    def translate(self, text, needprint=True):
        # Serialized on purpose. Parallel chapters share this instance —
        # `_clone_translator_for_context` only clones translators carrying
        # `context_flag`, and a codex thread *is* the context, so there are no
        # per-worker buffers to hand out. Letting workers overlap would
        # interleave unrelated chapters into one thread, lose window-token
        # updates to races, and let a compact swap the thread mid-turn.
        # `--parallel-workers` therefore buys nothing here, and the CLI says so.
        with self._turn_lock:
            thread_id = self._ensure_thread()

            # Pinned terms belong to this unit, so they ride with it rather
            # than with the thread instructions, which every later turn would
            # re-read.
            block = self.glossary.prompt_block(text) if self.glossary else ""
            payload = self._unit_text(text)
            payload = f"{block}\n\n{payload}" if block else payload

            translated = self._run_turn(thread_id, payload)
            self._report_quota()

            self._window_tokens += estimate_tokens(text) + estimate_tokens(translated)
            if self._window_tokens >= self._budget():
                if self.no_context_compact:
                    self._start_empty_thread()
                else:
                    self._compact_window()

        # `needprint` is accepted for signature compatibility only. The loaders
        # display the source and its translation themselves, so printing here
        # showed every paragraph's translation a second time.
        return translated

    def translate_list(self, text_list):
        """Translate a group of paragraphs in one turn.

        Plan mode hands whole batches of short units here, and the batch is
        the point: short lines only survive if they are translated together,
        with their neighbours in view. The inherited default loops over
        `translate` and dissolves the group into isolated lines, which is the
        one thing `--poetry-group-size` exists to prevent — and on a metered
        subscription it also pays for a turn per line instead of per stanza.

        The delimiter contract and the count check come from the base, so
        this route and the openai one agree on what a batch looks like — and
        on raising `BatchMismatch` instead of repairing a bad reply here; the
        loader's ladder owns the repair. `BATCH_PROMPT` is the carrier the base
        needs and nothing more: the thread instructions already say to
        translate whatever arrives, so the only thing worth adding on top of
        the source is the base's own segment count.
        """
        return self._do_batch_translate(
            text_list,
            self.prompt_template,
            self.prompt_sys_msg,
            BATCH_PROMPT,
            self.translate,
        )

    def _chat_completion(self, prompt, model=None):
        """One arbitrary question, so plan classification works on this path.

        Asked off the translation thread — a classification question inside it
        would pollute the context the next unit inherits — but on *one*
        question thread, reused, not a fresh one per question.

        That reuse is the difference between plan mode being usable here and
        draining a plan. A fresh thread costs ~16.9k input tokens of Codex's
        own preamble (see the module docstring), and the classifier is not one
        request: it pages signatures 12 at a time, `structured_json` retries
        down its rungs when a reply will not parse, and `_resolve` bisects a
        page that comes back partly unanswered. A thread each would put the
        preamble bill for classifying a book above the bill for translating
        it, on a subscription that meters exactly that.

        The questions do accumulate in the thread. That is far cheaper than
        re-paying the preamble — accumulated turns are re-read at the cache
        rate — and for classification it is mildly useful: later pages judge
        signatures against how earlier ones were judged, which is the
        consistency the whole plan wants anyway.

        Reuse costs the disposability the old fresh-thread call had for free:
        if the sidecar drops the thread, the cached id is dead and the caller
        cannot survive it — `_ask_page` turns any such error into
        `PlanClassifyFatal`, which stops classification outright rather than
        retrying. So a dropped thread is evicted and the question is asked once
        more on a new one. That pays the preamble only when a thread actually
        dies, which is what the old code paid on every single question.
        """
        try:
            return self._ask(prompt, model)
        except CodexTurnFailed:
            self._question_threads.pop(model or self.model, None)
            return self._ask(prompt, model)

    def classify_session(self, model=None):
        """See `Base.classify_session`. One thread, held open for the plan.

        This route has no schema verdict to offer at all — it reaches a
        sidecar, not an endpoint the capability probe can grade — so the
        session entry is how plan mode classifies here.
        """
        return ClassifierThread(self, model)

    def _ask(self, prompt, model=None):
        """One question on the (possibly newly opened) question thread."""
        server = self._ensure_server()
        target = model or self.model
        thread_id = self._question_threads.get(target)
        if thread_id is None:
            thread_id = server.start_thread(
                model=target,
                base_instructions=(
                    "Answer the question directly. Reply with the answer only."
                ),
            )
            self._question_threads[target] = thread_id
        # `_run_turn`, not `server.run_turn`: a question is billed to the same
        # subscription window as a translation, so a spent quota should be sat
        # out here too. Classification runs *before* the first paragraph, so
        # without this a user near their limit fails at the very start — and
        # plan mode has no degrade-to-defaults path, so that failure stops the
        # run rather than translating with a guess.
        return self._run_turn(thread_id, prompt)
