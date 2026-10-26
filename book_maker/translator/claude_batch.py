"""Anthropic's Message Batches API under the ordinary translation loop.

`--batch-api` (anthropic format, Markdown books and the `--to-epub` route,
no context): every translation request is sent as part of one Message
Batch at half price, instead of one request at a time. Nothing in the
loader's translation code is duplicated for it. The run goes through the
book twice:

1. collect -- the loader translates as usual, but the Claude translator
   hands each request to this queue (`Claude._create`), which records the
   request and raises `BatchPending` instead of sending it. The loader
   skips that batch and goes on.
2. replay -- after the batch has been submitted, has ended and its results
   are stored, the loader translates again. Each request that was in the
   batch gets its stored reply; anything new (the loader's repair of a
   misaligned reply, one paragraph at a time) and anything the batch did
   not answer usably is sent live.

A request is identified by the hash of its effective body, the JSON the
SDK would send, so the replay finds exactly what the collection recorded.
That only holds when a request depends on nothing but its own text, which
is why `--use_context` is refused with the flag.

The submission costs money, so it is guarded like a Datalab submission: a
`submitting` record is on disk before the create call goes out, the call is
made once (no SDK retry), and a create whose outcome is unknown leaves the
record and refuses to submit again until a person has looked. The batch id
is written the moment it exists; a rerun polls that batch instead of
submitting another. Polling and reading results are patient (owner ruling
260907: never give up while the error is not fatal).
"""

import hashlib
import json
import os
import time
from pathlib import Path

from rich import print

# Anthropic's limits are 100,000 requests or 256 MB per batch; well under
# both, so one book is a handful of batches at most and a request body
# never has to be measured against the edge.
MAX_REQUESTS_PER_BATCH = 10_000
MAX_BYTES_PER_BATCH = 100 * 1024 * 1024

POLL_SECONDS = 30
# Waits after a failed poll, doubling, capped per wait and never in count.
RETRY_MIN_SECONDS = 5
RETRY_MAX_SECONDS = 300

SUBMIT_UNKNOWN = (
    "The Message Batch submission recorded in {path} has no batch id: the "
    "create request was sent and its outcome is unknown, so it may have been "
    "accepted and billed. Check the batches in the Anthropic console "
    "(https://console.anthropic.com/workspaces/default/batches). If one was "
    'created, put its id into the file\'s "batches" list; if none was, '
    'delete the "submitting" entry. Then run again.'
)
STATE_OTHER_ENDPOINT = (
    "{path} records Message Batches made at {old} with {old_model}; this run "
    "uses {new} with {new_model}. Move the file aside to start new batches."
)


class BatchPending(BaseException):
    """Control flow, not a failure: the request was recorded for the batch.

    A BaseException so that no `except Exception` on the way out -- a
    retry, a fallback to single paragraphs, the loader's own handler --
    can mistake it for an API error and act on it.
    """


class BatchFailed(Exception):
    """The batch could not be submitted or read; the message says why."""


def effective_body(params):
    """The JSON body the SDK would send for `messages.create(**params)`.

    `extra_body` is the SDK's merge into the top level of the request, so
    it is merged here too; absent values are dropped as the SDK drops
    them. This is both what is hashed and what a batch request carries.
    """
    body = {
        key: value
        for key, value in params.items()
        if key not in ("extra_body", "extra_headers", "timeout") and value is not None
    }
    body.update(params.get("extra_body") or {})
    return body


