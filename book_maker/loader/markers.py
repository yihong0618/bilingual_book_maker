"""Atomic inline markers: placeholders for content the model must not see.

An excluded ``<code>``, a rendered ``<img>``, a skipped note reference sitting
mid-sentence used to be a *barrier*: the owner's run was cut in two, and
``Press <code>Ctrl+C</code> to stop it.`` reached the model as two fragments
translated blind to each other. The barrier only ever existed so the
translation could be written back in the right place — not because the model
had to be spared the sentence.

So short protected inline content becomes a token instead:

    Press ⟦code1⟧ to stop it now.

The token carries no characters of its own (the excluded text stays accounted
under its skip reason), the model is told to keep it where it belongs, and the
write-back replaces it with a clone of the original node.

Two hardenings borrowed from BabelDOC, both proven necessary in the field:

*Collision avoidance* — a token that already appears verbatim in the source
text is not a placeholder, it is ambiguity. Generation renumbers until the
chosen token occurs exactly once in the text that will be sent.

*Hallucination scrubbing* — a marker-shaped token in the reply that was never
sent is stripped rather than restored: it names no node, and leaving it in
puts ``⟦img7⟧`` in the finished book.

Reconciliation is deliberately **lenient** (pinned user decision): a reply
that loses a marker is accepted, the dropped markers are appended in source
order, and the unit is never failed and never retried for marker placement
alone. Paired markers (``⟦em4⟧…⟦/em4⟧``) are out of scope for this pass.
"""

import re
import unicodedata

# Rendered characters a protected inline node may hold and still become a
# marker. Above it the node keeps today's barrier: a long excluded listing is
# not something a sentence flows through, and appending it on a dropped marker
# would move a paragraph of text to the end of the translation.
INLINE_MARKER_MAX_CHARS = 40

# The same cap, for content that renders no prose (see `is_wordless`). The
# rationale above is about *reading*: a paragraph of words appended at the end
# of a translation is a wrecked page, so a long word-bearing inline keeps its
# barrier. A formula or a URL is not read that way — it is one atom, whatever
# its length — and measuring it in rendered characters is measuring the wrong
# thing. The 260906 corpus sweep found this cap was the sole cause of all 36
# skipped-barrier mid-sentence cuts in the 45-book epub3-samples corpus: 33 in
# linear-algebra.epub, where MathML-adjacent formulas render as spaced-out
# single characters ("0 . 2 1 ( 9 6 0 ) + 2 4 6 3 = 2 6 6 4 . 6 0", 43 chars),
# and 3 in epub30-spec.epub, where <code> URLs of 41-59 characters shattered
# one sentence into four units, three of them 5-12 characters long.
#
# 400 is a guess, not a measurement: the longest wordless inline in the corpus
# is 195 characters, and this leaves generous room above it while still
# bounding the pathological case (an inline data: URI of a few kilobytes).
INLINE_MARKER_WORDLESS_MAX_CHARS = 400

# Characters that keep a token together: what a URL, a filesystem path, an
# email address or an identifier is glued out of. A run of letters touching
# one of these on either side is a *piece* of an atom, not a word — which is
# how "feature" inside
# `http://www.w3.org/TR/SVG11/feature#AnimationEventsAttribute` is told apart
# from "feature" in a sentence.
_TOKEN_GLUE = r"[0-9A-Za-z_./:#?=&%~@+\-]"

# A word: four or more letters with no glue on either side. `[^\W\d_]` is
# alphabetic in the Unicode sense, so a run of CJK counts — CJK prose has no
# spaces to delimit it, and one 40-character run of it is a sentence, not an
# atom (its punctuation, `，` and `。`, is not glue, so the run still ends).
#
# Four rather than three because mathematics borrows short function names:
# "det", "dim", "sin", "log" sit inside formulas that are otherwise nothing
# but digits and operators, and two such formulas in linear-algebra.epub
# would have kept their barriers under a three-character rule.
#
# Two things the rule deliberately cannot tell apart, neither seen in the
# 45-book corpus: prose built only from words of three letters or fewer, and
# a single 40-character word that a sentence period follows (the period is
# glue, because `www.w3.org` needs it to be).
_PROSE_WORD_RE = re.compile(rf"(?<!{_TOKEN_GLUE})[^\W\d_]{{4,}}(?!{_TOKEN_GLUE})")


def _erase_marks(text):
    """Erase combining marks and zero-width joiners before the word scan.

    Vowel signs, viramas and harakat are part of the word they decorate,
    but `[^\\W\\d_]` reads them as non-letters, so scanned raw a Devanagari
    word like "साहित्य" is runs of one or two letters and whole Hindi
    sentences looked wordless — 54 characters of prose taking the 400-char
    cap (codex review 260906). Erasing the marks re-joins each word's base
    letters; the glue characters are ASCII and unaffected.
    """
    return "".join(
        c
        for c in text
        if c not in "\u200c\u200d" and unicodedata.category(c) not in ("Mn", "Mc")
    )


