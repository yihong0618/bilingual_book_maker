"""The page image, from pypdfium2, for PDFs docling-parse renders wrongly.

docling-parse 7.20 (the default backend of docling 2.129) ignores an image
`/Mask` or `/SMask` whose stream is JBIG2-encoded and paints the base image
unmasked. The scans made that way -- Internet Archive and ABBYY FineReader
"mixed raster content" books -- come back as a brown or grey smear, or
black, the OCR reads nothing or garbage on it, and the conversion still
reports success. docling issue #4329 reports it upstream. pypdfium2 renders
the same pages correctly.

Switching the whole backend to pypdfium2 was measured and rejected: tables
lose columns, hyperlinks go, and an invisible OCR layer is kept over fresh
OCR. So only the page image changes hands: docling-parse still reads the
text cells, shapes and bitmap rectangles, and pypdfium2 paints the pixels
the layout, table and OCR models look at. Measured on the Chinese Internet
Archive scan, Han CER 0.249 -> 0.010; born-digital pages unchanged in 15 of
17 slices. Evidence: docs/260923-eval-PDF_BACKEND_JBIG2_MASK_RENDER.md.

The switch is per document and decided by `has_jbig2_mask`, a byte scan of
the file, because pypdfium2 does not expose masks and pikepdf is not a
dependency.

docling is imported only when `pdfium_image_backend()` first builds the
class, so the byte scan needs neither docling nor its import time.
"""

import math
import re
from pathlib import Path

# A stream object: `N G obj << dictionary >> stream`. The dictionary may
# nest (`/DecodeParms << ... >>`), so it runs to the first `>>` that is
# followed by `stream`, and never past an `endobj`, so a dictionary-only
# object (an ExtGState, a font) is never read as part of the stream object
# after it. The bound is generous for an image dictionary with an inline
# palette.
_STREAM_OBJECT = re.compile(
    rb"(\d+)\s+(\d+)\s+obj\s*<<((?:(?!endobj).){0,8192}?)>>\s*stream",
    re.S,
)
_MASK_REF = re.compile(rb"/S?Mask\s+(\d+)\s+(\d+)\s+R")


def has_jbig2_mask(pdf_path):
    """Whether some image in the PDF is masked by a JBIG2-encoded stream.

    The defect's signature (docling issue #4329): an image dictionary
    carries `/Mask N G R` or `/SMask N G R`, and object `N G` is a stream
    whose dictionary names `/JBIG2Decode`. Measured on pages 1-2 of 60
    fixtures (120 pages): it flagged the 8 pages whose docling-parse render
    differs from pypdfium2's, and no other page
    (docs/260923-eval-PDF_BACKEND_JBIG2_MASK_RENDER.md, "Task 3").

    A byte scan, read once: image XObjects are streams, streams never live
    in object streams, and encryption leaves dictionaries in the clear, so
    both halves of the signature are plain bytes in any PDF. It is
    object-level: a reference counts only inside a stream object's own
    dictionary, so a `/SMask` in an ExtGState (a dictionary, not a stream)
    pointing at a JBIG2 stream does not flag. A JBIG2 image drawn as
    itself, with no mask pointing at it, does not flag either: docling-parse
    renders that correctly. File-level: it does not say which pages.
    """
    data = Path(pdf_path).read_bytes()
    if b"JBIG2Decode" not in data or b"Mask" not in data:
        return False
    jbig2 = set()
    masks = set()
    for match in _STREAM_OBJECT.finditer(data):
        dictionary = match.group(3)
        if b"/JBIG2Decode" in dictionary:
            jbig2.add((int(match.group(1)), int(match.group(2))))
        for ref in _MASK_REF.finditer(dictionary):
            masks.add((int(ref.group(1)), int(ref.group(2))))
    return bool(jbig2 & masks)


def _pixel_box(size, full, cropbox):
    """The pixels docling-parse cuts from its full image for `cropbox`.

    `size` is the page's displayed size in points, `full` the full image's
    `(width, height)` in pixels. The same arithmetic as docling-parse's
    `PageParseResult._crop_image`, so the crop has its exact pixel size.
    """
    width, height = full
    if cropbox is None or size.width <= 0 or size.height <= 0:
        return 0, 0, width, height
    box = cropbox.to_top_left_origin(page_height=size.height)
    x_scale = width / size.width
    y_scale = height / size.height
    left = max(0, round(box.l * x_scale))
    top = max(0, round(box.t * y_scale))
    right = min(width, round(box.r * x_scale))
    bottom = min(height, round(box.b * y_scale))
    return left, top, right, bottom


