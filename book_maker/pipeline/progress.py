"""One line that says a slow stage is still moving.

Extraction is minutes of somebody else's work with nothing of ours in
between: docling's layout, table and OCR models reading the PDF, with the
models downloaded on the first run. It reports no page count as it goes, so
what can honestly be shown is the stage, how long it has been running, and
the last thing it said for itself.

The renderer has no opinion about what it prints: it takes a label, an
elapsed clock and an optional detail, and decides only *when* and *how* the
line appears. A terminal gets one line rewritten in place; a log file (or a
pipe, or CI) gets a fresh line every ten seconds instead, because carriage
returns in a file produce one unreadable megabyte-long line and a progress
display nobody can read is worse than silence.

Nothing here starts a thread of its own. `ticking` does, for the caller that
has no code of its own running while it waits.
"""

import sys
import threading
import time
from contextlib import contextmanager

from .messages import PROGRESS_LINE, PROGRESS_LINE_DETAIL

# How often the line is redrawn: every second on a terminal, where it costs
# nothing, and every ten in a log, where every line is kept forever.
TTY_INTERVAL = 1.0
LOG_INTERVAL = 10.0

# How much of another program's log line is worth carrying. Long enough for
# a sentence, short enough to leave a terminal line intact beside the label.
DETAIL_WIDTH = 80


class ProgressLine:
    """A live "still working" line, rewritten on a TTY and logged otherwise.

    `note` records what the stage last said; `tick` renders it if enough
    time has passed. The two are separate because the notes arrive whenever
    another process feels like speaking -- five in one millisecond and then
    nothing for half a minute -- and the operator needs the clock moving in
    between.
    """

    def __init__(
        self,
        label,
        *,
        stream=None,
        clock=time.monotonic,
        isatty=None,
        tty_interval=TTY_INTERVAL,
        log_interval=LOG_INTERVAL,
        enabled=True,
    ):
        self.label = label
        # Resolved now, not at render time: extraction redirects `sys.stdout`
        # to capture the converter's own chatter, and the progress line must
        # keep going to the real one.
        self.stream = stream if stream is not None else sys.stdout
        self.clock = clock
        self.enabled = enabled
        if isatty is None:
            try:
                isatty = bool(self.stream.isatty())
            except (AttributeError, ValueError):
                isatty = False
        self.isatty = isatty
        self.interval = tty_interval if isatty else log_interval
        self.detail = None
        self._lock = threading.Lock()
        self._started = None
        self._last = None
        self._width = 0

    def elapsed(self):
        return 0.0 if self._started is None else self.clock() - self._started

    def start(self):
        with self._lock:
            self._started = self.clock()
            self._last = None
        self.tick(force=True)
        return self

    def note(self, detail):
        """Remember the last meaningful thing the stage said."""
        if not detail:
            return
        detail = str(detail).strip()
        if not detail:
            return
        with self._lock:
            self.detail = detail[:DETAIL_WIDTH]

    def text(self):
        fields = {
            "label": self.label,
            "elapsed": int(self.elapsed()),
            "detail": self.detail,
        }
        template = PROGRESS_LINE_DETAIL if self.detail else PROGRESS_LINE
        return template.format(**fields)

    def tick(self, force=False):
        """Render if the interval has elapsed. Safe to call as often as liked."""
        if not self.enabled or self._started is None:
            return
        with self._lock:
            now = self.clock()
            if (
                not force
                and self._last is not None
                and now - self._last < self.interval
            ):
                return
            self._last = now
            self._write(self.text())

    def finish(self, message=None):
        """Close the line, leaving `message` as the record of the stage."""
        if not self.enabled:
            return
        with self._lock:
            self._started = None
            if self.isatty and self._width:
                # Erase the transient line rather than leave half of a longer
                # one behind the shorter message that replaces it.
                self.stream.write("\r" + " " * self._width + "\r")
                self._width = 0
            if message:
                self.stream.write(f"{message}\n")
            self._flush()

    def _write(self, text):
        if self.isatty:
            padding = " " * max(0, self._width - len(text))
            self.stream.write(f"\r{text}{padding}")
            self._width = len(text)
        else:
            self.stream.write(f"{text}\n")
        self._flush()

    def _flush(self):
        try:
            self.stream.flush()
        except (AttributeError, ValueError):
            pass

    def __enter__(self):
        return self.start()

    def __exit__(self, *exception):
        self.finish()
        return False


@contextmanager
def ticking(line, poll=None, every=0.25):
    """Drive `line` from a background thread while the caller waits.

    The caller is blocked inside somebody else's `convert()` for the whole
    stage, so nothing of ours runs to redraw the clock; this thread does it,
    and calls `poll` (reading the backend's log) on the same beat. A daemon
    thread: an interrupted run must not wait for it.
    """
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            if poll is not None:
                try:
                    poll()
                except Exception:
                    # A progress display must never be the reason a
                    # conversion fails; it stops being updated instead.
                    return
            line.tick()
            stop.wait(every)

    thread = threading.Thread(target=loop, name="pipeline-progress", daemon=True)
    thread.start()
    try:
        yield line
    finally:
        stop.set()
        thread.join(timeout=max(every * 4, 1.0))
        if poll is not None:
            # Whatever arrived between the last beat and the end of the
            # stage still belongs to the failure message.
            try:
                poll()
            except Exception:
                pass