def is_wordless(text):
    """Does this rendered text carry no prose word at all?

    True for a URL, a spaced-out formula, a stretch of digits and operators;
    False for anything with a word in it. Only the marker cap consults it —
    it decides which of the two caps above applies, never whether something
    is protected content in the first place.
    """
    return _PROSE_WORD_RE.search(_erase_marks(text or "")) is None


MARKER_OPEN = "⟦"
MARKER_CLOSE = "⟧"

# Anything marker-*shaped*, not only tokens we issued: the scrub has to catch
# what the model invented. Deliberately narrow — no whitespace, bounded — so a
# stray bracket in the prose cannot swallow half a paragraph.
MARKER_RE = re.compile(r"⟦[^⟦⟧\s]{1,32}⟧")

_NAME_RE = re.compile(r"[^a-z0-9]+")


def marker_name(tag_name):
    """The name half of a token: a tag name reduced to ``[a-z0-9]+``."""
    name = _NAME_RE.sub("", (tag_name or "").lower())
    return name or "x"


def marker_token(name, ordinal):
    return f"{MARKER_OPEN}{name}{ordinal}{MARKER_CLOSE}"


class Ordinals:
    """A monotonic ordinal supply, one per document.

    Ordinals only have to be unique inside a single *request*, and a request
    never spans two documents, so per-document numbering is enough — and it is
    stable, which a per-request counter could not be (grouping happens after
    the partition).
    """

    def __init__(self, start=1):
        self.next = start

    def allocate(self, tag_name, occupied="", taken=()):
        """A token for `tag_name` that appears nowhere in `occupied`.

        `occupied` is the text the token is about to be planted in; `taken`
        the tokens already planted there. Renumbering is the whole point:
        a book that prints ``⟦code1⟧`` verbatim must not be handed a
        placeholder spelled the same way.
        """
        name = marker_name(tag_name)
        while True:
            token = marker_token(name, self.next)
            self.next += 1
            if token not in occupied and token not in taken:
                return token


def find_markers(text):
    """Marker tokens in `text`, in order of first appearance, without repeats."""
    found = []
    for match in MARKER_RE.finditer(text or ""):
        token = match.group(0)
        if token not in found:
            found.append(token)
    return found


def _issued_and_literal(sent, issued):
    """Split the marker-shaped tokens in `sent` into ours and the book's.

    `issued` is what generation actually planted (``unit.markers``). Anything
    else that merely *looks* like a token is the source text's own: a book
    that prints ``⟦x1⟧`` verbatim, which collision avoidance already refused
    to reuse as a placeholder. It stands for no node, so it is literal text —
    it is not deduped, not appended, and above all not scrubbed, because
    scrubbing it would delete a character the author wrote.

    Without `issued` there is nothing better to go on than the shape, which
    is what every caller did before the distinction existed.
    """
    shaped = find_markers(sent)
    if issued is None:
        return shaped, []
    issued = list(issued)
    return issued, [token for token in shaped if token not in issued]


def reconcile_markers(sent, reply, issued=None):
    """The reply with its markers made to match what was sent. Never raises.

    Invented tokens are dropped, repeats of an issued token after the first
    are dropped, and issued markers the model lost are appended in source
    order. A reply that already agrees with the source comes back
    byte-identical, so the ordinary case costs nothing.
    """
    if reply is None:
        reply = ""
    issued, literal = _issued_and_literal(sent, issued)
    seen = []

    def keep(match):
        token = match.group(0)
        if token in issued:
            if token in seen:
                return ""
            seen.append(token)
            return token
        if token in literal:
            # the source's own text, echoed back: not ours to touch
            return token
        return ""  # neither planted nor in the source: invented

    out = MARKER_RE.sub(keep, reply)
    missing = [token for token in issued if token not in seen]
    if missing:
        out = (out.rstrip() + " " + " ".join(missing)).strip()
    return out


def marker_report(unit_name, sent, reply, issued=None):
    """One line naming what reconciliation had to do, or None when nothing.

    The operator sees the unit, not a count buried in a summary: a book whose
    every marker comes back missing is a prompt problem worth noticing early.
    """
    issued, literal = _issued_and_literal(sent, issued)
    got = find_markers(reply or "")
    missing = [token for token in issued if token not in got]
    invented = [token for token in got if token not in issued and token not in literal]
    if not missing and not invented:
        return None
    parts = []
    if missing:
        parts.append(f"{len(missing)} marker(s) missing ({', '.join(missing)})")
    if invented:
        parts.append(f"{len(invented)} invented ({', '.join(invented)})")
    return f"{unit_name}: " + "; ".join(parts) + " — reconciled"


def split_on_markers(text, tokens):
    """`text` as an ordered list of ``(kind, value)``: 'text' or 'marker'.

    The write-back's shape: each marker piece names the token whose source
    node replaces it, everything else is literal text.
    """
    if not tokens:
        return [("text", text)] if text else []
    pattern = re.compile("|".join(re.escape(t) for t in tokens))
    pieces = []
    position = 0
    for match in pattern.finditer(text):
        if match.start() > position:
            pieces.append(("text", text[position : match.start()]))
        pieces.append(("marker", match.group(0)))
        position = match.end()
    if position < len(text):
        pieces.append(("text", text[position:]))
    return pieces
