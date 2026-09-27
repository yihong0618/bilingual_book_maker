"""Figures, drawn from the PDF by our own renderer at a chosen resolution.

docling finds the figures and says where they stand; its own pictures of
them are 1 px per PDF point (72 DPI, `images_scale` 1.0), which is what a
reader zooming into a plot sees blur. So docling keeps the layout, the
text and the placement, and the pixels are drawn here with pypdfium2, one
page at a time, at a resolution a `FigurePolicy` chooses (owner 260925:
the policy is general -- "A4 was an example, not that every page is
treated as A4").

The seam is a stable name per picture. At extraction every docling
PictureItem that reaches the Markdown is given `figures/p{page}-{n}.png`
as its reference, by identity (the item's own `image.uri`, set before the
serializer runs -- the way `pdf_formula.mark` gives a formula its marker),
so `source.md` names a figure and never a resolution: it is byte-identical
whatever the policy, and so is the translation built from it. The record
of where each figure stands is `.work/extraction/figures.json`; the render
step reads it and writes the PNGs under `assets/figures/`. Changing the
policy redraws those files and nothing else -- no extraction, no
translation -- and the EPUB, which is always rebuilt, picks them up.

docling's own pictures stay under `.work/extraction/images/` as the
fallback for a figure that cannot be drawn.

The policy is not an extraction setting (`ExtractionSettings`): it does
not change a word of the text, and a change must never re-extract.
"""

import argparse
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .bundle import ASSETS_DIR, sha256_bytes, sha256_file
from .errors import PipelineError
from .messages import (
    FIGURE_CAPPED,
    FIGURE_FALLBACK_MISSING,
    FIGURE_RECORD_BROKEN,
    FIGURE_RECORD_MISSING,
    FIGURE_RECORD_UNLISTED,
    FIGURE_RECORD_UNREADABLE,
    FIGURE_RENDER_FAILED,
    FIGURES_DRAWN,
    FIGURES_LEGACY,
    FIGURES_NATIVE,
    FIGURES_REDRAWN,
    FIGURES_UNREFERENCED,
    PDF_IMAGE_DPI_INVALID,
)

# Beside `source.md` in the extraction's staging directory, and under
# `assets/` once imported.
FIGURE_DIR = "figures"
# Inside the extraction's staging directory, `.work/extraction/`.
FIGURES_FILE = "figures.json"
# docling's own pictures, in the same staging directory (the adapter's
# `IMAGE_DIR`, spelled here so this module never imports the adapter).
DOCLING_IMAGE_DIR = "images"
# Bumped when the drawing itself changes (crop, encoding), so every bundle
# drawn by the older code is drawn again on its next run. 2: padding, the
# megapixel ceiling, the native resolution of a lone embedded picture.
FIGURE_REVISION = 2
# No figure bitmap above this many pixels (Codex astra 260925,
# docs/260925-docs-PDF_IMAGE_FIDELITY_DESIGN.md): the ceiling most
# e-readers accept for one image. Applied to every policy after padding,
# before anything is rendered; the figure's shown width is unchanged.
FIGURE_MAX_PIXELS = 5_600_000
# Room around a detected figure box, per side, in points (provisional):
# docling's box can shave an axis label or a stroke. Each side stops
# `pdf_formula.PAD_CLEAR` short of the nearest unrelated item and at the
# page edge; no minimum.
FIGURE_PAD_PT = 2.0
# How far a lone embedded picture's placement may fall short of docling's
# detected box and still be the figure. Nothing else may intersect the
# padded crop (`native_dpi`), so what the picture leaves out is blank
# page. Measured: docling's box stood 1.56 pt left of a lone 173 DPI
# plot (mit_lecnotes12 page 5); a tolerance on the crop of the padding
# plus half a point refused it.
NATIVE_COVER_TOLERANCE = FIGURE_PAD_PT

POLICY_KINDS = ("dpi", "page-width", "figure-px")
# `figure-px`: the height is held to this many times the asked width, so a
# tall narrow figure does not become a column of pixels, and the scale is
# never above what `dpi` 300 would draw -- a thumbnail is not blown up to
# thousands of pixels from a few points of vector art.
FIGURE_PX_HEIGHT_CAP = 1.5
FIGURE_PX_MAX_DPI = 300

# A name `name_pictures` gives: `p0003-01.png`. Wider numbers are allowed,
# a page past 9999 or a hundredth picture on one page is still ours.
STABLE_NAME = re.compile(r"^p\d{4,}-\d{2,}\.png$")