def request_key(body):
    """A batch `custom_id` (64 hex characters) for one request body."""
    canonical = json.dumps(
        body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def usable(result):
    """Whether a stored result can stand in for a live reply.

    Succeeded, finished on its own (`end_turn`) and carrying text. A reply
    cut off at max_tokens, a refusal, an errored, expired or cancelled
    request is not a translation; the replay sends that request live.
    """
    if not result or result.get("type") != "succeeded":
        return False
    message = result.get("message") or {}
    if message.get("stop_reason") != "end_turn":
        return False
    return any(
        block.get("type") == "text" and block.get("text")
        for block in message.get("content") or []
    )


class ClaudeBatchQueue:
    """One book's Message Batches: what was asked, sent and answered."""

    COLLECT = "collect"
    REPLAY = "replay"

    def __init__(self, state_path, *, endpoint, model, sleep=time.sleep):
        self.state_path = Path(state_path)
        self.endpoint = endpoint
        self.model = model
        self.mode = self.COLLECT
        self.pending = {}
        self.sleep = sleep
        self.state = self._load()

    # -- the translator's side ----------------------------------------
    def request(self, params):
        """The stored reply for `params` as a `Message`, or None to go live.

        In collect mode a request with no stored result is recorded and
        `BatchPending` raised; one whose stored result is unusable is
        neither recorded again nor sent -- the replay sends it live.
        """
        body = effective_body(params)
        key = request_key(body)
        result = self.state["results"].get(key)
        if usable(result):
            from anthropic.types import Message

            return Message.model_validate(result["message"])
        if self.mode == self.COLLECT:
            if result is None and not self._submitted(key):
                self.pending[key] = body
            raise BatchPending(key)
        return None

    # -- the loader's side --------------------------------------------
    def run(self, client, note_usage=None):
        """Submit what was collected, wait for every batch, store results.

        `note_usage(message)` is called once per stored reply, so the run's
        token count includes what the batch billed (at half the price the
        meter assumes).
        """
        self._refuse_unknown_submission()
        keys = [key for key in self.pending if not self._submitted(key)]
        for chunk in self._chunks(keys):
            self._submit(client, chunk)
        for batch in self.state["batches"]:
            if batch.get("collected"):
                continue
            self._wait(client, batch)
            self._collect(client, batch, note_usage)
        self.pending = {}
        self.mode = self.REPLAY

    def summary(self):
        results = self.state["results"].values()
        good = sum(1 for result in results if usable(result))
        return good, len(self.state["results"]) - good

    # -- state ----------------------------------------------------------
    def _load(self):
        if self.state_path.is_file():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (state.get("endpoint"), state.get("model")) != (
                self.endpoint,
                self.model,
            ):
                raise BatchFailed(
                    STATE_OTHER_ENDPOINT.format(
                        path=self.state_path,
                        old=state.get("endpoint"),
                        old_model=state.get("model"),
                        new=self.endpoint,
                        new_model=self.model,
                    )
                )
            state.setdefault("batches", [])
            state.setdefault("results", {})
            state.setdefault("submitting", None)
            return state
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "batches": [],
            "submitting": None,
            "results": {},
        }

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.state_path.with_name(self.state_path.name + ".part")
        partial.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(partial, self.state_path)

    def _submitted(self, key):
        return any(key in batch.get("keys", ()) for batch in self.state["batches"])

    def _refuse_unknown_submission(self):
        if self.state.get("submitting"):
            raise BatchFailed(SUBMIT_UNKNOWN.format(path=self.state_path))

    def _chunks(self, keys):
        chunk, size = [], 0
        for key in keys:
            length = len(json.dumps(self.pending[key], ensure_ascii=False)) + 100
            if chunk and (
                len(chunk) >= MAX_REQUESTS_PER_BATCH
                or size + length > MAX_BYTES_PER_BATCH
            ):
                yield chunk
                chunk, size = [], 0
            chunk.append(key)
            size += length
        if chunk:
            yield chunk

    # -- the API ----------------------------------------------------------
    def _submit(self, client, keys):
        from anthropic import APIStatusError

        self.state["submitting"] = {
            "keys": keys,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save()
        requests = [{"custom_id": key, "params": self.pending[key]} for key in keys]
        try:
            # Once: an SDK retry after a timeout could create a second,
            # billed batch for the same requests.
            batch = client.with_options(
                max_retries=0, timeout=600
            ).messages.batches.create(requests=requests)
        except APIStatusError as err:
            if 400 <= err.status_code < 500 and err.status_code not in (408, 409, 429):
                # Refused outright: nothing was created, nothing billed.
                self.state["submitting"] = None
                self._save()
                raise BatchFailed(
                    f"Anthropic refused the Message Batch ({err.status_code}): {err}"
                ) from err
            raise BatchFailed(SUBMIT_UNKNOWN.format(path=self.state_path)) from err
        except Exception as err:
            raise BatchFailed(SUBMIT_UNKNOWN.format(path=self.state_path)) from err
        self.state["batches"].append(
            {
                "id": batch.id,
                "keys": keys,
                "submitted_at": self.state["submitting"]["at"],
            }
        )
        self.state["submitting"] = None
        self._save()
        print(
            f"Message Batch {batch.id}: {len(keys)} requests submitted "
            f"(results within 24 hours, usually minutes; state in "
            f"{self.state_path})"
        )

    def _patiently(self, what, call):
        """`call()`, retried on anything but a refusal of the credentials."""
        from anthropic import APIStatusError

        wait = RETRY_MIN_SECONDS
        while True:
            try:
                return call()
            except APIStatusError as err:
                if err.status_code in (401, 403, 404):
                    raise BatchFailed(f"{what}: {err}") from err
                reason = f"HTTP {err.status_code}"
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as err:
                reason = f"{type(err).__name__}: {err}"
            print(f"[yellow]{what} failed ({reason}); retrying in {wait}s[/yellow]")
            self.sleep(wait)
            wait = min(wait * 2, RETRY_MAX_SECONDS)

    def _wait(self, client, batch):
        said = None
        while True:
            current = self._patiently(
                f"reading Message Batch {batch['id']}",
                lambda: client.messages.batches.retrieve(batch["id"]),
            )
            if current.processing_status == "ended":
                return
            counts = current.request_counts
            line = (
                f"Message Batch {batch['id']}: {current.processing_status}, "
                f"{counts.processing} processing, {counts.succeeded} succeeded, "
                f"{counts.errored} errored"
            )
            if line != said:
                print(line)
                said = line
            self.sleep(POLL_SECONDS)

    def _collect(self, client, batch, note_usage):
        def read():
            return list(client.messages.batches.results(batch["id"]))

        entries = self._patiently(f"reading the results of {batch['id']}", read)
        counts = {}
        for entry in entries:
            result = entry.result
            stored = {"type": result.type}
            if result.type == "succeeded":
                stored["message"] = result.message.model_dump(mode="json")
                if note_usage is not None:
                    note_usage(result.message)
            elif result.type == "errored":
                stored["error"] = result.error.model_dump(mode="json")
            self.state["results"][entry.custom_id] = stored
            kind = (
                result.type
                if usable(stored) or result.type != "succeeded"
                else (f"succeeded but {result.message.stop_reason}")
            )
            counts[kind] = counts.get(kind, 0) + 1
        batch["collected"] = True
        self._save()
        tally = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
        print(f"Message Batch {batch['id']} ended: {tally or 'no results'}")
