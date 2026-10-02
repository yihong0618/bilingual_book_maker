"""Shared pieces for the bundle-pipeline tests.

A deterministic stand-in translator, registered as an ordinary endpoint
format, so the tests drive `book_maker.cli.main` exactly the way an operator
does instead of reaching past it.
"""

import base64

from book_maker.pipeline.messages import PANDOC_REQUIRED
from book_maker.pipeline.preflight import find_pandoc
from book_maker.pipeline.errors import PipelineError

# 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhg"
    "GAWjR9awAAAABJRU5ErkJggg=="
)

FIXTURE = """# Chapter One

The first paragraph is ordinary prose that the model will translate.

The second paragraph carries a [link](https://example.com) and `inline code`,
and its translation comes back as two paragraphs.

![A plate](assets/plate.png)

- first item
- second item

```python
print("never translated")
```

| a | b |
| --- | --- |
| 1 | 2 |

## Notes

A closing paragraph under the second heading.
"""


class FakeTranslator:
    """Fixed answers, so a rendered book is a fact and not a sample."""

    SUPPORTS_REQUEST_EXTRAS = False
    SUPPORTS_BATCH_API = False
    SUPPORTS_SESSION_CONTEXT = False
    SUPPORTS_STRUCTURED_OUTPUT = False

    instances = []
    fail_after = None  # raise KeyboardInterrupt once this many texts are done

    def __init__(self, key, language, api_base=None, **kwargs):
        self.language = language
        self.model_name = "fake-test-model"
        self.translated = []
        self.context_list = []
        self.context_translated_list = []
        type(self).instances.append(self)

    # The one deterministic rule, plus a second paragraph for one block.
    def _answer(self, text):
        stripped = text.strip()
        if stripped.startswith("#"):
            hashes, _, title = stripped.partition(" ")
            return f"{hashes} 译:{title.strip()}"
        if "two paragraphs" in stripped:
            return "译:第一段。\n\n译:第二段。"
        lines = stripped.splitlines()
        if lines and all(line.startswith("- ") for line in lines):
            return "\n".join(f"- 译:{line[2:]}" for line in lines)
        return f"译:{stripped}"

    def translate(self, text):
        self._account(text)
        return self._answer(text)

    def translate_list(self, texts):
        return [self.translate(text) for text in texts]

    def _account(self, text):
        cls = type(self)
        done = sum(len(instance.translated) for instance in cls.instances)
        if cls.fail_after is not None and done >= cls.fail_after:
            raise KeyboardInterrupt("injected interruption")
        self.translated.append(text)

    # Attributes the CLI sets or reads on a translator it built.
    def set_interval(self, interval):
        pass


def register_fake_format(monkeypatch, name="faketest", cls=FakeTranslator):
    from book_maker import cli
    from book_maker.translator import FORMAT_DICT

    monkeypatch.setitem(FORMAT_DICT, name, cls)
    monkeypatch.setitem(cli.FORMAT_DICT, name, cls)
    cls.instances = []
    cls.fail_after = None
    return name


def pandoc_or_skip():
    import pytest

    try:
        return find_pandoc()
    except PipelineError as err:
        if err.detail != PANDOC_REQUIRED:
            raise  # a Pandoc too old for the route is a failure, not a skip
        pytest.skip(f"pandoc is not available on PATH: {err}")


def write_fixture(directory, text=FIXTURE):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "assets").mkdir(exist_ok=True)
    (directory / "assets" / "plate.png").write_bytes(PNG)
    book = directory / "book.md"
    book.write_text(text, encoding="utf-8")
    return book


