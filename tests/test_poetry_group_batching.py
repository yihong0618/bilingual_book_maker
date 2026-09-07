"""A batch must reach the model as one request, on every LLM route.

`--poetry-group-size` exists so runs of short units are translated with their
neighbours in view. The grouping happens in the plan, but a batch only
becomes a single request if the translator's `translate_list` batches — the
base implementation loops and translates each line alone, which dissolves the
group and gives the flag nothing to do. These tests hold `claude` and `codex`
to the contract every LLM route now keeps: N lines in, exactly N lines out
and in order, **or `BatchMismatch`**. No route repairs a bad reply itself any
more; the loader's `_translate_texts_aligned` ladder halves the chunk, which
costs about twice the batch instead of N singles on top of it.
"""

import re
from types import SimpleNamespace

import pytest

from book_maker.translator.base_translator import BATCH_DELIMITER, BatchMismatch
from book_maker.translator.claude_translator import Claude
from book_maker.translator.codex_translator import Codex

STANZA = ["Tyger Tyger", "burning bright", "In the forests", "of the night"]


# ------------------------------------------------------------------ claude


def _claude_payload(request):
    """The source text inside the prompt template's triple backticks."""
    content = request["messages"][-1]["content"]
    match = re.search(r"```(.*)```", content, re.DOTALL)
    return match.group(1) if match else content


def _claude(replies=(), **kwargs):
    """A Claude whose only network call is replaced by a scripted stub.

    There is no anthropic key here; `Anthropic(...)` is built by __init__ but
    never reached, since `client` is swapped before any translation.
    """
    claude = Claude("k", "Chinese", **kwargs)
    calls = []
    queued = list(replies)

    def create(**request):
        calls.append(request)
        text = queued.pop(0) if queued else f"译[{_claude_payload(request)}]"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])

    claude.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return claude, calls


def test_claude_sends_a_window_as_one_request():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, calls = _claude([reply])

    assert claude.translate_list(STANZA) == ["虎", "灼灼", "林中", "夜里"]

    assert len(calls) == 1
    sent = _claude_payload(calls[0])
    # every line in the one request, in order, joined by the shared delimiter
    assert sent == BATCH_DELIMITER.join(STANZA)


def test_claude_short_reply_raises_for_the_loader_to_divide():
    # one paragraph back for four lines: no split of it yields four, so
    # accepting it would put the whole stanza on the first line
    claude, calls = _claude(["虎啊虎啊，燃烧在夜的森林里"])

    with pytest.raises(BatchMismatch):
        claude.translate_list(STANZA)

    # the batch attempt and nothing else: no self-repair, no retry of the
    # same group — the loader halves it instead
    assert len(calls) == 1


def test_claude_over_long_reply_raises_for_the_loader_to_divide():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜", "里"])
    claude, calls = _claude([reply])

    with pytest.raises(BatchMismatch):
        claude.translate_list(STANZA)

    assert len(calls) == 1


def test_claude_empty_slot_for_a_non_empty_line_raises():
    # count is not alignment: a merged pair keeps the count by padding
    reply = BATCH_DELIMITER.join(["虎灼灼", "", "林中", "夜里"])
    claude, calls = _claude([reply])

    with pytest.raises(BatchMismatch):
        claude.translate_list(STANZA)

    assert len(calls) == 1


def test_claude_single_line_group_takes_the_plain_path():
    claude, calls = _claude()

    assert claude.translate_list(["burning bright"]) == ["译[burning bright]"]

    assert len(calls) == 1
    content = calls[0]["messages"][-1]["content"]
    assert BATCH_DELIMITER.strip() not in content
    assert "segments" not in content


def test_claude_saves_context_per_line_not_per_window():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, _ = _claude([reply], context_flag=True, context_paragraph_limit=8)

    claude.translate_list(STANZA)

    # one pair per line — a single entry full of "@@" would evict four real
    # paragraphs of context and feed the marker back to the model
    assert claude.context_list == STANZA
    assert claude.context_translated_list == ["虎", "灼灼", "林中", "夜里"]


def test_claude_restores_its_prompt_after_a_window():
    claude, _ = _claude([BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])])
    before = (claude.prompt_template, claude.prompt_sys_msg)

    claude.translate_list(STANZA)

    assert (claude.prompt_template, claude.prompt_sys_msg) == before


# ------------------------------------------------------------------- codex


