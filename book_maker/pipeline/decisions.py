"""Region roles decided by a vision model, over docling's own regions.

docling's layout model draws the right boxes far more often than it names
them right: an author's name comes back as a section heading, the first
lines of a code listing as footnotes, an affiliation note as body prose.
The 260923 evaluation (docs/260923-eval-PDF_STRUCTURE_FAULTS_LUNA_REGION_ROLES.md)
showed a vision model, shown the page with the regions drawn on it, fixing
most of those by naming the role again -- without touching a box or a
word. This module is that pass, and nothing more:

1. `eligible` lists the regions it may decide: plain text-like items in
   the body, not captions or notes a picture or table owns, not table
   cells, not lists, formulas, tables or pictures.
2. `overlay_png` draws every region of the page with a tag `<id> <label>`
   (non-candidates as `x<id>`), and `decide_roles` asks the injected
   `ask(prompt, schema, image_png) -> dict` for a label per candidate id,
   in batches, under a budget.
3. `parse_reply` validates each answer on its own: a duplicate key rejects
   the whole reply, an unknown id is a protocol violation, a value outside
   the candidate's choices is invalid, a missing id is unanswered, the
   same label or `abstain` is kept.
4. `apply` writes the accepted changes into the document as typed
   replacements at the same `texts` slot -- never a bare `.label` write,
   because docling-core's Markdown serializer dispatches on the item's
   class, and a label-only change exports differently before and after a
   JSON round trip (Codex consult 260923, measured on docling-core
   2.97.2). A page's changes go in together or not at all.

The overlay (`decisions.json`) is the audit record: every question, every
answer and what became of it, every call's model, usage and latency.

This module never imports a translator: the model call is the caller's.
docling-core, pypdfium2 and Pillow are imported inside the functions that
need them, so the module itself is as light as the pipeline around it.
"""

import copy
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .messages import STRUCTURE_DETAIL_BUDGET, STRUCTURE_PARTIAL

# Bump PROMPT_REV when the prompt text or the region list's shape changes,
# POLICY_REV when ELIGIBLE_SOURCE, TARGETS or ALLOWED change. Both enter the extraction identity: a rerun under another
# revision extracts again.
PROMPT_REV = "260923a"
POLICY_REV = "260923b"  # b: the quarantine became a warning (260923)

OVERLAY_FILE = "decisions.json"
# The pass's outcome, recorded as the manifest's `structure_status`: only a
# complete pass satisfies a later run that asks for one (ruling 260923).
STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# The lead's text, verbatim (packet E2, 260923). The candidate list is
# appended after the final line as JSON; the overlay image is the second
# content part of the same message.
PROMPT = """You are checking the region labels of a layout detector on one page of a document.

The image is the page with the detector's regions drawn on it. Each box has a small tag above its top-left corner: "<id> <label>". A tag that starts with "x" (for example "x7 picture") is shown for context only and is not yours to decide.

After this text comes a JSON list of the regions you decide: id, the detector's label, and the first 80 characters of the text the detector read inside the box.

For EVERY listed id, choose the label that describes the region's role on the page, from: text, section_header, title, caption, footnote, code.

Meaning: title = the document's own title, once per document; section_header = a heading that starts a section of the body (not a name, an address, a URL, a question or a bold sentence); caption = the label or title line of a figure, table or code listing; footnote = a note at the foot of a page or column, including author-affiliation and copyright notes; code = program source lines; text = body prose and anything else.

Rules:
- Keep the detector's label when it is right: return it unchanged.
- Return "abstain" when you cannot tell from the image.
- Only the label changes. Do not merge, split, move or rewrite regions or text.

Answer with one JSON object and nothing else, mapping every listed id (as a string) to a label or "abstain", for example {"0": "title", "1": "text", "2": "abstain"}.

Regions:
"""