# pypdfium2 renders at this multiple of the asked scale and the image is
# downsampled to size: docling's own pypdfium2 backend does the same, and
# the measured hybrid (Han CER 0.010) rendered this way.
OVERSAMPLE = 1.5


def _render(page, scale, size, cropbox):
    """`page` (a pypdfium2 page) as docling-parse would size it, RGB.

    docling-parse's full image at `scale` is `ceil(side * scale)` pixels
    per side -- the same as pypdfium2's own render size (measured on 108
    page/scale/rotation cases, 260923) -- and a crop is cut from it by
    `_pixel_box`. The region those pixels cover is rendered at the
    oversampled scale and resized to exactly that many pixels, so every
    box downstream, which is in docling-parse's frame, lands on the same
    pixels. pypdfium2's width and height, its render and its crop are all
    in the page's displayed frame, `/Rotate` applied, as docling-parse's
    are; `rotation=0` adds no turn of its own.
    """
    points_w, points_h = page.get_width(), page.get_height()
    full = (math.ceil(points_w * scale), math.ceil(points_h * scale))
    left, top, right, bottom = _pixel_box(size, full, cropbox)
    if right <= left or bottom <= top:
        # A box off the page: docling-parse hands these pixels to PIL's
        # crop, which refuses a negative side and returns an empty image
        # for a zero one; the same answers here.
        if right < left or bottom < top:
            raise ValueError(f"the crop box {cropbox} lies outside the page")
        from PIL import Image

        return Image.new("RGB", (right - left, bottom - top))
    x_scale = full[0] / points_w
    y_scale = full[1] / points_h
    crop = (
        left / x_scale,
        points_h - bottom / y_scale,
        points_w - right / x_scale,
        top / y_scale,
    )
    bitmap = page.render(scale=scale * OVERSAMPLE, rotation=0, crop=crop)
    try:
        image = bitmap.to_pil().copy()
    finally:
        bitmap.close()
    return image.resize((right - left, bottom - top)).convert("RGB")


def pdfium_image_backend():
    """`PdfiumImageParseBackend`, the class to hand docling's `PdfFormatOption`.

    Built on first call, because it subclasses docling's backend and this
    module is imported for the byte scan alone too; the same class every
    call after that.
    """
    if _BACKEND:
        return _BACKEND[0]

    from io import BytesIO

    import pypdfium2 as pdfium
    from docling.backend.docling_parse_backend import (
        ThreadedDoclingParseDocumentBackend,
        ThreadedDoclingParsePageBackend,
    )
    from docling.utils.locks import pypdfium2_lock

    class PdfiumImageParsePage(ThreadedDoclingParsePageBackend):
        """docling-parse's page, with pypdfium2's page image."""

        def __init__(self, result, document, rendered=True):
            super().__init__(result, rendered=rendered)
            self._document = document

        def get_page_image(self, scale=1, cropbox=None):
            size = self.get_size()
            # PDFium is not thread-safe, and docling's pipeline asks for
            # page images from its worker threads; this is the lock every
            # pypdfium2 call in docling takes.
            with pypdfium2_lock:
                page = self._document[self.page_no - 1]
                try:
                    return _render(page, scale, size, cropbox)
                finally:
                    page.close()

    class PdfiumImageParseBackend(ThreadedDoclingParseDocumentBackend):
        """docling-parse for everything but the page image.

        One pypdfium2 document for the whole conversion, shared by its
        pages and used only under docling's pypdfium2 lock; closed with
        the backend.
        """

        def __init__(self, in_doc, path_or_stream, options=None):
            super().__init__(in_doc, path_or_stream, options)
            source = path_or_stream
            if isinstance(source, BytesIO):
                source = source.getvalue()
            with pypdfium2_lock:
                self._pdfium = pdfium.PdfDocument(source)

        def iter_pages(self):
            for page in super().iter_pages():
                yield PdfiumImageParsePage(
                    page._result, self._pdfium, rendered=page._rendered
                )

        def unload(self):
            try:
                super().unload()
            finally:
                document, self._pdfium = self._pdfium, None
                if document is not None:
                    with pypdfium2_lock:
                        document.close()

    _BACKEND.append(PdfiumImageParseBackend)
    return PdfiumImageParseBackend


_BACKEND = []