class FakeServer:
    """Stands in for the codex sidecar; scripts the replies, records turns."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.turns = []
        self.threads = []

    def start(self):
        return self

    def close(self):
        pass

    def latest_rate_limits(self):
        return None

    def rate_limits(self):
        return None

    def start_thread(self, model=None, base_instructions=None, cwd=None):
        self.threads.append(base_instructions)
        return f"th-{len(self.threads)}"

    def run_turn(self, thread_id, text, output_schema=None, timeout=None):
        self.turns.append(text)
        return self.replies.pop(0) if self.replies else f"译[{text}]"


def _codex(replies=(), **kwargs):
    server = FakeServer(replies)
    return Codex(key="", language="Chinese", server=server, **kwargs), server


def test_codex_sends_a_window_as_one_turn():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    codex, server = _codex([reply])

    assert codex.translate_list(STANZA) == ["虎", "灼灼", "林中", "夜里"]

    assert len(server.turns) == 1
    assert BATCH_DELIMITER.join(STANZA) in server.turns[0]
    # the turn has to say what shape the reply must come back in
    assert "4" in server.turns[0]


def test_codex_short_reply_raises_for_the_loader_to_divide():
    codex, server = _codex(["虎啊虎啊，燃烧在夜的森林里"])

    with pytest.raises(BatchMismatch):
        codex.translate_list(STANZA)

    assert len(server.turns) == 1


def test_codex_over_long_reply_raises_for_the_loader_to_divide():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜", "里"])
    codex, server = _codex([reply])

    with pytest.raises(BatchMismatch):
        codex.translate_list(STANZA)

    assert len(server.turns) == 1


def test_codex_single_line_group_takes_the_plain_path():
    codex, server = _codex()

    assert codex.translate_list(["burning bright"]) == ["译[burning bright]"]

    # the bare line, exactly as an ungrouped unit is sent
    assert server.turns == ["burning bright"]


def test_codex_thread_instructions_survive_a_window():
    """The batch system message must not leak into the next thread.

    A window borrows `prompt_sys_msg` for the length of one request; codex
    reads that attribute when it opens a thread, so a value left behind would
    describe "@@"-separated segments to every unit of the next window.
    """
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    codex, server = _codex([reply])

    codex.translate_list(STANZA)
    codex.translate("of the night")

    assert codex.prompt_sys_msg is None
    assert codex.prompt_template is None
    assert len(server.threads) == 1
    assert BATCH_DELIMITER.strip() not in server.threads[0]


# --------------------------------------------------- claude in session mode


def _session_claude(replies=(), **kwargs):
    """A session-mode Claude on the same scripted stub."""
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    return _claude(replies, **kwargs)


def _extends(calls):
    """Whether each request's messages are the next request's opening prefix."""
    return all(
        later["messages"][: len(earlier["messages"])] == earlier["messages"]
        for earlier, later in zip(calls, calls[1:])
    )


def test_claude_session_keeps_the_system_message_byte_identical():
    """The anthropic cache covers the system message, so borrowing it for one
    grouped request moves the prefix and every later request re-reads the whole
    accumulated history at full input price."""
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, calls = _session_claude(["一", reply, "二"], prompt_sys_msg="be terse")

    claude.translate("Tyger Tyger")
    claude.translate_list(STANZA)
    claude.translate("of the night")

    assert [c["system"] for c in calls] == ["be terse"] * 3


def test_claude_session_still_states_the_batch_contract():
    """It moves into the user message; it does not go away."""
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, calls = _session_claude([reply], prompt_sys_msg="be terse")

    claude.translate_list(STANZA)

    content = calls[0]["messages"][-1]["content"]
    assert "4" in content
    assert BATCH_DELIMITER.strip() in content


def test_claude_session_records_the_group_as_the_one_exchange_it_was():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, _ = _session_claude([reply])

    claude.translate_list(STANZA)

    # one pair, not four: the history's job is to replay what was sent, and
    # what was sent was a single joined request
    assert len(claude.session.messages()) == 2
    assert BATCH_DELIMITER.join(STANZA) in claude.session.messages()[0]["content"]


def test_claude_session_prefix_survives_a_grouped_request():
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, calls = _session_claude(["一", reply, "二"])

    claude.translate("Tyger Tyger")
    claude.translate_list(STANZA)
    claude.translate("of the night")

    assert _extends(calls), "a grouped request broke the cached prefix"


def test_claude_session_does_not_record_a_failed_batch():
    """A misaligned exchange in the prefix is worse than one cache miss.

    Pinned decision: the failed batch request *was* sent, and recording it
    would keep the history truthful — but it would also seed every later
    request with an answer we refused to use, and the divide's sub-batches
    then extend a prefix built on it. Not appended.
    """
    claude, calls = _session_claude(["虎啊虎啊，燃烧在夜的森林里"])

    with pytest.raises(BatchMismatch):
        claude.translate_list(STANZA)

    assert claude.session.messages() == []
    assert len(calls) == 1


def test_claude_session_prefix_survives_the_divide_that_follows():
    """The loader's halves extend the prefix, because the failure left none."""
    good = BATCH_DELIMITER.join(["虎", "灼灼"])
    claude, calls = _session_claude(["虎啊虎啊，燃烧在夜的森林里", good, good])

    with pytest.raises(BatchMismatch):
        claude.translate_list(STANZA)
    claude.translate_list(STANZA[:2])
    claude.translate_list(STANZA[2:])

    assert _extends(calls[1:]), "the halves did not extend one another"


def test_claude_window_mode_still_borrows_the_system_message():
    """Window mode has no prefix to protect; nothing here changes for it."""
    reply = BATCH_DELIMITER.join(["虎", "灼灼", "林中", "夜里"])
    claude, calls = _claude([reply], prompt_sys_msg="be terse")

    claude.translate_list(STANZA)

    assert "segments" in calls[0]["system"]
    assert claude.prompt_sys_msg == "be terse"