ELIGIBLE_SOURCE = {
    "text",
    "paragraph",
    "footnote",
    "caption",
    "section_header",
    "title",
}
TARGETS = {"text", "footnote", "caption", "section_header", "title", "code"}
ABSTAIN = "abstain"
# Which change each detector label may undergo (lead decision, packet E2).
# Lists, formulas, tables, pictures, furniture, key-value regions and
# document indexes are neither asked about nor accepted.
_FROM_TEXT = {"footnote", "caption", "section_header", "title", "code"}
ALLOWED = {
    "text": _FROM_TEXT,
    "paragraph": _FROM_TEXT,
    "footnote": {"text", "caption", "code"},
    "caption": {"text", "footnote"},
    "section_header": {"text", "footnote", "caption", "title"},
    "title": {"text", "section_header"},
}

# A page on which more than this share of the asked items changed is
# called out (terminal and manifest) for the operator to read before
# translating; its changes stand. A guess, not a measurement. It was a
# quarantine that applied nothing until the 260923 real runs: on the one
# page where it fired (mixed_photo_code_1_p3031 p1) Luna was right on 9 of
# 10 and the quarantine withheld the listing's repair, and it helped on no
# page (lead ruling 260923, docs/260923-feat-PDF_ROLE_DECISIONS.md).
CHANGE_RATE_WARN = 0.6

# Per-page budget for the pass, multiplied by the pages asked. Guesses, not
# measurements: the 260923 evaluation spent at most 7,860 prompt tokens and
# 10.6 s on one page (a 164-region OCR page), one call each.
CALLS_PER_PAGE = 4
SECONDS_PER_PAGE = 300
PROMPT_TOKENS_PER_PAGE = 40_000

TEXT_HEAD = 80
FORMULA_MARKER_PREFIX = "bbm-formula-"

# Overlay drawing, as in the evaluation that measured the pass (scale 2,
# a 15 px tag on a white strip above each box).
SCALE = 2
TAG_SIZE = 15
COLOURS = {
    "text": (0, 90, 255),
    "paragraph": (0, 90, 255),
    "section_header": (220, 0, 0),
    "title": (160, 0, 160),
    "caption": (0, 150, 0),
    "footnote": (200, 120, 0),
    "picture": (255, 0, 200),
    "chart": (255, 0, 200),
    "table": (0, 170, 170),
    "formula": (120, 60, 0),
    "list_item": (60, 60, 200),
    "page_header": (128, 128, 128),
    "page_footer": (128, 128, 128),
    "code": (90, 0, 90),
    "document_index": (255, 140, 0),
    "key_value_region": (0, 100, 0),
    "form": (0, 100, 0),
}


class ReplyRejected(Exception):
    """The reply as a whole cannot be trusted (a duplicate key, not an object)."""


class AskFailed(Exception):
    """One question got no usable answer; the pass records it and goes on.

    The caller's `ask` raises this for a failure that is about the request
    (the endpoint refused the image, no rung parsed). Anything else it
    raises -- an authentication error above all -- ends the pass.
    """


@dataclass
class Candidate:
    id: int
    ref: str
    label: str
    text_head: str
    bbox: tuple
    page: int


@dataclass
class Region:
    """One box drawn on the overlay: `tag` is `3` or `x3`."""

    tag: str
    label: str
    bbox: tuple


@dataclass
class Budget:
    """Upper bounds on the whole pass; None is no bound.

    Checked before each call: a call already started is never cut off,
    the next one is not made.
    """

    max_calls: int = None
    max_seconds: float = None
    max_prompt_tokens: int = None

    @classmethod
    def for_pages(cls, pages):
        pages = max(1, int(pages))
        return cls(
            max_calls=CALLS_PER_PAGE * pages,
            max_seconds=SECONDS_PER_PAGE * pages,
            max_prompt_tokens=PROMPT_TOKENS_PER_PAGE * pages,
        )


