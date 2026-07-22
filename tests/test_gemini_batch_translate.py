import json

from book_maker.translator.gemini_translator import Gemini


def _make_gemini():
    """Build a Gemini instance without running __init__ (no API client / network)."""
    g = Gemini.__new__(Gemini)
    g.prompt = "Translate `{text}` to {language}"
    g.language = "ja"
    g._fatal_error_detected = False
    return g


def _stub_retry(g, captured):
    """Replace the network call: record args, echo one translation per requested item."""

    def fake(prompt, expected_count):
        captured["prompt"] = prompt
        captured["expected_count"] = expected_count
        return [f"t{i}" for i in range(expected_count)]

    g._batch_translate_with_retry = fake


# 1. Regression: an empty paragraph is not sent and is preserved in place.
def test_empty_paragraph_not_sent_and_preserved():
    g = _make_gemini()
    captured = {}
    _stub_retry(g, captured)

    result = g._batch_translate(["A", "", "B"], batch_size=3)

    assert captured["expected_count"] == 2
    assert "A" in captured["prompt"] and "B" in captured["prompt"]
    assert result == ["t0", "", "t1"]


# 2. All paragraphs empty: API is not called, originals returned unchanged.
def test_all_empty_returns_originals_without_api_call():
    g = _make_gemini()
    called = {"n": 0}

    def fake(prompt, expected_count):
        called["n"] += 1
        return []

    g._batch_translate_with_retry = fake

    original = ["", "  ", "\n"]
    result = g._batch_translate(original, batch_size=3)

    assert called["n"] == 0
    assert result == original


# 3. No empty paragraphs: all sent, order preserved (normal-path regression).
def test_no_empty_translates_all_in_order():
    g = _make_gemini()
    captured = {}
    _stub_retry(g, captured)

    result = g._batch_translate(["A", "B", "C"], batch_size=3)

    assert captured["expected_count"] == 3
    assert result == ["t0", "t1", "t2"]


# 4. Empties at edges / consecutive: translations land at correct indices.
def test_empty_at_edges_and_consecutive_keep_positions():
    g = _make_gemini()
    captured = {}
    _stub_retry(g, captured)

    result = g._batch_translate(["", "A", "", "", "B"], batch_size=5)

    assert captured["expected_count"] == 2
    assert result == ["", "t0", "", "", "t1"]


# 5. Whitespace-only variants count as empty.
def test_whitespace_only_treated_as_empty():
    g = _make_gemini()
    captured = {}
    _stub_retry(g, captured)

    result = g._batch_translate([" ", "\t", "X", "\n"], batch_size=4)

    assert captured["expected_count"] == 1
    assert result == [" ", "\t", "t0", "\n"]


# 6. Count check stays strict: a short response is rejected, an exact one accepted.
def test_parse_rejects_short_response():
    g = _make_gemini()
    short = json.dumps({"translated_paragraphs": ["only-one"]})
    assert g._parse_batch_response(short, expected_count=2) is None


def test_parse_accepts_exact_response():
    g = _make_gemini()
    ok = json.dumps({"translated_paragraphs": ["a", "b"]})
    assert g._parse_batch_response(ok, expected_count=2) == ["a", "b"]


# 7. translate_list dispatch: 0 -> [], 1 -> translate(), many -> _batch_translate().
def test_translate_list_empty_returns_empty():
    g = _make_gemini()
    assert g.translate_list([]) == []


def test_translate_list_single_uses_translate():
    g = _make_gemini()
    g.translate = lambda text: f"single:{text}"
    assert g.translate_list(["hello"]) == ["single:hello"]


def test_translate_list_multiple_uses_batch():
    g = _make_gemini()
    seen = {}

    def fake_batch(text_list, batch_size):
        seen["batch_size"] = batch_size
        return ["x"] * batch_size

    g._batch_translate = fake_batch

    result = g.translate_list(["a", "b"])

    assert seen["batch_size"] == 2
    assert result == ["x", "x"]


# 8. Fatal error already detected: return error markers sized to batch_size.
def test_fatal_error_returns_markers():
    g = _make_gemini()
    g._fatal_error_detected = True

    result = g._batch_translate(["A", "B", "C"], batch_size=3)

    assert result == [Gemini.TRANSLATION_ERROR_MARKER] * 3