@dataclass(frozen=True)
class FigurePolicy:
    """How many pixels a figure is drawn with.

    - `dpi`: the PDF's own physical resolution; scale = value / 72.
    - `page-width`: a pixel budget for the page's displayed width; scale =
      value / page width in points (1654 is "200 DPI of an A4-wide page",
      and A4 is only one way to pick the number).
    - `figure-px`: the figure's own width in pixels; scale = value / box
      width, the height held to 1.5 x value, never above `dpi` 300.
    """

    kind: str
    value: float

    def __post_init__(self):
        if self.kind not in POLICY_KINDS:
            raise ValueError(
                f"figure policy kind {self.kind!r} is not one of "
                f"{', '.join(POLICY_KINDS)}"
            )
        try:
            value = float(self.value)
        except (TypeError, ValueError):
            raise ValueError(f"figure policy value {self.value!r} is not a number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"figure policy value {self.value!r} is not positive")
        # 200 and 200.0 are one policy, and the manifest says 200.
        object.__setattr__(self, "value", int(value) if value.is_integer() else value)

    def describe(self):
        """`200 DPI`, `1654 px per page width`, `1600 px per figure`."""
        value = f"{self.value:g}" if isinstance(self.value, float) else self.value
        return {
            "dpi": f"{value} DPI",
            "page-width": f"{value} px per page width",
            "figure-px": f"{value} px per figure",
        }[self.kind]

    def to_manifest(self):
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_manifest(cls, data):
        return cls(data["kind"], data["value"])


# Owner 260925: the PDF's real physical DPI, 200 (packet R measured the
# candidates, docs/260925-eval-PDF_FIGURE_RESOLUTION_POLICIES.md). The
# main CLI's `--pdf-image-dpi` defaults to its value.
FIGURE_POLICY_DEFAULT = FigurePolicy("dpi", 200)


def parse_figure_policy(text):
    """`KIND:VALUE` as a `FigurePolicy`; an argparse `type=`.

    Refused at parse time (argparse exits 2): an unknown kind, a value
    that is not a positive number, a missing colon.
    """
    kind, sep, value = str(text).partition(":")
    try:
        if not sep:
            raise ValueError("no colon")
        return FigurePolicy(kind.strip(), float(value))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not KIND:VALUE with KIND one of "
            f"{', '.join(POLICY_KINDS)} and VALUE a positive number "
            f"(e.g. dpi:200, page-width:1654, figure-px:1600)"
        )


# `--pdf-image-dpi`: the operator's knob, the `dpi` kind only (owner
# 260925: the PDF's real physical DPI). 72 is docling's own picture; past
# 600 a page-wide figure is a poster.
PDF_IMAGE_DPI_MIN = 72
PDF_IMAGE_DPI_MAX = 600


def parse_pdf_image_dpi(text):
    """`--pdf-image-dpi N` as an int, 72..600; an argparse `type=`."""
    try:
        value = int(str(text).strip())
    except ValueError:
        raise argparse.ArgumentTypeError(PDF_IMAGE_DPI_INVALID)
    if not PDF_IMAGE_DPI_MIN <= value <= PDF_IMAGE_DPI_MAX:
        raise argparse.ArgumentTypeError(PDF_IMAGE_DPI_INVALID)
    return value


def parse_pdf_image_dpi_policy(text):
    """`--pdf-image-dpi N` as the `dpi` policy (the harness shorthand)."""
    return FigurePolicy("dpi", parse_pdf_image_dpi(text))


def figure_scale(box, page_size, policy):
    """Pixels per PDF point for `box` on a page of `page_size`, by `policy`.

    `box` is `(left, top, right, bottom)` and `page_size` `(width,
    height)`, both in points in the page's displayed frame (after
    /Rotate), which is the frame docling reports and pypdfium2 renders.
    A box with no area cannot be drawn and is refused.
    """
    left, top, right, bottom = (float(v) for v in box)
    width, height = right - left, bottom - top
    if not (width > 0 and height > 0) or not all(
        math.isfinite(v) for v in (width, height)
    ):
        raise ValueError(f"the figure box {list(box)} has no area")
    if policy.kind == "dpi":
        return policy.value / 72.0
    if policy.kind == "page-width":
        page_width = float(page_size[0])
        if not page_width > 0:
            raise ValueError(f"the page width {page_width} is not positive")
        return policy.value / page_width
    scale = policy.value / width
    scale = min(scale, FIGURE_PX_HEIGHT_CAP * policy.value / height)
    return min(scale, FIGURE_PX_MAX_DPI / 72.0)


def figure_id(page, number):
    return f"p{page:04d}-{number:02d}"


def is_drawn_figure(path):
    """Whether a bundle-relative asset path is one of the drawn figures."""
    parts = Path(path).parts
    return (
        len(parts) == 3
        and parts[0] == ASSETS_DIR
        and parts[1] == FIGURE_DIR
        and bool(STABLE_NAME.match(parts[2]))
    )


def display_width(box, page_width):
    """The figure's share of the page's width, in whole percent, 1..100."""
    share = 100.0 * (float(box[2]) - float(box[0])) / float(page_width)
    return max(1, min(100, int(round(share))))