@dataclass
class Overlay:
    """Every question the pass asked, the answers, and what became of them.

    `pages[page_no]` holds `decisions` (`{id, ref, from, to, status}`),
    `calls` (one entry per `ask`) and a page `status`: `asked`, `unasked`
    (the budget ran out first), `no_candidates` or `failed_apply`. A
    decision's status is one of kept, accepted, invalid,
    protocol_violation, unanswered, disallowed. `recount` adds each page's
    `asked`, `changed`, change `histogram` (`{"from->to": n}`) and
    `high_change` (more than `CHANGE_RATE_WARN` of its asked items
    changed).
    """

    prompt_rev: str = PROMPT_REV
    policy_rev: str = POLICY_REV
    model: str = None
    pages: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)

    def to_json(self):
        data = asdict(self)
        data["pages"] = {str(page): entry for page, entry in self.pages.items()}
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text):
        data = json.loads(text)
        data["pages"] = {int(page): entry for page, entry in data["pages"].items()}
        return cls(**data)

    def write(self, path):
        Path(path).write_text(self.to_json(), encoding="utf-8")

    def status(self):
        """`complete`, or `partial` when anything asked for went without.

        Complete: every batch asked got a usable answer for every id and
        every page's answers were applied. Partial: a question failed or
        its reply was rejected whole, a reply left an id out (`unanswered`),
        a page could not be changed safely, the budget stopped the pass,
        or a page was not asked. (`failed`, the pass
        raising, is the caller's to record: there is no overlay then.)
        """
        if self.totals.get("budget_exhausted"):
            return STATUS_PARTIAL
        for entry in self.pages.values():
            if entry.get("status") in ("unasked", "failed_apply"):
                return STATUS_PARTIAL
            if any(call.get("error") for call in entry.get("calls", [])):
                return STATUS_PARTIAL
            if any(
                decision.get("status") == "unanswered"
                for decision in entry.get("decisions", [])
            ):
                return STATUS_PARTIAL
        return STATUS_COMPLETE

    def recount(self):
        """`totals` from the pages as they stand now."""
        counts = {
            status: 0
            for status in (
                "kept",
                "accepted",
                "invalid",
                "protocol_violation",
                "unanswered",
                "disallowed",
            )
        }
        asked = calls = prompt = completion = 0
        seconds = 0.0
        high = unasked = failed = 0
        for entry in self.pages.values():
            if entry.get("status") == "unasked":
                unasked += 1
            if entry.get("status") == "failed_apply":
                failed += 1
            _page_counts(entry)
            if entry["high_change"]:
                high += 1
            for decision in entry.get("decisions", []):
                counts[decision["status"]] = counts.get(decision["status"], 0) + 1
                if decision["status"] != "protocol_violation":
                    asked += 1
            for call in entry.get("calls", []):
                calls += 1
                usage = call.get("usage") or {}
                prompt += int(usage.get("prompt_tokens") or 0)
                completion += int(usage.get("completion_tokens") or 0)
                seconds += float(call.get("latency_s") or 0.0)
        budget = self.totals.get("budget_exhausted")
        self.totals = {
            "asked": asked,
            **counts,
            "rejected": counts["invalid"]
            + counts["protocol_violation"]
            + counts["disallowed"],
            "pages": len(self.pages),
            "high_change_pages": high,
            "unasked_pages": unasked,
            "failed_apply_pages": failed,
            "calls": calls,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "seconds": round(seconds, 2),
            "budget_exhausted": budget,
        }
        return self.totals


def _page_counts(entry):
    """A page's asked items, the changes that stand, and their histogram."""
    decisions = entry.get("decisions", [])
    asked = sum(1 for d in decisions if d["status"] != "protocol_violation")
    histogram = {}
    if entry.get("status") != "failed_apply":
        for d in decisions:
            if d["status"] == "accepted":
                key = f"{d['from']}->{d['to']}"
                histogram[key] = histogram.get(key, 0) + 1
    changed = sum(histogram.values())
    entry["asked"] = asked
    entry["changed"] = changed
    entry["histogram"] = histogram
    entry["high_change"] = bool(asked) and changed > CHANGE_RATE_WARN * asked