def write_pdf(
    path,
    pages=("Hello from an embedded text layer.",),
    figure=None,
    figure_clip=True,
    figure_draws=True,
    figure_scale=1.0,
    figure_shown=1,
    scan=None,
    scan_nested=False,
    text_clip=None,
    cropbox=None,
    render_mode=None,
    rotate=None,
):
    """A real PDF, one page per entry; `None` writes a page with no text.

    Built by hand rather than by a library: the tests need a file pdfium
    can actually open and read, and the only thing they vary is whether a
    page carries a text layer at all.

    `figure=(page number, lines)` also draws a vector figure on that page:
    a Form XObject writing `lines` of text over a filled rectangle
    (`figure_draws`), placed under a clip window so small that only its
    first line shows (`figure_clip`) -- the shape of the arXiv teaser
    figure whose clipped-away copies flooded the extraction (260920).
    `figure_scale` enlarges it; 2.5 makes it a whole-page wrapper.

    `figure_shown` is how many of its lines the window shows.

    `scan=(page numbers)` covers those pages with one picture, the way a
    scanner's PDF does; whatever text the page entry carries sits on top of
    it, as a stamped page number would. `scan_nested` draws that picture
    from inside a Form XObject, as some scanners' producers do.

    `text_clip=(left, bottom, width, height)` puts every page's own text
    under that clip window, the page-level shape of clipped-away text.
    `cropbox=(left, bottom, right, top)` is written on the page tree, so
    every page inherits it rather than carrying its own.
    `render_mode={page number: mode}` sets that page's text render mode
    (`3 Tr` is invisible, the way a scanned book's OCR layer is written);
    a tuple sets it line by line. The mode is graphics state and outlives
    `ET`, so a visible line after an invisible one needs its own `0`.
    `rotate` writes `/Rotate` on every page (90: a portrait page shown
    landscape).
    """
    objects = []
    figure_page, figure_lines = figure if figure else (None, ())
    scan = set(scan or ())
    picture = None

    def add(body):
        objects.append(body)
        return len(objects)  # object numbers start at 1

    catalog = add(b"")  # 1, patched once the page tree number is known
    tree = add(b"")  # 2
    font = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    kids = []

    def escape(text):
        return (
            str(text)
            .replace("\\", r"\\")
            .replace("(", r"\(")
            .replace(")", r"\)")
            .encode("ascii", "replace")
        )

    for number, text in enumerate(pages, start=1):
        if text is None:
            stream = b""
        else:
            # One line per newline, so a test can put more on a page than
            # one line holds; text past the right edge is not "on" the page.
            lines = str(text).split("\n")
            modes = (render_mode or {}).get(number)
            if not isinstance(modes, (tuple, list)):
                modes = [modes] * len(lines)

            def tr(mode):
                return b"" if mode is None else b"%d Tr " % mode

            stream = b" ".join(
                b"BT %s/F1 18 Tf 72 %d Td (%s) Tj ET"
                % (tr(modes[i]), 700 - 24 * i, escape(line))
                for i, line in enumerate(lines)
            )
            if text_clip is not None:
                stream = (
                    b"q %.2f %.2f %.2f %.2f re W n " % tuple(text_clip) + stream + b" Q"
                )
        resources = b"/Font << /F1 %d 0 R >>" % font
        if number in scan:
            if picture is None:
                picture = add(
                    b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
                    b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\n"
                    b"stream\n\xc0\nendstream"
                )
            if scan_nested:
                wrapper = add(
                    b"<< /Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                    b"/Resources << /XObject << /Im1 %d 0 R >> >> /Length 30 >>\n"
                    b"stream\nq 612 0 0 792 0 0 cm /Im1 Do Q\nendstream" % picture
                )
                stream = b"q /Sx Do Q " + stream
                resources += b" /XObject << /Sx %d 0 R >>" % wrapper
            else:
                stream = b"q 612 0 0 792 0 0 cm /Im1 Do Q " + stream
                resources += b" /XObject << /Im1 %d 0 R >>" % picture
        if number == figure_page:
            # Eight bars, the way a chart draws: enough paths to be a figure
            # by drawing alone (FIGURE_MIN_DRAWINGS), where a callout box's
            # single rectangle is not.
            drawing = (
                b"0 0 1 rg "
                + b" ".join(b"%d 0 30 300 re f" % (i * 37) for i in range(8))
                + b" "
                if figure_draws
                else b""
            ) + b" ".join(
                b"BT /F1 12 Tf 10 %d Td (%s) Tj ET" % (280 - 14 * i, escape(line))
                for i, line in enumerate(figure_lines)
            )
            form = add(
                b"<< /Type /XObject /Subtype /Form /BBox [0 0 300 300] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Length %d >>\n"
                b"stream\n%s\nendstream" % (font, len(drawing), drawing)
            )
            # A window over the form's first `figure_shown` lines and
            # nothing below them.
            bottom = 284 - 14 * figure_shown
            window = (
                b"0 %d 300 %d re W n " % (bottom, 300 - bottom) if figure_clip else b""
            )
            stream += b" q %.2f 0 0 %.2f 10 10 cm %s/Fx Do Q" % (
                figure_scale,
                figure_scale,
                window,
            )
            if picture is not None and number in scan and not scan_nested:
                resources = resources.replace(
                    b"/XObject << /Im1 %d 0 R >>" % picture,
                    b"/XObject << /Im1 %d 0 R /Fx %d 0 R >>" % (picture, form),
                )
            elif picture is not None and number in scan:
                resources = resources.replace(
                    b"/XObject << /Sx %d 0 R >>" % wrapper,
                    b"/XObject << /Sx %d 0 R /Fx %d 0 R >>" % (wrapper, form),
                )
            else:
                resources += b" /XObject << /Fx %d 0 R >>" % form
        content = add(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
        turned = b"/Rotate %d " % rotate if rotate else b""
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] %s"
                b"/Resources << %s >> /Contents %d 0 R >>"
                % (tree, turned, resources, content)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
    inherited = b"/CropBox [%.2f %.2f %.2f %.2f]" % tuple(cropbox) if cropbox else b""
    objects[tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d %s >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids),
        len(kids),
        inherited,
    )

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        start,
    )
    path.write_bytes(bytes(out))
    return path