# --------------------------------------------------------------------------
# Extraction: names, records, widths
# --------------------------------------------------------------------------
def name_pictures(document, out_dir):
    """Give every written picture of `document` its stable name; return records.

    `document` is the copy docling-core's `_with_pictures_refs` returned:
    each picture it wrote carries its file's path as `image.uri`. That
    file is copied to `out_dir/figures/<name>` (a stand-in until the render
    step draws it, and the same pixels if the drawing fails) and the item's
    `uri` becomes `figures/<name>`, so the serializer writes the name where
    the item stands, whatever order it walks the document in.

    The pictures are numbered per page in the order docling-core writes
    them (`iterate_items`, reading order). A picture without a page or a
    written file keeps what docling gave it; there is nothing to draw.
    Each record: `id`, `page`, `bbox` (`[left, top, right, bottom]`, points,
    displayed frame, top-left origin: docling's box as detected), `crop`
    (the same frame: the box drawn, `figure_crop`), `file`
    (bundle-relative), `width`
    (percent of the page width), `fallback` (docling's file, relative to
    `out_dir`; the extraction writes it bundle-relative into figures.json).
    """
    from docling_core.types.doc import PictureItem
    from pydantic import AnyUrl

    target = Path(out_dir) / FIGURE_DIR
    counts, records = {}, []
    others = page_boxes(document)
    for item, _level in document.iterate_items(with_groups=False):
        if not isinstance(item, PictureItem) or not item.prov:
            continue
        image = item.image
        if image is None or image.uri is None or isinstance(image.uri, AnyUrl):
            continue
        source = Path(str(image.uri))
        if not source.is_file():
            continue
        prov = item.prov[0]
        page = document.pages.get(prov.page_no)
        if page is None:
            continue
        size = page.size
        box = prov.bbox.to_top_left_origin(page_height=size.height)
        counts[prov.page_no] = counts.get(prov.page_no, 0) + 1
        ident = figure_id(prov.page_no, counts[prov.page_no])
        name = f"{ident}.png"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / name)
        item.image.uri = Path(FIGURE_DIR) / name
        bbox = [round(float(v), 3) for v in (box.l, box.t, box.r, box.b)]
        crop = figure_crop(
            bbox, (size.width, size.height), others.get(prov.page_no) or []
        )
        records.append(
            {
                "id": ident,
                "page": prov.page_no,
                "bbox": bbox,
                "crop": [round(v, 3) for v in crop],
                "file": f"{ASSETS_DIR}/{FIGURE_DIR}/{name}",
                "width": display_width(bbox, size.width),
                "fallback": f"{DOCLING_IMAGE_DIR}/{source.name}",
            }
        )
    return records


def page_boxes(document):
    """`{page: [[left, top, right, bottom], ...]}` of every positioned item,
    top-left origin, the frame of a record's `bbox` (the same walk as
    `pdf_formula.neighbours`, turned the way `name_pictures` turns a
    picture's box)."""
    boxes = {}
    for item, _level in document.iterate_items(with_groups=False):
        for prov in getattr(item, "prov", None) or []:
            page = document.pages.get(prov.page_no)
            if page is None:
                continue
            box = prov.bbox.to_top_left_origin(page_height=page.size.height)
            boxes.setdefault(prov.page_no, []).append(
                [float(box.l), float(box.t), float(box.r), float(box.b)]
            )
    return boxes


def figure_crop(box, page_size, others, pad=FIGURE_PAD_PT):
    """`box` grown by up to `pad` points per side: `[left, top, right, bottom]`.

    Top-left origin, points. Each side stops `pdf_formula.PAD_CLEAR` short
    of the nearest item in that direction and at the page edge; no
    minimum, so a caption 1.5 pt below leaves 0.5 pt. Items inside the
    figure box (its own labels, the picture itself) do not count; one
    that overlaps the box's edge leaves no padding on the sides it
    reaches past; one off a corner holds both of that corner's sides.
    """
    from .pdf_formula import PAD_CLEAR

    left, top, right, bottom = (float(v) for v in box)
    width, height = (float(v) for v in page_size)
    room = {"left": pad, "top": pad, "right": pad, "bottom": pad}
    inside = 0.5
    for o_left, o_top, o_right, o_bottom in others:
        if (
            o_left >= left - inside
            and o_top >= top - inside
            and o_right <= right + inside
            and o_bottom <= bottom + inside
        ):
            continue
        if (
            o_right <= left - pad
            or o_left >= right + pad
            or o_bottom <= top - pad
            or o_top >= bottom + pad
        ):
            continue
        if o_right > left and o_left < right and o_bottom > top and o_top < bottom:
            # Over the box's edge: no room on each side it reaches past.
            if o_left < left:
                room["left"] = 0.0
            if o_top < top:
                room["top"] = 0.0
            if o_right > right:
                room["right"] = 0.0
            if o_bottom > bottom:
                room["bottom"] = 0.0
            continue
        gaps = {}
        if o_top >= bottom:
            gaps["bottom"] = o_top - bottom
        if o_bottom <= top:
            gaps["top"] = top - o_bottom
        if o_left >= right:
            gaps["right"] = o_left - right
        if o_right <= left:
            gaps["left"] = left - o_right
        for side, gap in gaps.items():
            room[side] = min(room[side], max(0.0, gap - PAD_CLEAR))
    return [
        max(0.0, left - room["left"]),
        max(0.0, top - room["top"]),
        min(width, right + room["right"]),
        min(height, bottom + room["bottom"]),
    ]