@dataclass
class Applied:
    """What `apply` changed: how many items, which, and which pages failed."""

    count: int = 0
    refs: list = field(default_factory=list)
    transitions: dict = field(default_factory=dict)
    failed_pages: list = field(default_factory=list)
    disallowed: int = 0


# --------------------------------------------------------------------------
# Which regions are asked about
# --------------------------------------------------------------------------
def _label(item):
    label = getattr(item, "label", "")
    return str(getattr(label, "value", label))


def _box(document, prov):
    """`(left, bottom, right, top)` in points, bottom-left origin.

    docling's PDF pipeline reports bottom-left boxes; a top-left one is
    turned over with the page's own height rather than trusted as is.
    """
    box = prov.bbox
    if "TOPLEFT" in str(getattr(box, "coord_origin", "")).upper():
        height = document.pages[prov.page_no].size.height
        box = box.to_bottom_left_origin(page_height=height)
    return (float(box.l), float(box.b), float(box.r), float(box.t))


def _owned_refs(document):
    """Every item a picture, table or other floating item names as its own."""
    owned = set()
    collections = (
        "pictures",
        "tables",
        "texts",
        "key_value_items",
        "form_items",
    )
    for name in collections:
        for item in getattr(document, name, None) or []:
            for attribute in ("captions", "footnotes", "references"):
                for ref in getattr(item, attribute, None) or []:
                    owned.add(ref.cref)
    for table in getattr(document, "tables", None) or []:
        for cell in getattr(getattr(table, "data", None), "table_cells", None) or []:
            ref = getattr(cell, "ref", None)
            if ref is not None:
                owned.add(ref.cref)
    return owned


def _inside_table(document, item):
    from docling_core.types.doc.document import TableItem

    parent = item.parent
    while parent is not None:
        node = parent.resolve(document)
        if isinstance(node, TableItem):
            return True
        parent = getattr(node, "parent", None)
    return False


def _page_items(document, page_no):
    """`[(id, item)]` for every region with a box on the page, reading order.

    All content layers, so the overlay shows the running heads and feet
    too; pictures' own children are not walked (docling does not export
    them), their captions are.
    """
    from docling_core.types.doc import ContentLayer

    found = []
    for item, _level in document.iterate_items(
        with_groups=False,
        page_no=page_no,
        included_content_layers=set(ContentLayer),
    ):
        if any(prov.page_no == page_no for prov in getattr(item, "prov", None) or []):
            found.append(item)
    return list(enumerate(found))


def _is_candidate(document, item, page_no, owned):
    from docling_core.types.doc import ContentLayer
    from docling_core.types.doc.document import (
        SectionHeaderItem,
        TextItem,
        TitleItem,
    )

    if type(item) not in (TextItem, TitleItem, SectionHeaderItem):
        return False
    if item.content_layer != ContentLayer.BODY:
        return False
    if _label(item) not in ELIGIBLE_SOURCE:
        return False
    pages = [prov.page_no for prov in item.prov]
    # An item spanning two pages is asked once, on the first.
    if not pages or min(pages) != page_no:
        return False
    if item.self_ref in owned:
        return False
    if (item.text or "").startswith(FORMULA_MARKER_PREFIX):
        return False
    return not _inside_table(document, item)


def _page_regions(document, page_no):
    """`(candidates, regions)` for one page, from one walk."""
    owned = _owned_refs(document)
    candidates, regions = [], []
    for index, item in _page_items(document, page_no):
        label = _label(item)
        chosen = _is_candidate(document, item, page_no, owned)
        boxes = [_box(document, prov) for prov in item.prov if prov.page_no == page_no]
        for box in boxes:
            regions.append(Region(str(index) if chosen else f"x{index}", label, box))
        if chosen:
            candidates.append(
                Candidate(
                    id=index,
                    ref=item.self_ref,
                    label=label,
                    text_head=(item.text or "")[:TEXT_HEAD],
                    bbox=boxes[0],
                    page=page_no,
                )
            )
    return candidates, regions


