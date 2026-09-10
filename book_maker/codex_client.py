"""A JSON-RPC client for the `codex app-server` sidecar.

This is how the `codex` translator format spends a ChatGPT/Codex subscription
allowance instead of API credits. The app-server is the same interface the
official Codex clients drive: it owns OAuth, token refresh and credential
storage, so none of that lives here.

Extracting the OAuth token and calling api.openai.com with it is deliberately
not supported — ChatGPT credits and API credits are separate billing systems,
and doing that would bill the wrong one.

Protocol notes, verified against codex-cli 0.150.1 (see
docs/260827-feat-CODEX_TRANSLATOR_PROVIDER.md):

- Requests are newline-delimited JSON with an `id`; replies carry the same
  `id` plus `result` or `error`. Anything with a `method` and no `id` is a
  notification. Clients must send one `initialize` request and acknowledge
  with an `initialized` notification before anything else.
- `turn/start` returns as soon as the turn is accepted. The answer arrives
  later as a `turn/completed` notification carrying `turn.items`; the
  translation is the item with `type: "agentMessage"` and
  `phase: "final_answer"`.
- With `sandbox: "read-only"` and `approvalPolicy: "never"` a turn behaves
  like a chat completion rather than an agent session — but it still holds
  every tool the user's codex config grants. Book text is untrusted input,
  so `start()` removes those capabilities outright; see
  docs/260830-fix-CODEX_SIDECAR_HARDENING.md for the measurements behind
  each step.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass

# A turn is a model call, and a slow model on a long paragraph is normal.
DEFAULT_TURN_TIMEOUT = 600.0
# Everything else is local bookkeeping inside the sidecar.
DEFAULT_REQUEST_TIMEOUT = 60.0

# What Codex records this client as: it shows up in the app-server's
# userAgent, and is how a run of ours is told apart from the Codex CLI or the
# VS Code extension in any usage view.
CLIENT_NAME = "bilingual-book-maker"


def _client_version():
    """The installed package version, or a placeholder when running from source."""
    try:
        from importlib.metadata import version

        return version("bbook_maker")
    except Exception:
        return "0+unknown"


CLIENT_VERSION = _client_version()

# Every feature that would let a turn act instead of answer: run commands,
# evaluate code, browse, drive plugins or other agents, or fire the user's
# hooks. A flag this codex build does not know is dropped with a warning
# rather than passed (an unknown flag kills the sidecar at spawn); everything
# that survives is verified off via config/read before the first turn.
UNWANTED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "hooks",
    "plugins",
    "apps",
    "remote_plugin",
    "code_mode_host",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "computer_use",
    "image_generation",
    "view_image",
    "multi_agent",
    "skill_search",
    "js_repl",
    "remote_control",
    "web_search",
    "web_search_request",
    "web_search_cached",
    "standalone_web_search",
    "in_app_browser",
    "in_app_local_automation",
    "skill_mcp_dependency_install",
)

# How the sidecar names a --disable flag it does not have, on stderr, before
# exiting. One flag per death.
_UNKNOWN_FEATURE = re.compile(r"Unknown feature flag: (\S+)")

# The only server names expressible as an unquoted dotted override
# (`-c mcp_servers.<name>.enabled=false`). Quoting the key does not help: it
# replaces the whole table and breaks config loading.
_OVERRIDABLE_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class CodexError(Exception):
    """Anything that went wrong talking to the app-server."""


class CodexUnavailable(CodexError):
    """The `codex` binary is missing, too old, or would not start.

    Its message says how to install it, so the run reports that line and
    stops rather than printing a traceback about a spawn that failed.
    """

    user_facing = True


class CodexLoginRequired(CodexError):
    """No usable ChatGPT session. Carries what the user must do about it."""

    user_facing = True


class CodexTurnFailed(CodexError):
    """A turn failed, timed out, or produced no assistant message."""


class CodexQuotaExhausted(CodexError):
    """The plan allowance is spent and its reset is too far off to sit out.

    Its message names when the limit clears, so the run reports it and
    stops rather than printing a traceback about a turn that failed.
    """

    user_facing = True


# Only this one clears with time. The credit/usage-limit variants are not
# windowed, so waiting for a reset would hang forever.
WINDOWED_LIMIT = "rate_limit_reached"


@dataclass(frozen=True)
class RateLimits:
    """A quota snapshot. Fields are optional because rolling updates are sparse."""

    used_percent: float = 0.0
    window_minutes: int | None = None
    resets_at: int | None = None
    plan_type: str | None = None
    reached_type: str | None = None
    secondary_used_percent: float | None = None
    secondary_resets_at: int | None = None

    @classmethod
    def from_snapshot(cls, snapshot):
        primary = snapshot.get("primary") or {}
        secondary = snapshot.get("secondary") or {}
        return cls(
            used_percent=primary.get("usedPercent", 0),
            window_minutes=primary.get("windowDurationMins"),
            resets_at=primary.get("resetsAt"),
            plan_type=snapshot.get("planType"),
            reached_type=snapshot.get("rateLimitReachedType"),
            secondary_used_percent=secondary.get("usedPercent"),
            secondary_resets_at=secondary.get("resetsAt"),
        )

    def merged_with(self, other: "RateLimits") -> "RateLimits":
        """Fold a rolling update in.

        The protocol calls these updates sparse and says a null does not clear
        a previously observed value, so only fields the update actually
        carries are taken.
        """
        if other is None:
            return self
        fields = {}
        for name in self.__dataclass_fields__:
            new = getattr(other, name)
            fields[name] = getattr(self, name) if new is None else new
        # usedPercent is required in an update, so it always wins.
        fields["used_percent"] = other.used_percent
        return RateLimits(**fields)

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - self.used_percent)

    @property
    def depleted(self) -> bool:
        return self.reached_type is not None or self.used_percent >= 100

    @property
    def waitable(self) -> bool:
        """Whether sitting out the window would actually help.

        Credit depletion and account usage limits are not windowed; only a
        rate limit is, and even then we need a reset time to wait for.
        """
        if self.reached_type not in (None, WINDOWED_LIMIT):
            return False
        return self.blocking_reset is not None

    @property
    def blocking_reset(self) -> int | None:
        """When the window that is actually blocking clears.

        Both windows must have room, so a spent weekly limit is what matters
        even when the 5-hour one has already rolled over.
        """
        resets = []
        if self.used_percent >= 100 and self.resets_at:
            resets.append(self.resets_at)
        if (self.secondary_used_percent or 0) >= 100 and self.secondary_resets_at:
            resets.append(self.secondary_resets_at)
        if not resets:
            return self.resets_at
        return max(resets)


class CodexAppServer:
    """One sidecar process, driven request/response with a reader thread.

    `start()` spawns twice. The first spawn only asks `config/read` which MCP
    servers the user's codex config declares, because those are the only
    names a disabling override may mention (an unconfigured name kills
    startup). The second is the one that lives: every agent-facing feature
    `--disable`d, every configured server switched off, cwd pointed at a
    private empty directory so no thread ever runs where the user launched
    us. Both removals are then verified — model refusal is not capability
    removal, so nothing here trusts instructions to do a flag's job.
    """

    def __init__(self, binary="codex", spawn=None):
        # `spawn` takes the extra CLI args for one sidecar spawn.
        self._spawn = spawn or self._spawn_codex
        self.binary = binary
        # Serializes start(): during the two-phase boot `process` is set
        # before verification has run, and a concurrent start() must wait for
        # the verified sidecar rather than return the unverified one.
        self._start_lock = threading.Lock()
        self._run_dir = None
        self._work_dir = None
        self._stderr_path = None
        self._stderr_file = None
        self.process = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        # stdin is written by callers *and* by the reader thread's denials, so
        # it needs a lock of its own — one that is never held while waiting on
        # `_cond`, or the reader could block the only stdout consumer.
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._replies: dict[int, dict] = {}
        self._notifications: list[dict] = []
        # Notifications are consumed by index, so pruning shifts every live
        # mark. `_consumed` records how many were dropped so marks stay valid.
        self._consumed = 0
        # Absolute marks of in-flight waiters; pruning never goes below them.
        self._active_marks: list[int] = []
        self._reader = None
        self._stopped = False
        self._rate_limits = None

    # ---- lifecycle --------------------------------------------------------

    def _spawn_codex(self, args):
        # stderr goes to a file, not a pipe: nothing drains a pipe here, so a
        # chatty sidecar would fill it and stall, while a file never
        # backpressures — and a spawn that dies leaves its reason readable.
        self._stderr_file = open(self._stderr_path, "w")
        return subprocess.Popen(
            [self.binary, "app-server", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            text=True,
            bufsize=1,
            cwd=self._work_dir,
        )

    def start(self, init_timeout=DEFAULT_REQUEST_TIMEOUT):
        with self._start_lock:
            if self.process is not None:
                return self
            self._run_dir = tempfile.mkdtemp(prefix="bbm-codex-")
            try:
                self._stderr_path = os.path.join(self._run_dir, "stderr.log")
                # The sidecar's and every thread's cwd. Empty and private on
                # purpose: `.` ran book turns wherever the user launched bbm,
                # with that directory's project config — and, before `hooks`
                # was disabled, its hook droppings.
                self._work_dir = os.path.join(self._run_dir, "work")
                os.mkdir(self._work_dir)
                servers = self._discover_mcp_servers(init_timeout)
                self._start_hardened(servers, init_timeout)
            except BaseException:
                self.close()
                raise
            return self

    def _boot(self, args, init_timeout):
        """Spawn one sidecar and complete the documented handshake."""
        with self._cond:
            self._stopped = False
            self._replies.clear()
            del self._notifications[:]
            self._consumed = 0
        try:
            self.process = self._spawn(list(args))
        except FileNotFoundError as e:
            raise CodexUnavailable(
                f"could not run the {self.binary!r} binary. Install the Codex "
                f"CLI (https://developers.openai.com/codex/cli) and make sure "
                f"`{self.binary} app-server` works."
            ) from e
        except OSError as e:
            raise CodexUnavailable(
                f"could not start `{self.binary} app-server`: {e}"
            ) from e

        self._reader = threading.Thread(
            target=self._read_loop, args=(self.process,), daemon=True
        )
        self._reader.start()
        try:
            self.request(
                "initialize",
                {"clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}},
                timeout=init_timeout,
            )
            # Required before any other request; a server may answer earlier
            # ones with "Not initialized".
            self.notify("initialized")
        except CodexError:
            # A sidecar that never finished the handshake is useless and must
            # not be left running: a retry loop would spawn one per attempt.
            self._shutdown_process()
            raise

    def _discover_mcp_servers(self, init_timeout):
        """Ask this codex build which MCP servers its config declares.

        Only names present in the config may appear in a disabling override —
        naming any other server kills startup — and `config/read` is the only
        census of them. (Its `features` dict is *not* a census of feature
        flags: a flag is listed only once explicitly set, so `shell_tool` is
        absent from a plain spawn's reply. Measured; do not "simplify" this
        into one spawn.)
        """
        self._boot((), init_timeout)
        try:
            # `{}`, not None: config/read rejects an absent params field.
            config = self.request("config/read", {}).get("config") or {}
        finally:
            self._shutdown_process()
        names = sorted(config.get("mcp_servers") or {})
        for name in names:
            if not _OVERRIDABLE_SERVER_NAME.match(name):
                raise CodexError(
                    f"the configured MCP server {name!r} cannot be disabled: "
                    f"its name cannot be written as a config override. Rename "
                    f"or remove it in the codex config, then rerun."
                )
        return names

    def _start_hardened(self, servers, init_timeout):
        disables = list(UNWANTED_FEATURES)
        overrides = []
        for name in servers:
            overrides += ["-c", f"mcp_servers.{name}.enabled=false"]
        while True:
            args = [arg for f in disables for arg in ("--disable", f)] + overrides
            try:
                self._boot(args, init_timeout)
                break
            except CodexUnavailable:
                raise
            except CodexError:
                unknown = self._unknown_feature()
                if unknown not in disables:
                    raise
                # This build has no such flag, which usually means the codex
                # release renamed or retired it. Degrade that one flag, loudly
                # — everything else stays disabled and stays verified.
                print(
                    f"bilingual-book-maker: this codex build has no feature "
                    f"flag {unknown!r}; continuing without disabling it",
                    file=sys.stderr,
                )
                disables.remove(unknown)
        self._verify_hardened(disables, servers)

    def _unknown_feature(self):
        try:
            with open(self._stderr_path) as f:
                match = _UNKNOWN_FEATURE.search(f.read())
        except OSError:
            return None
        return match.group(1) if match else None

    def _verify_hardened(self, disables, servers):
        """Prove the flags took. Loud on any gap, never half-hardened."""
        config = self.request("config/read", {}).get("config") or {}
        features = config.get("features") or {}
        still_on = [name for name in disables if features.get(name) is not False]
        if still_on:
            raise CodexError(
                f"codex did not honor --disable for: {', '.join(still_on)}. "
                f"Refusing to run book text through a sidecar holding tools."
            )
        # An empty tools list alone would not prove the override took — a
        # server that is merely slow to connect also shows none. The config is
        # the ground truth for `enabled`.
        declared = config.get("mcp_servers") or {}
        still_enabled = [
            name
            for name in servers
            if (declared.get(name) or {}).get("enabled") is not False
        ]
        if still_enabled:
            raise CodexError(
                f"codex did not honor enabled=false for the MCP server(s): "
                f"{', '.join(still_enabled)}. Refusing to run book text "
                f"through a sidecar holding tools."
            )
        cursor = None
        while True:
            result = self.request(
                "mcpServerStatus/list", {"cursor": cursor} if cursor else {}
            )
            for entry in result.get("data") or []:
                if entry.get("tools"):
                    raise CodexError(
                        f"the MCP server {entry.get('name')!r} still exposes "
                        f"tools after hardening. Refusing to run book text "
                        f"through a sidecar holding tools."
                    )
            cursor = result.get("nextCursor")
            if not cursor:
                break

    def _shutdown_process(self):
        """Stop the child and its reader; keep the run directory."""
        self._stopped = True
        process = self.process
        self.process = None
        stderr_file, self._stderr_file = self._stderr_file, None
        if stderr_file is not None:
            try:
                stderr_file.close()
            except Exception:
                pass
        if process is None:
            with self._cond:
                self._cond.notify_all()
            return
        try:
            process.terminate()
        except Exception:
            pass
        # Reap it, so a long-lived run does not accumulate zombies. A sidecar
        # that ignores SIGTERM gets killed rather than left behind.
        try:
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        for pipe in (getattr(process, "stdin", None), getattr(process, "stdout", None)):
            try:
                pipe.close()
            except Exception:
                pass
        # The reader must be gone before a later _boot resets shared state,
        # or its exit path would mark the fresh spawn stopped.
        reader, self._reader = self._reader, None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=5)
        with self._cond:
            self._cond.notify_all()

    def close(self):
        self._shutdown_process()
        run_dir, self._run_dir = self._run_dir, None
        self._work_dir = None
        self._stderr_path = None
        if run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- transport --------------------------------------------------------

    def _read_loop(self, process):
        try:
            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue  # the sidecar also prints non-protocol chatter
                deny_id = None
                with self._cond:
                    if "id" in message and ("result" in message or "error" in message):
                        self._replies[message["id"]] = message
                    elif "method" in message and "id" in message:
                        # A ServerRequest (an approval). We run read-only with
                        # approvals off, so one arriving is unplanned: refuse
                        # it rather than let the turn block on it.
                        deny_id = message["id"]
                    elif "method" in message:
                        if message["method"] == "account/rateLimits/updated":
                            self._merge_rate_limits(message)
                        self._notifications.append(message)
                    self._cond.notify_all()
                if deny_id is not None:
                    # Off this thread entirely: the denial writes to stdin,
                    # stdin can backpressure, and this is the only stdout
                    # reader. Blocking here while the sidecar blocks writing
                    # stdout is a deadlock, so hand it to a throwaway thread
                    # and keep draining.
                    threading.Thread(
                        target=self._deny, args=(deny_id,), daemon=True
                    ).start()
        except (ValueError, OSError):
            pass  # process went away; waiters fail on their own timeouts
        finally:
            with self._cond:
                # Only for the process this loop was reading. During the
                # two-phase start a superseded reader may exit after the next
                # spawn is already up, and must not mark *it* stopped.
                if self.process is process or self.process is None:
                    self._stopped = True
                self._cond.notify_all()

    def _merge_rate_limits(self, message):
        """Fold a pushed update into the snapshot. Caller holds `_cond`."""
        snapshot = (message.get("params") or {}).get("rateLimits")
        if not snapshot:
            return
        update = RateLimits.from_snapshot(snapshot)
        self._rate_limits = (
            update
            if self._rate_limits is None
            else self._rate_limits.merged_with(update)
        )

    def latest_rate_limits(self):
        """The most recent quota snapshot, or None before one has arrived."""
        with self._cond:
            return self._rate_limits

    def _deny(self, request_id):
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "bilingual-book-maker runs codex non-interactively",
                    },
                }
            )
        except CodexError:
            pass  # the connection is already gone; waiters will time out

    def _write(self, payload):
        process = self.process
        if process is None:
            raise CodexError("the codex app-server is not running")
        try:
            # One writer at a time: a TextIOWrapper gives no interleaving
            # guarantee, and a half-written line would corrupt the stream for
            # every later request.
            with self._write_lock:
                process.stdin.write(json.dumps(payload) + "\n")
                process.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise CodexError(f"lost the connection to codex app-server: {e}") from e

    def notify(self, method, params=None):
        """Send a notification: a method with no id, expecting no reply."""
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

    def request(self, method, params=None, timeout=DEFAULT_REQUEST_TIMEOUT):
        """Send a request and wait for its reply. Raises on an error reply."""
        with self._cond:
            self._next_id += 1
            request_id = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )

        with self._cond:
            if not self._cond.wait_for(
                lambda: request_id in self._replies or self._stopped, timeout=timeout
            ):
                raise CodexError(f"codex did not answer {method} within {timeout}s")
            message = self._replies.pop(request_id, None)
        if message is None:
            raise CodexError(f"codex app-server stopped before answering {method}")
        if "error" in message:
            raise CodexError(
                f"{method} failed: {message['error'].get('message', message['error'])}"
            )
        return message.get("result") or {}

    # ---- account ----------------------------------------------------------

    def rate_limits(self):
        """Current quota, or None when the server reports none."""
        result = self.request("account/rateLimits/read")
        snapshot = result.get("rateLimits")
        if not snapshot:
            return None
        # A full read replaces the snapshot; rolling updates only merge.
        limits = RateLimits.from_snapshot(snapshot)
        with self._cond:
            self._rate_limits = limits
        return limits

    def ensure_logged_in(self):
        """Confirm a usable session, or say exactly how to get one.

        Reading the quota is the cheapest call that needs an account, so an
        error here is the login signal.
        """
        try:
            return self.rate_limits()
        except CodexError as e:
            raise CodexLoginRequired(
                f"codex is not signed in to ChatGPT ({e}). Sign in with "
                f"`codex login`, then run this again."
            ) from e

    # ---- threads and turns ------------------------------------------------

    def start_thread(self, model=None, base_instructions=None):
        """A new thread configured to answer, not to act.

        `read-only` + `never` is what keeps a turn shaped like a chat
        completion; `ephemeral` keeps translation runs out of the user's
        Codex thread history. The cwd is not a parameter on purpose: every
        thread runs in the private work directory, so no caller can point a
        turn at the user's files or a directory carrying project config.
        """
        params = {
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": True,
            "cwd": self._work_dir,
        }
        if model:
            params["model"] = model
        if base_instructions:
            params["baseInstructions"] = base_instructions
        result = self.request("thread/start", params)
        thread_id = (result.get("thread") or {}).get("id")
        if not thread_id:
            raise CodexError(f"thread/start returned no thread id: {result!r}")
        return thread_id

    def run_turn(self, thread_id, text, output_schema=None, timeout=None):
        """One turn; returns the final assistant message.

        The reply is a notification that can land before `turn/start`'s own
        response is processed, so the notification list is marked *before*
        sending and scanned from that mark.
        """
        timeout = DEFAULT_TURN_TIMEOUT if timeout is None else timeout
        # Reserved before the request goes out, not when the wait starts: in
        # the gap between the two, another turn's prune could drop the very
        # completion this call is about to look for.
        with self._reserve_mark() as mark:
            return self._run_turn_from(mark, thread_id, text, output_schema, timeout)

    def _run_turn_from(self, mark, thread_id, text, output_schema, timeout):
        params = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
        if output_schema is not None:
            params["outputSchema"] = output_schema
        result = self.request("turn/start", params)
        turn_id = (result.get("turn") or {}).get("id")

        note = self._await_notification(
            lambda m: (
                m.get("method") == "turn/completed"
                and (
                    turn_id is None
                    or (m.get("params", {}).get("turn") or {}).get("id") == turn_id
                )
            ),
            timeout=timeout,
            mark=mark,
        )
        # Whatever the outcome, this turn's notifications are finished with. A
        # book is thousands of turns and every `turn/completed` carries the
        # full translated text, so keeping them would hold most of the book in
        # memory a second time.
        self._prune_notifications()
        if note is None:
            raise CodexTurnFailed(
                f"the codex turn timed out after {timeout}s with no answer"
            )
        return self._turn_text(note["params"].get("turn") or {})

    @contextmanager
    def _reserve_mark(self):
        """Hold a position in the notification stream so pruning cannot pass it."""
        with self._cond:
            mark = self._consumed + len(self._notifications)
            self._active_marks.append(mark)
        try:
            yield mark
        finally:
            with self._cond:
                self._active_marks.remove(mark)

    def _prune_notifications(self):
        """Drop notifications no live waiter can still be scanning for.

        Marks are absolute positions in the whole stream, so pruning is safe
        as long as nothing is dropped below the earliest mark still in use.
        """
        with self._cond:
            floor = min(
                self._active_marks, default=self._consumed + len(self._notifications)
            )
            drop = floor - self._consumed
            if drop > 0:
                del self._notifications[:drop]
                self._consumed += drop

    @staticmethod
    def _turn_text(turn):
        status = turn.get("status")
        if status != "completed":
            message = (turn.get("error") or {}).get("message") or status
            raise CodexTurnFailed(f"the codex turn did not complete: {message}")
        for item in turn.get("items") or []:
            if item.get("type") == "agentMessage" and item.get("text"):
                return item["text"]
        raise CodexTurnFailed("the codex turn produced no assistant message")

    def _await_notification(self, matches, timeout, mark):
        """Wait for the first notification at or after `mark` that matches.

        `mark` is an absolute position in the whole notification stream, so it
        stays valid across pruning; `_consumed` converts it to a list index.
        The mark is registered while waiting so pruning cannot drop out from
        under this scan.
        """

        def found():
            start = max(0, mark - self._consumed)
            return next((m for m in self._notifications[start:] if matches(m)), None)

        with self._cond:
            if not self._cond.wait_for(
                lambda: found() is not None or self._stopped, timeout=timeout
            ):
                return None
            return found()