def _reference(record):
    """The Markdown target a record's picture is written with, in staging."""
    return f"{FIGURE_DIR}/{Path(record['file']).name}"


def add_widths(markdown, records):
    """Each named figure's reference, with its width and without alt text.

    By name, which is the item's identity: `![Image](figures/p0003-01.png)`
    becomes `![](figures/p0003-01.png){width=76%}`. So more pixels never
    change how large the figure is shown, only how sharp. The alt text goes
    because docling writes the word "Image" for every picture, and Pandoc
    turns an image with alt text that stands alone in a paragraph into a
    figure captioned with it (a hidden `<figcaption>Image</figcaption>`
    under every figure); an empty one is a plain `<img>`, as the formula
    pictures already are.
    """
    for record in records:
        pattern = re.compile(
            r"!\[[^\]\n]*\](\(" + re.escape(_reference(record)) + r"\))(?!\{)"
        )
        markdown = pattern.sub(
            lambda match: f"![]{match.group(1)}{{width={record['width']}%}}", markdown
        )
    return markdown


def referenced(records, markdown):
    """The records whose picture the final Markdown still names."""
    return [r for r in records if f"]({_reference(r)})" in markdown]


def write_records(path, records):
    Path(path).write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def records_path(bundle):
    return bundle.work_file("extraction") / FIGURES_FILE


def forget(bundle):
    """A new extraction: the drawn figures' record and its limitations go.

    The files under `assets/figures/` are replaced by the extraction's own
    stand-ins, so a `figures` block left behind would claim a drawing that
    is no longer there, and the next run would draw nothing.
    """
    data = bundle.read_manifest()
    old = data.pop("figures", None)
    if old is None:
        return
    bundle.write_manifest(data)
    bundle.drop_limitations(old.get("limitations") or [])