def eligible(document, page_no):
    """The regions of `page_no` the pass may decide, as `Candidate`s.

    Plain `TextItem`, `TitleItem` or `SectionHeaderItem` (not a list item,
    formula or code item) in the body layer, labelled one of
    `ELIGIBLE_SOURCE`, with a box on this page and on no earlier one, not
    named as a caption, footnote or reference by any picture or table, not
    inside a table, not a formula marker. `id` is the region's number on
    the overlay.
    """
    return _page_regions(document, page_no)[0]


def regions(document, page_no):
    """Every region of the page as the overlay draws it."""
    return _page_regions(document, page_no)[1]


# --------------------------------------------------------------------------
# The picture
# --------------------------------------------------------------------------
def _font():
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=TAG_SIZE)
    except TypeError:  # Pillow before 10.1 has one bitmap size
        return ImageFont.load_default()


def overlay_png(pdf, page_no, regions, scale=SCALE):
    """The page rendered at `scale`, every region boxed and tagged, as PNG.

    Coordinates are docling's: PDF points, bottom-left origin, relative to
    the page as displayed (CropBox origin, `/Rotate` applied) -- the frame
    `page.get_size()` describes and pdfium renders in, as `pdf_formula`
    crops in it.
    """
    import io

    from PIL import ImageDraw

    from .pdf_common import _pdfium

    pdfium, _raw = _pdfium()
    document = pdfium.PdfDocument(str(pdf))
    try:
        page = document[page_no - 1]
        _width, height = (float(value) for value in page.get_size())
        image = page.render(scale=scale).to_pil().convert("RGB")
    finally:
        document.close()
    draw = ImageDraw.Draw(image)
    font = _font()
    for region in regions:
        left, bottom, right, top = region.bbox
        x0, y0 = left * scale, (height - top) * scale
        x1, y1 = right * scale, (height - bottom) * scale
        colour = COLOURS.get(region.label, (0, 0, 0))
        context = region.tag.startswith("x")
        draw.rectangle([x0, y0, x1, y1], outline=colour, width=1 if context else 2)
        tag = f"{region.tag} {region.label}"
        strip = TAG_SIZE + 2
        width = draw.textlength(tag, font=font)
        draw.rectangle(
            [x0, max(0, y0 - strip), x0 + width + 4, max(strip, y0)],
            fill=(255, 255, 255),
        )
        draw.text((x0 + 2, max(0, y0 - strip)), tag, fill=colour, font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# The question and the answer
# --------------------------------------------------------------------------
def choices(candidate):
    """What a candidate may be answered with: its targets, its own label, abstain."""
    allowed = sorted(ALLOWED.get(candidate.label, ()))
    ordered = allowed + [candidate.label, ABSTAIN]
    return list(dict.fromkeys(ordered))


SCHEMA_NAME = "region_roles"


def schema(candidates):
    """One required string property per candidate id, enumerating its choices.

    In the wrapper the structured channel takes (`name`, `strict`,
    `schema`), as the plan classifier's schema is.
    """
    return {
        "name": SCHEMA_NAME,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                str(c.id): {"type": "string", "enum": choices(c)} for c in candidates
            },
            "required": [str(c.id) for c in candidates],
            "additionalProperties": False,
        },
    }


def prompt(candidates):
    listed = [
        {"id": c.id, "docling_label": c.label, "text_head": c.text_head}
        for c in candidates
    ]
    return PROMPT + json.dumps(listed, ensure_ascii=False)