def write_image_pdf(
    path,
    placements,
    size=(612.0, 792.0),
    pixels=(96, 72),
    label=None,
    rotate=None,
    smask=False,
    empty_form=None,
    state_smask=False,
    clip=None,
):
    """A one-page PDF drawing an embedded RGB picture once per placement.

    `placements` are `cm` matrices `(a, b, c, d, e, f)` in PDF points; each
    draws its own image object (`/Im0`, `/Im1`, ...) of `pixels` size.
    `label=(x, y, text)` writes a line of text; `rotate` writes `/Rotate`;
    `smask` gives the first picture a soft mask. `empty_form=(x, y)` draws
    a Form XObject with nothing in it there (the shape of pdfTeX's link
    anchors). `state_smask` draws the first picture under a graphics
    state whose `/SMask` is a luminosity soft mask (an ExtGState object,
    not the image's own dictionary). `clip` is a path, in content-stream
    operators (`"100 600 m 172 600 l 136 654 l h"`), clipping the first
    picture.
    """
    px_w, px_h = pixels
    data = bytes(
        (x * 255 // max(1, px_w - 1), y * 255 // max(1, px_h - 1), 128)[c]
        for y in range(px_h)
        for x in range(px_w)
        for c in range(3)
    )
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    catalog = add(b"")
    tree = add(b"")
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    mask = None
    if smask:
        alpha = bytes(200 for _ in range(px_w * px_h))
        mask = add(
            b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
            b"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length %d >>\nstream\n"
            % (px_w, px_h, len(alpha))
            + alpha
            + b"\nendstream"
        )
    images = []
    for index, _matrix in enumerate(placements):
        extra = b" /SMask %d 0 R" % mask if (mask and index == 0) else b""
        images.append(
            add(
                b"<< /Type /XObject /Subtype /Image /Width %d /Height %d "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8%s /Length %d >>\n"
                b"stream\n" % (px_w, px_h, extra, len(data)) + data + b"\nendstream"
            )
        )
    state = None
    if state_smask:
        group = b"0.5 g 0 0 %.2f %.2f re f" % size
        luminosity = add(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 %.2f %.2f] "
            b"/Group << /S /Transparency /CS /DeviceGray >> /Length %d >>\n"
            b"stream\n" % (size[0], size[1], len(group)) + group + b"\nendstream"
        )
        state = add(
            b"<< /Type /ExtGState /SMask << /Type /Mask /S /Luminosity "
            b"/G %d 0 R >> >>" % luminosity
        )
    content = b""
    for index, matrix in enumerate(placements):
        numbers = b" ".join(b"%.4f" % float(v) for v in matrix)
        before = b""
        if index == 0 and state:
            before += b"/GS0 gs "
        if index == 0 and clip:
            before += clip.encode() + b" W n "
        content += b"q %s%s cm /Im%d Do Q\n" % (before, numbers, index)
    form = None
    if empty_form:
        form = add(
            b"<< /Type /XObject /Subtype /Form /BBox [0 0 1 1] /Length 0 >>\n"
            b"stream\n\nendstream"
        )
        content += b"q 1 0 0 1 %.2f %.2f cm /Fm0 Do Q\n" % empty_form
    if label:
        x, y, text = label
        content += b"BT /F1 10 Tf %.2f %.2f Td (%s) Tj ET\n" % (x, y, text.encode())
    stream = add(
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"
    )
    xobjects = b" ".join(b"/Im%d %d 0 R" % (i, n) for i, n in enumerate(images))
    if form:
        xobjects += b" /Fm0 %d 0 R" % form
    turn = b" /Rotate %d" % rotate if rotate else b""
    states = b" /ExtGState << /GS0 %d 0 R >>" % state if state else b""
    page = add(
        b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f]%s "
        b"/Resources << /Font << /F1 %d 0 R >> /XObject << %s >>%s >> "
        b"/Contents %d 0 R >>"
        % (tree, size[0], size[1], turn, font, xobjects, states, stream)
    )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
    objects[tree - 1] = b"<< /Type /Pages /Kids [%d 0 R] /Count 1 >>" % page
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref,
    )
    path.write_bytes(bytes(out))
    return path