def clear_assets(bundle):
    """Remove the previous extraction's figure files before the import.

    The importer gives a file whose name is taken by other bytes a
    `-2` suffix; the stable names must land on their own names.
    """
    shutil.rmtree(Path(bundle.assets) / FIGURE_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# The render step
# --------------------------------------------------------------------------
LEGACY_PICTURE = re.compile(
    r"!\[[^\]\n]*\]\(<?" + ASSETS_DIR + r"/images/(?!formula_)[^)\n]+\)"
)
# A drawn figure as source.md shows it: an image, `![...](assets/figures/
# p0003-01.png...)`, never a plain link.
DRAWN_REFERENCE = re.compile(
    r"!\[[^\]\n]*\]\(<?("
    + ASSETS_DIR
    + "/"
    + FIGURE_DIR
    + r"/p\d{4,}-\d{2,}\.png)(?=[>)\s])"
)
# What the book will not show: HTML comments and fenced code blocks. An
# operator who comments a figure out, or quotes its line in code, has
# removed it (Codex re-verify 260925: the raw-text match still drew it).
_HIDDEN = re.compile(
    r"<!--.*?-->|^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def shown_markdown(text):
    """`text` without the parts a reader never sees as images."""
    return _HIDDEN.sub("", text)


def render_figures(bundle, pdf_path, policy=FIGURE_POLICY_DEFAULT):
    """Draw the bundle's figures at `policy`, unless they already are.

    Called after the extraction (whether it ran or was reused) and before
    the EPUB is built. Returns the manifest's `figures` block, or None when
    the bundle has no extraction to draw from.

    - No completed PDF extraction (a Markdown import, a stub stage): nothing.
    - source.md names no drawn figure and there is no `figures.json`: a
      bundle made before this step. It is left exactly as it is, and
      `FIGURES_LEGACY` is said once if it names docling's pictures.
    - Otherwise the record must list every figure source.md names
      (`_read_records`), or the run stops (`FIGURE_RECORD_BROKEN`) before
      anything is drawn or reused. A recorded figure source.md no longer
      names (the operator removed it) is skipped -- not drawn, not
      counted, its file left alone -- with `FIGURES_UNREFERENCED`.
    - The manifest's block matches (`_still_drawn`: policy, revision, the
      digest of the named figures' records, every file's size and hash,
      nothing failed): nothing is drawn.
    - Otherwise every figure is drawn again. One that cannot be drawn gets
      docling's picture under its name, a line and a limitation, and is
      tried again on the next run; with docling's picture gone too the run
      stops (`FIGURE_FALLBACK_MISSING`).
    """
    manifest = bundle.read_manifest()
    extract = ((manifest.get("stages") or {}).get("extract") or {}).get("status")
    if extract != "completed":
        return None
    text = bundle.source.read_text(encoding="utf-8") if bundle.source.is_file() else ""
    text = shown_markdown(text)
    named = sorted(set(DRAWN_REFERENCE.findall(text)))
    path = records_path(bundle)
    if not named and not path.exists():
        # Legacy is decided from source.md alone: docling's pictures and
        # not one stable name.
        if LEGACY_PICTURE.search(text):
            print(FIGURES_LEGACY)
        return None
    records, skipped = _read_records(path, named)
    if skipped:
        print(FIGURES_UNREFERENCED.format(count=len(skipped), ids=_ids(skipped)))
    digest = records_digest(records)
    old = manifest.get("figures")
    wanted = policy.to_manifest()
    if _still_drawn(bundle, old, wanted, digest, records):
        return old

    # Forgotten before the first file is touched: a drawing interrupted
    # half-way is then drawn again on the next run, whatever it asks for.
    forget(bundle)
    failed, lines, total, files, native = [], [], 0, {}, 0
    # Only a drawing reads the PDF's bytes.
    masked = masked_sizes(pdf_path) if records else None
    for record, outcome in _draw_all(pdf_path, records, bundle.root, policy, masked):
        destination = bundle.root / record["file"]
        if isinstance(outcome, BaseException):
            _fallback(record, bundle.root / record["fallback"], destination, outcome)
            line = FIGURE_RENDER_FAILED.format(
                id=record["id"],
                page=record["page"],
                policy=policy.describe(),
                err=f"{type(outcome).__name__}: {outcome}",
            )
            print(line)
            failed.append(record["id"])
            lines.append(line)
            state = {}
        else:
            state = outcome
            if state.get("capped_mp") is not None:
                print(
                    FIGURE_CAPPED.format(
                        id=record["id"],
                        page=record["page"],
                        mp=state["capped_mp"],
                        policy=policy.describe(),
                        dpi=round(state["effective_scale"] * 72),
                        cap=FIGURE_MAX_PIXELS / 1_000_000,
                    )
                )
            if state.get("native_dpi") is not None:
                native += 1
        size = destination.stat().st_size
        total += size
        files[record["id"]] = {
            "size": size,
            "sha256": sha256_file(destination),
            **{k: v for k, v in state.items() if k != "capped_mp"},
        }
    block = {
        "policy": wanted,
        "revision": FIGURE_REVISION,
        "count": len(records),
        "bytes": total,
        "failed": failed,
        # This drawing's own lines, so the next drawing takes them back.
        "limitations": lines,
        # What the reuse check compares (`_still_drawn`); never part of the
        # translation's identity (translate._translated_assets).
        "records_sha256": digest,
        "files": files,
    }
    bundle.update_manifest(figures=block)
    bundle.add_limitations(lines)
    if records:
        if (
            old
            and old.get("policy")
            and (old.get("policy") != wanted or old.get("revision") != FIGURE_REVISION)
        ):
            print(
                FIGURES_REDRAWN.format(
                    policy=policy.describe(),
                    old=_describe_recorded(old),
                )
            )
        print(
            FIGURES_DRAWN.format(
                count=len(records) - len(failed),
                policy=policy.describe(),
                size=human_size(total),
            )
        )
        if native:
            print(FIGURES_NATIVE.format(count=native, policy=policy.describe()))
    return block


def _read_records(path, named):
    """`(records, skipped)` from `figures.json`: the records of the figures
    in `named`, and the paths of the recorded figures it does not name.

    `named` are the bundle-relative figure paths source.md references. A
    record file that is missing, cannot be read or leaves a named figure
    out stops the run: the figures would otherwise be drawn wrong or not
    at all while the run looks fine. A recorded figure source.md does not
    name was removed by the operator and is only skipped.
    """

    def broken(problem):
        return PipelineError(
            FIGURE_RECORD_BROKEN.format(count=len(named), problem=problem),
            stage="figures",
        )

    if not path.is_file():
        raise broken(FIGURE_RECORD_MISSING)
    try:
        raw = path.read_bytes()
        records = json.loads(raw.decode("utf-8"))
        if not isinstance(records, list):
            raise ValueError("not a list")
        listed = []
        for record in records:
            if not isinstance(record, dict) or not is_drawn_figure(record["file"]):
                raise ValueError(f"not a figure record: {record!r}")
            if Path(record["file"]).stem != record["id"]:
                raise ValueError(f"id and file disagree: {record!r}")
            listed.append(record["file"])
        if len(set(listed)) != len(listed):
            raise ValueError("a figure is listed twice")
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise broken(FIGURE_RECORD_UNREADABLE) from err
    unlisted = sorted(set(named) - set(listed))
    if unlisted:
        raise broken(FIGURE_RECORD_UNLISTED.format(ids=_ids(unlisted)))
    wanted = set(named)
    skipped = sorted(set(listed) - wanted)
    return [r for r in records if r["file"] in wanted], skipped


def records_digest(records):
    """The reuse check's digest of the records drawn: the named figures'
    only, so a skipped record never forces a redraw."""
    return sha256_bytes(
        json.dumps(records, sort_keys=True, ensure_ascii=False).encode("utf-8")
    )


def _ids(paths, shown=10):
    """`p0001-01, p0002-01` from figure paths; past `shown`, `and N more`."""
    ids = [Path(p).stem for p in paths]
    text = ", ".join(ids[:shown])
    if len(ids) > shown:
        text += f" and {len(ids) - shown} more"
    return text


def _still_drawn(bundle, old, wanted, digest, records):
    """Whether the recorded drawing is exactly what is on disk now."""
    if not old or old.get("policy") != wanted:
        return False
    if old.get("revision") != FIGURE_REVISION:
        return False
    if old.get("records_sha256") != digest or old.get("failed"):
        return False
    files = old.get("files") or {}
    for record in records:
        entry = files.get(record["id"])
        destination = bundle.root / record["file"]
        if not entry or not destination.is_file():
            return False
        if destination.stat().st_size != entry.get("size"):
            return False
        if sha256_file(destination) != entry.get("sha256"):
            return False
    return True


def _describe_recorded(block):
    try:
        text = FigurePolicy.from_manifest(block["policy"]).describe()
    except (KeyError, TypeError, ValueError):
        return "an unrecorded policy"
    if block.get("revision") != FIGURE_REVISION:
        return f"{text}, drawing revision {block.get('revision')}"
    return text


def _fallback(record, source, destination, error):
    """docling's 72 DPI picture under the figure's own name, or a refusal.

    A figure file left as it was would claim a drawing that never happened,
    so without docling's picture the run stops and says how to recover.
    """
    try:
        data = Path(source).read_bytes()
        _check_png(data)
    except Exception as missing:
        raise PipelineError(
            FIGURE_FALLBACK_MISSING.format(
                id=record["id"],
                page=record["page"],
                err=f"{type(error).__name__}: {error}",
                path=source,
            ),
            stage="figures",
        ) from missing
    _replace(destination, lambda partial: partial.write_bytes(data))


def _check_png(data):
    """Raise unless `data` is an image Pillow can read (a PNG signature
    without Pillow)."""
    try:
        from PIL import Image
    except ImportError:
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("not a PNG")
        return
    import io

    with Image.open(io.BytesIO(data)) as image:
        image.verify()


def _replace(destination, write):
    """Write through a sibling and a rename: a figure file is never half
    written, and never a leftover under assets/, where every file is hashed."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.part")
    try:
        write(partial)
        os.replace(partial, destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


_WIDTH = re.compile(rb"/Width\s+(\d+)(?![\d\s]*R\b)")
_HEIGHT = re.compile(rb"/Height\s+(\d+)(?![\d\s]*R\b)")
# A soft mask set by a graphics state (`/SMask` in an ExtGState), which no
# image dictionary shows: its dictionary written inline, or the soft-mask
# dictionary itself (`/S /Alpha` or `/S /Luminosity` is required there
# and used nowhere else), wherever it stands. `/SMask /None` matches
# neither.
_STATE_SOFT_MASK = re.compile(rb"/SMask\s*<<|/S\s*/(?:Alpha|Luminosity)\b")
# The file is read this many bytes at a time, each piece scanned with the
# previous one's last `SCAN_OVERLAP` bytes in front, so a match up to that
# long is whole in one piece: `_STREAM_OBJECT`'s dictionary is at most
# 8192 bytes, and the overlap leaves room for the object header and the
# whitespace around it.
SCAN_CHUNK = 8 * 1024 * 1024
SCAN_OVERLAP = 64 * 1024


def masked_sizes(pdf_path, chunk=SCAN_CHUNK, overlap=SCAN_OVERLAP):
    """The pixel sizes of the PDF's masked pictures, or None if unknown.

    pypdfium2 does not say whether an image object carries a `/SMask` or
    `/Mask` (whose resolution can be higher than the picture's own), so
    the file's image dictionaries are read as bytes, the same scan as
    `pdf_render.has_jbig2_mask`, and a picture on the page whose size is
    one of these is not trusted with the native path. An unmasked picture
    that happens to share a masked one's size is only drawn normally.
    None -- the file cannot be read, a masked picture's size is not
    written plainly, or a graphics state anywhere carries a soft mask
    (`_STATE_SOFT_MASK`) -- means no picture is trusted.

    The file is read `chunk` bytes at a time, never whole. An object
    stream (`/ObjStm`) is compressed and not seen here; `native_dpi`
    asks pdfium about each picture's own graphics state as well.
    """
    from .pdf_render import _STREAM_OBJECT

    sizes = set()
    try:
        with open(pdf_path, "rb") as handle:
            tail = b""
            while True:
                piece = handle.read(chunk)
                if not piece:
                    break
                data = tail + piece
                tail = data[-overlap:] if overlap else b""
                if _STATE_SOFT_MASK.search(data):
                    return None
                if b"Mask" not in data:
                    continue
                for match in _STREAM_OBJECT.finditer(data):
                    dictionary = match.group(3)
                    if b"/Image" not in dictionary or not re.search(
                        rb"/S?Mask\b", dictionary
                    ):
                        continue
                    width = _WIDTH.search(dictionary)
                    height = _HEIGHT.search(dictionary)
                    if not width or not height:
                        return None
                    sizes.add((int(width.group(1)), int(height.group(1))))
    except OSError:
        return None
    return sizes


def _draw_all(pdf_path, records, root, policy, masked=None):
    """`(record, state dict or the exception)` for each record, page by page.

    One page is open at a time, and each bitmap is released before the
    next is drawn: a long illustrated book must not hold its pages.
    """
    from .pdf_common import _pdfium

    try:
        pdfium, _raw = _pdfium()
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as err:
        for record in records:
            yield record, err
        return
    try:
        pages = {}
        for record in records:
            pages.setdefault(record["page"], []).append(record)
        for number in sorted(pages):
            try:
                page = document[number - 1]
            except Exception as err:
                for record in pages[number]:
                    yield record, err
                continue
            try:
                for record in pages[number]:
                    try:
                        state = _draw(
                            page,
                            record,
                            policy,
                            Path(root) / record["file"],
                            masked=masked,
                        )
                    except Exception as err:
                        yield record, err
                    else:
                        yield record, state or {}
            finally:
                page.close()
    finally:
        document.close()


def _crop_of(record):
    """The box drawn: the padded crop from the extraction, else the bbox
    (a record written before padding)."""
    return [float(v) for v in (record.get("crop") or record["bbox"])]


def _draw(page, record, policy, destination, *, masked=None):
    """Render one record's crop from `page` at `policy` into `destination`.

    Returns the figure's state: `requested_scale` (the policy's),
    `effective_scale` (what was drawn), `native_dpi` when the figure is
    one embedded picture drawn at its own resolution, and `capped_mp`
    (the megapixels the policy asked for) when the ceiling held it back.

    The margins come from the formula crop's own frame
    (`pdf_formula._crop`: the page as rendered, CropBox after /Rotate,
    clamped to it). The bitmap is never allocated above
    `FIGURE_MAX_PIXELS`: the scale is settled before `render`.
    """
    from .pdf_formula import _crop

    width, height = (float(v) for v in page.get_size())
    requested = figure_scale(record["bbox"], (width, height), policy)
    left, top, right, bottom = _crop_of(record)
    margins, _share = _crop(
        page, (left, height - bottom, right, height - top), 0.0, (0.0, 0.0)
    )
    if margins is None:
        raise ValueError(f"the figure box {record['bbox']} is outside the page")
    crop_w = width - margins[0] - margins[2]
    crop_h = height - margins[1] - margins[3]
    scale = requested
    state = {"requested_scale": round(requested, 6)}
    native = native_dpi(page, (left, top, right, bottom), masked, record["bbox"])
    if native is not None and native / 72.0 < scale:
        scale = native / 72.0
        state["native_dpi"] = round(native, 2)
    capped = capped_scale(crop_w, crop_h, scale)
    if capped < scale:
        wide, high = pixel_size(crop_w, crop_h, scale)
        state["capped_mp"] = wide * high / 1_000_000
        scale = capped
    state["effective_scale"] = round(scale, 6)
    bitmap = page.render(
        scale=scale, crop=_snapped(margins, (width, height), scale), rotation=0
    )
    try:
        image = bitmap.to_pil()
        try:
            if image.mode != "RGB":
                converted = image.convert("RGB")
                image.close()
                image = converted
            _replace(
                destination,
                lambda partial: image.save(partial, format="PNG", optimize=True),
            )
        finally:
            image.close()
    finally:
        bitmap.close()
    return state


def pixel_size(crop_w, crop_h, scale):
    """The bitmap `_snapped` asks pypdfium2 for: whole pixels per side."""
    return max(1, round(crop_w * scale)), max(1, round(crop_h * scale))


def capped_scale(crop_w, crop_h, scale, limit=FIGURE_MAX_PIXELS):
    """`scale`, or the largest below it whose bitmap stays within `limit`.

    Decided from the crop's size in points, before anything is rendered;
    the rounded pixel counts are checked and the scale stepped down until
    they fit.
    """
    area = crop_w * crop_h
    if area <= 0:
        return scale
    scale = min(scale, math.sqrt(limit / area))
    while True:
        w, h = pixel_size(crop_w, crop_h, scale)
        if w * h <= limit:
            return scale
        scale *= 0.999


def native_dpi(page, crop, masked=None, box=None):
    """The resolution of the one picture that is this figure, or None.

    All must hold, else None (the figure is rendered normally): the page
    is not rotated; exactly one top-level page object of any type (text,
    path, shading, form, image) intersects `crop`, and it is an image --
    an object whose bounds are a single point (an empty form, a link
    anchor) paints nothing and is not counted; its matrix has no
    rotation, skew or flip; its placement covers `box` (the detected box;
    `crop` when not given) within `NATIVE_COVER_TOLERANCE`. The DPI is
    the image's pixel size over its placed size in points (the larger of
    the two axes), never the file's metadata. Its pixel size must not be
    one of `masked` (`masked_sizes`); `masked` None trusts no picture.
    pdfium must say the picture is drawn without transparency (a soft
    mask, alpha or blend mode from its graphics state) and without a clip
    other than one axis-aligned rectangle (`_plainly_clipped`); where it
    cannot say, the picture is not trusted.
    """
    import pypdfium2.raw as raw

    if masked is None or page.get_rotation() % 360:
        return None
    crop_l, crop_t, crop_r, crop_b = crop
    box_l, box_b, box_r, box_t = page.get_cropbox()
    hits = []
    for obj in page.get_objects(max_depth=0):
        left, bottom, right, top = obj.get_bounds()
        shown = (left - box_l, box_t - top, right - box_l, box_t - bottom)
        if right <= left and top <= bottom:
            continue
        if (
            shown[0] < crop_r
            and shown[2] > crop_l
            and shown[1] < crop_b
            and shown[3] > crop_t
        ):
            hits.append(obj)
            if len(hits) > 1:
                return None
    if len(hits) != 1 or hits[0].type != raw.FPDF_PAGEOBJ_IMAGE:
        return None
    image = hits[0]
    matrix = image.get_matrix()
    a, b, c, d, e, f = matrix.get()
    if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:
        return None
    placed = (e - box_l, box_t - (f + d), e + a - box_l, box_t - f)
    cover_l, cover_t, cover_r, cover_b = box if box is not None else crop
    tolerance = NATIVE_COVER_TOLERANCE
    if not (
        placed[0] <= cover_l + tolerance
        and placed[1] <= cover_t + tolerance
        and placed[2] >= cover_r - tolerance
        and placed[3] >= cover_b - tolerance
    ):
        return None
    px_w, px_h = image.get_px_size()
    if px_w <= 0 or px_h <= 0 or (px_w, px_h) in masked:
        return None
    if not _plainly_drawn(image):
        return None
    return max(px_w * 72.0 / a, px_h * 72.0 / d)


def _plainly_drawn(image):
    """Whether pdfium says `image` is drawn with no transparency and at
    most a rectangular clip; False when it cannot say.

    A rectangle is allowed because pdfTeX clips every included picture to
    its bounding box (measured: mit_lecnotes12 page 5, 0.44 pt inside the
    picture); a rectangle trims straight picture edges and adds no edge of
    its own for more pixels to sharpen. Any other clip (a curve, several
    paths) is a vector edge, and the picture is drawn normally.
    """
    import pypdfium2.raw as raw

    try:
        if raw.FPDFPageObj_HasTransparency(image.raw):
            return False
        clip = raw.FPDFPageObj_GetClipPath(image.raw)
        if not clip:
            return True
        paths = raw.FPDFClipPath_CountPaths(clip)
        if paths < 1:
            return True
        return paths == 1 and _is_rectangle(clip)
    except Exception:
        return False


def _is_rectangle(clip):
    """Whether the clip's one path is an axis-aligned rectangle."""
    import ctypes

    import pypdfium2.raw as raw

    points = []
    for index in range(raw.FPDFClipPath_CountPathSegments(clip, 0)):
        segment = raw.FPDFClipPath_GetPathSegment(clip, 0, index)
        kind = raw.FPDFPathSegment_GetType(segment)
        if kind != (raw.FPDF_SEGMENT_MOVETO if index == 0 else raw.FPDF_SEGMENT_LINETO):
            return False
        x, y = ctypes.c_float(), ctypes.c_float()
        if not raw.FPDFPathSegment_GetPoint(segment, x, y):
            return False
        points.append((x.value, y.value))
    if len(points) == 5 and _same(points[0], points[-1]):
        points.pop()
    if len(points) != 4:
        return False
    for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]):
        if not (abs(x0 - x1) < 1e-3 or abs(y0 - y1) < 1e-3):
            return False
    xs = sorted({round(x, 3) for x, _y in points})
    ys = sorted({round(y, 3) for _x, y in points})
    return len(xs) == 2 and len(ys) == 2