def _no_duplicates(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ReplyRejected(f"duplicate key {key!r} in the reply")
        seen[key] = value
    return seen


def _decode(text):
    decoder = json.JSONDecoder(object_pairs_hook=_no_duplicates)
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        raise ReplyRejected("the reply holds no JSON object")
    try:
        value, _end = decoder.raw_decode(text, start)
    except json.JSONDecodeError as err:
        raise ReplyRejected(f"the reply is not JSON: {err}")
    return value


def parse_reply(reply, candidates):
    """`[{id, ref, from, to, status}]` for one reply to one batch.

    `reply` is the reply's text, or the dict the caller's schema rung
    already parsed. A dict cannot show a key twice any more, so when it
    carries the text it came from (a `raw` attribute), that text is
    checked for duplicates first. A duplicate key rejects the whole reply
    (`ReplyRejected`): which of the two values was meant is unknowable.

    Per id: an id not asked about is a `protocol_violation` and is never
    applied; a value that is not a string or not a label at all is
    `invalid`; a label the transition policy forbids for this candidate is
    `disallowed`; no answer is `unanswered`; its own label or `abstain`
    is `kept`; anything else is `accepted` (a proposed change -- `apply`
    decides whether it goes in).
    """
    raw = reply if isinstance(reply, str) else getattr(reply, "raw", None)
    if raw is not None:
        answers = _decode(raw)
    else:
        answers = reply
    if not isinstance(answers, dict):
        raise ReplyRejected("the reply is not a JSON object")
    by_id = {str(c.id): c for c in candidates}
    decisions = []
    for candidate in candidates:
        key = str(candidate.id)
        entry = {
            "id": candidate.id,
            "ref": candidate.ref,
            "from": candidate.label,
            "to": None,
            "status": "unanswered",
        }
        if key in answers:
            value = answers[key]
            entry["to"] = value
            if not isinstance(value, str):
                entry["status"] = "invalid"
            elif value in (candidate.label, ABSTAIN):
                entry["status"] = "kept"
            elif value in ALLOWED.get(candidate.label, ()):
                entry["status"] = "accepted"
            elif value in TARGETS:
                entry["status"] = "disallowed"
            else:
                entry["status"] = "invalid"
        decisions.append(entry)
    for key, value in answers.items():
        if key not in by_id:
            decisions.append(
                {
                    "id": key,
                    "ref": None,
                    "from": None,
                    "to": value,
                    "status": "protocol_violation",
                }
            )
    return decisions


# --------------------------------------------------------------------------
# The pass
# --------------------------------------------------------------------------
def _usage(reply):
    usage = getattr(reply, "usage", None)
    if not usage:
        return None
    if not isinstance(usage, dict):
        usage = {
            name: getattr(usage, name, None)
            for name in ("prompt_tokens", "completion_tokens")
        }
    return {name: usage.get(name) for name in ("prompt_tokens", "completion_tokens")}


def _spent(budget, calls, started, prompt_tokens):
    """The name of the bound that is reached, or None."""
    if budget.max_calls is not None and calls >= budget.max_calls:
        return "max_calls"
    if (
        budget.max_seconds is not None
        and time.monotonic() - started >= budget.max_seconds
    ):
        return "max_seconds"
    if (
        budget.max_prompt_tokens is not None
        and prompt_tokens >= budget.max_prompt_tokens
    ):
        return "max_prompt_tokens"
    return None


def _batches(candidates, cap):
    cap = max(1, int(cap))
    return [candidates[i : i + cap] for i in range(0, len(candidates), cap)]


def decide_roles(
    ask, document, pdf, pages, *, batch_cap=40, budget=None, model=None, log=print
):
    """Ask `ask` about every eligible region of `pages`; return the `Overlay`.

    One overlay image per page; a page with more than `batch_cap`
    candidates is asked in consecutive batches over the same image, each
    batch's schema covering only its own ids. `budget` (a `Budget`,
    default `Budget.for_pages(len(pages))`) is checked before every call;
    when it is spent, the pass stops with one warning line and the pages
    not yet asked are recorded `unasked`, their detector labels standing.

    A page on which more than `CHANGE_RATE_WARN` of the asked items would
    change is marked `high_change` for the operator; its changes stand.

    `ask(prompt, schema, image_png, deadline=None)` returns the parsed
    dict; it may carry `raw` (the reply text), `usage` and `model` as
    attributes. It raises `AskFailed` or `ReplyRejected` for a question
    that got no usable answer (recorded, the pass goes on); anything else
    it raises ends the pass. `deadline` is the `time.monotonic()` at which
    the pass's `max_seconds` runs out (None without one): a question still
    waiting on the endpoint then is to end with `AskFailed`, so a
    persistent outage costs the budget, not the run.
    """
    pages = [int(page) for page in pages]
    budget = budget or Budget.for_pages(len(pages))
    overlay = Overlay(model=model)
    started = time.monotonic()
    deadline = started + budget.max_seconds if budget.max_seconds is not None else None
    calls = prompt_tokens = 0
    exhausted = None
    for position, page_no in enumerate(pages):
        candidates, drawn = _page_regions(document, page_no)
        entry = {"status": "asked", "decisions": [], "calls": []}
        overlay.pages[page_no] = entry
        if not candidates:
            entry["status"] = "no_candidates"
            continue
        if exhausted is None:
            exhausted = _spent(budget, calls, started, prompt_tokens)
        if exhausted is not None:
            entry["status"] = "unasked"
            continue
        image = overlay_png(pdf, page_no, drawn)
        for batch in _batches(candidates, batch_cap):
            reason = _spent(budget, calls, started, prompt_tokens)
            if reason is not None:
                exhausted = reason
                entry["decisions"].extend(_unanswered(batch, f"budget: {reason}"))
                continue
            call = {"ids": [c.id for c in batch], "model": model}
            begun = time.monotonic()
            calls += 1
            try:
                reply = ask(prompt(batch), schema(batch), image, deadline=deadline)
            except (AskFailed, ReplyRejected) as err:
                call["latency_s"] = round(time.monotonic() - begun, 2)
                call["error"] = f"{type(err).__name__}: {err}"
                entry["calls"].append(call)
                entry["decisions"].extend(_unanswered(batch, call["error"]))
                # A question that waited out the deadline spent the budget.
                if deadline is not None and time.monotonic() >= deadline:
                    exhausted = exhausted or "max_seconds"
                continue
            call["latency_s"] = round(time.monotonic() - begun, 2)
            call["model"] = getattr(reply, "model", None) or model
            call["usage"] = _usage(reply)
            if call["usage"]:
                prompt_tokens += int(call["usage"].get("prompt_tokens") or 0)
            entry["calls"].append(call)
            try:
                entry["decisions"].extend(parse_reply(reply, batch))
            except ReplyRejected as err:
                call["error"] = f"ReplyRejected: {err}"
                entry["decisions"].extend(
                    _unanswered(batch, call["error"], status="invalid")
                )
    if exhausted is not None:
        overlay.totals["budget_exhausted"] = exhausted
        left = [
            page
            for page, entry in overlay.pages.items()
            if entry["status"] == "unasked"
        ]
        log(
            STRUCTURE_PARTIAL.format(
                detail=STRUCTURE_DETAIL_BUDGET.format(
                    bound=exhausted,
                    pages=", ".join(str(page) for page in sorted(left)) or "none",
                )
            )
        )
    overlay.recount()
    return overlay


def _unanswered(batch, reason, status="unanswered"):
    return [
        {
            "id": c.id,
            "ref": c.ref,
            "from": c.label,
            "to": None,
            "status": status,
            "reason": reason,
        }
        for c in batch
    ]


# --------------------------------------------------------------------------
# Applying it
# --------------------------------------------------------------------------
def _slot(ref):
    prefix = "#/texts/"
    if not ref or not ref.startswith(prefix):
        return None
    try:
        return int(ref[len(prefix) :])
    except ValueError:
        return None


def _replacement(item, target):
    """A new item of the class `target` needs, carrying everything `item` has.

    Same `self_ref`, parent, children, provenance, text, `orig`,
    formatting, hyperlink, content layer and metadata. A section header
    starts at level 1; `pdf_headings.assign` sets the real level after.
    A code item keeps the text exactly as it is: newlines the detector
    already lost are not restored here.
    """
    from docling_core.types.doc import DocItemLabel
    from docling_core.types.doc.document import (
        CodeItem,
        SectionHeaderItem,
        TextItem,
        TitleItem,
    )

    cls = {
        "section_header": SectionHeaderItem,
        "title": TitleItem,
        "code": CodeItem,
    }.get(target, TextItem)
    data = copy.deepcopy(item.model_dump())
    fields = {
        name: value
        for name, value in data.items()
        if name in cls.model_fields and name not in ("label", "level")
    }
    lost = [
        name
        for name, value in data.items()
        if name not in cls.model_fields and name != "level" and value
    ]
    if lost:
        raise ValueError(f"{item.self_ref} would lose {', '.join(lost)}")
    fields["label"] = DocItemLabel(target)
    if cls is SectionHeaderItem:
        fields["level"] = 1
    return cls.model_validate(fields)


def _intact(document, new, old):
    """The replacement sits where the old item sat, with the same graph."""
    if new.self_ref != old.self_ref:
        return False
    if (new.parent and new.parent.cref) != (old.parent and old.parent.cref):
        return False
    if [c.cref for c in new.children] != [c.cref for c in old.children]:
        return False
    if new.text != old.text or new.orig != old.orig or new.prov != old.prov:
        return False
    if new.content_layer != old.content_layer:
        return False
    return new.get_ref().resolve(document) is new


def apply(document, overlay):
    """Write the overlay's accepted changes into `document`. Returns `Applied`.

    Each accepted decision is checked against `ALLOWED` again and against
    the item it names (the ref must hold an item still labelled as the
    decision says); one that fails is marked `disallowed` and skipped. A
    page's changes are then made together, as typed replacements at the
    same `texts` slot: if any replacement raises or the document's tree no
    longer validates, the original items go back into their slots and the
    page is marked `failed_apply`.
    """
    result = Applied()
    for page_no, entry in overlay.pages.items():
        if entry.get("status") != "asked":
            continue
        patches = []
        for decision in entry.get("decisions", []):
            if decision.get("status") != "accepted":
                continue
            slot = _slot(decision.get("ref"))
            item = (
                document.texts[slot]
                if slot is not None and slot < len(document.texts)
                else None
            )
            source, target = decision.get("from"), decision.get("to")
            if (
                item is None
                or _label(item) != source
                or target not in ALLOWED.get(source, ())
            ):
                decision["status"] = "disallowed"
                result.disallowed += 1
                continue
            patches.append((slot, item, target, decision))
        if not patches:
            continue
        originals = {slot: item for slot, item, _target, _decision in patches}
        try:
            for slot, item, target, _decision in patches:
                new = _replacement(item, target)
                document.texts[slot] = new
                if not _intact(document, new, item):
                    raise ValueError(f"{item.self_ref} did not keep its place")
            document.validate_tree(document.body, raise_on_error=True)
        except Exception as err:
            for slot, item in originals.items():
                document.texts[slot] = item
            entry["status"] = "failed_apply"
            entry["error"] = f"{type(err).__name__}: {err}"
            result.failed_pages.append(page_no)
            continue
        for slot, item, target, _decision in patches:
            result.count += 1
            result.refs.append(item.self_ref)
            key = f"{_label(item)}->{target}"
            result.transitions[key] = result.transitions.get(key, 0) + 1
    overlay.recount()
    return result