def _same(p, q):
    return abs(p[0] - q[0]) < 1e-3 and abs(p[1] - q[1]) < 1e-3


def _snapped(margins, page_size, scale):
    """`margins` (points) that pypdfium2 turns into whole pixels exactly.

    pypdfium2 rounds the page and each margin up separately
    (`ceil(c * scale)`), so a box could lose up to two pixels of the width
    the policy asked for. Here the box's pixel width is decided once --
    `round(box width x scale)` -- and the margins are given as the whole
    pixel counts that leave exactly that, a hair under each so the
    library's ceiling lands on the count and not one past it.
    """
    left, bottom, right, top = margins
    width, height = page_size
    page_w = math.ceil(width * scale)
    page_h = math.ceil(height * scale)
    box_w, box_h = pixel_size(width - left - right, height - bottom - top, scale)
    left_px = min(round(left * scale), page_w - 1)
    top_px = min(round(top * scale), page_h - 1)
    box_w = min(box_w, page_w - left_px)
    box_h = min(box_h, page_h - top_px)
    pixels = (left_px, page_h - top_px - box_h, page_w - left_px - box_w, top_px)
    return tuple(max(0.0, (count - 1e-6) / scale) if count else 0.0 for count in pixels)


def human_size(count):
    """`812 B`, `640 KB`, `1.2 MB` (decimal units)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f} MB"
    if count >= 1_000:
        return f"{count / 1_000:.0f} KB"
    return f"{count} B"
