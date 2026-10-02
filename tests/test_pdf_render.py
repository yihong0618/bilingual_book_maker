"""The page image from pypdfium2, for PDFs whose JBIG2 masks docling-parse
paints wrongly (docling issue #4329).

The byte scan runs on hand-built files and needs nothing installed. The
backend tests need docling and compare the subclass's page image with
docling-parse's own on the same page, unturned and turned, because every
box downstream is in docling-parse's frame. Evidence for the switch:
docs/260923-eval-PDF_BACKEND_JBIG2_MASK_RENDER.md.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import write_pdf  # noqa: E402

from book_maker.pipeline import pdf_render  # noqa: E402


# --------------------------------------------------------------------------
# has_jbig2_mask: the byte scan
# --------------------------------------------------------------------------
def build_pdf(path, objects):
    """A PDF from `objects`, numbered from 1, with a correct xref.

    Each entry is the bytes between `N 0 obj` and `endobj`; a stream entry
    carries its own `stream ... endstream`. Nothing is decoded by the scan,
    so the JBIG2 data is a stand-in.
    """
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.write_bytes(bytes(out))
    return path


def stream(dictionary, data=b"\x00\x01\x02\x03"):
    return b"<< %s /Length %d >>\nstream\n%s\nendstream" % (
        dictionary,
        len(data),
        data,
    )


def page_objects(image_dict, extra=(), resources=b"/XObject << /Im1 5 0 R >>"):
    """Catalog, pages, page, content, image (5), then `extra` from 6 on."""
    content = b"q 100 0 0 100 0 0 cm /Im1 Do Q"
    return [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << " + resources + b" >> /Contents 4 0 R >>",
        stream(b"", content),
        stream(image_dict),
        *extra,
    ]


IMAGE = b"/Type /XObject /Subtype /Image /Width 4 /Height 4 /BitsPerComponent 8 "
JBIG2_STENCIL = stream(
    b"/Type /XObject /Subtype /Image /ImageMask true /Width 8 /Height 8 "
    b"/BitsPerComponent 1 /Filter /JBIG2Decode"
)
FLATE_STENCIL = stream(
    b"/Type /XObject /Subtype /Image /ImageMask true /Width 8 /Height 8 "
    b"/BitsPerComponent 1 /Filter /FlateDecode"
)


def test_an_image_masked_by_a_jbig2_stream_flags(tmp_path):
    pdf = build_pdf(
        tmp_path / "mask.pdf",
        page_objects(IMAGE + b"/ColorSpace /DeviceRGB /Mask 6 0 R", [JBIG2_STENCIL]),
    )
    assert pdf_render.has_jbig2_mask(pdf) is True


def test_an_image_soft_masked_by_a_jbig2_stream_flags(tmp_path):
    pdf = build_pdf(
        tmp_path / "smask.pdf",
        page_objects(IMAGE + b"/ColorSpace /DeviceRGB /SMask 6 0 R", [JBIG2_STENCIL]),
    )
    assert pdf_render.has_jbig2_mask(pdf) is True


def test_the_filter_as_an_array_with_nested_parameters_still_flags(tmp_path):
    """The Internet Archive shape: `/Filter [/JBIG2Decode]`, a nested
    `/DecodeParms` dictionary, and a dictionary-only object just before."""
    stencil = stream(
        b"/Type /XObject /Subtype /Image /ImageMask true /Width 8 /Height 8 "
        b"/BitsPerComponent 1 /Filter [/JBIG2Decode] "
        b"/DecodeParms << /JBIG2Globals 7 0 R >>"
    )
    pdf = build_pdf(
        tmp_path / "array.pdf",
        page_objects(
            IMAGE + b"/ColorSpace /DeviceRGB /Filter [/JPXDecode] /Mask 8 0 R",
            [b"<< /Type /ExtGState /CA 1 >>", stream(b""), stencil],
        ),
    )
    assert pdf_render.has_jbig2_mask(pdf) is True


def test_a_jbig2_image_drawn_as_itself_does_not_flag(tmp_path):
    # docling-parse renders a JBIG2 image correctly; only the mask case
    # is broken (the 260923 isolation variants).
    pdf = build_pdf(
        tmp_path / "direct.pdf",
        page_objects(
            b"/Type /XObject /Subtype /Image /ImageMask true /Width 8 /Height 8 "
            b"/BitsPerComponent 1 /Filter /JBIG2Decode"
        ),
    )
    assert pdf_render.has_jbig2_mask(pdf) is False


def test_a_flate_mask_does_not_flag(tmp_path):
    pdf = build_pdf(
        tmp_path / "flate.pdf",
        page_objects(
            IMAGE + b"/ColorSpace /DeviceRGB /Mask 6 0 R",
            [FLATE_STENCIL, JBIG2_STENCIL],  # a JBIG2 stream nobody masks with
        ),
    )
    assert pdf_render.has_jbig2_mask(pdf) is False


def test_a_pdf_without_jbig2_does_not_flag(tmp_path):
    pdf = write_pdf(tmp_path / "plain.pdf", ["Just text."])
    assert b"JBIG2Decode" not in pdf.read_bytes()
    assert pdf_render.has_jbig2_mask(pdf) is False


def test_a_soft_mask_in_a_graphics_state_does_not_flag(tmp_path):
    # PIN (lead, 260923, packet D): the scan is object-level -- a mask
    # reference counts only inside a stream object's dictionary. An
    # ExtGState is a dictionary, never a stream, so its /SMask is not an
    # image's mask even when the object it names is a JBIG2 stream.
    pdf = build_pdf(
        tmp_path / "gstate.pdf",
        page_objects(
            IMAGE + b"/ColorSpace /DeviceRGB",
            [b"<< /Type /ExtGState /SMask 7 0 R >>", JBIG2_STENCIL],
            resources=b"/XObject << /Im1 5 0 R >> /ExtGState << /G1 6 0 R >>",
        ),
    )
    assert pdf_render.has_jbig2_mask(pdf) is False


# The reproducer from docling issue #4329 (filed 2026-09-21 by
# ekarami2000-prog, https://github.com/docling-project/docling/issues/4329):
# one 348x536 pt page whose only content is a DeviceGray JPX image with a
# /Mask pointing at a JBIG2 /ImageMask. Correctly rendered the page is
# white; docling-parse ignores the mask and paints the image in full.
# Measured 260923 at scale 2 (696x1072): pypdfium2 direct 255.00,
# docling-parse 231.72, PdfiumImageParsePage 255.00 (mean absolute
# difference to the direct render 0.00004).
MASK_REPRO_PDF = (
    "JVBERi0xLjUKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2Jq"
    "CjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2Jq"
    "CjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCAzNDgg"
    "NTM2XSAvUmVzb3VyY2VzIDw8IC9YT2JqZWN0IDw8IC9JbTAgNCAwIFIgPj4gPj4gL0NvbnRlbnRz"
    "IDUgMCBSID4+CmVuZG9iago0IDAgb2JqCjw8L1R5cGUvWE9iamVjdC9TdWJ0eXBlL0ltYWdlL0lu"
    "dGVycG9sYXRlIHRydWUvV2lkdGggMzQ5L0hlaWdodCA1NjAvQml0c1BlckNvbXBvbmVudCA4L0Nv"
    "bG9yU3BhY2UKL0RldmljZUdyYXkvRmlsdGVyWy9KUFhEZWNvZGVdL01hc2sgNiAwIFIvTGVuZ3Ro"
    "IDQ3NT4+c3RyZWFtCgAAAAxqUCAgDQqHCgAAABRmdHlwanAyIAAAAABqcDIgAAAAWWpwMmgAAAAW"
    "aWhkcgAAAjAAAAFdAAEHBwEAAAAAD2NvbHIBAAAAAAARAAAALHJlcyAAAAAScmVzZCXMgAAlzIAA"
    "BAQAAAAScmVzYyXMgAAlzIAABAQAAAAAanAyY/9P/1EAKQAAAAABXQAAAjAAAAAAAAAAAAAAAV0A"
    "AAIwAAAAAAAAAAAAAQcBAf9SAAwAAAABAAUEBAAA/1wAIyJ3Hnbqdup2vG8AbwBu4mdMZ0xnZFAD"
    "UANQRVfSV9JXYf9kABEAAUtha2FkdS12Ni4zLjH/ZABZAAFLZHUtTGF5ZXItSW5mbzogbG9nXzJ7"
    "RGVsdGEtRChNU0UpL1syXjE2KkRlbHRhLUwoYnl0ZXMpXX0sIEwoYnl0ZXMpCiAtNTMuMCwgMy40"
    "ZSswMDIK/5AACgAAAAAAigAB/5PPsdAUAFyv1lZUiJRnoD2rbtsrYiJRnoD1UUfS3qQ20GouXd61"
    "KTnqatJznOc5znOc5znOdEw+Nm8271qUnNcMreo2h7VAAAATx2B6jhoez2szMzC8ZHK3qQ2mLLsj"
    "DJUAMI8NKa4ABMIJUAAAofYSU3gZrCjlGICAgICA/9kKZW5kc3RyZWFtCmVuZG9iago1IDAgb2Jq"
    "Cjw8IC9MZW5ndGggMzAgPj5zdHJlYW0KcSAzNDggMCAwIDUzNiAwIDAgY20gL0ltMCBEbyBRCmVu"
    "ZHN0cmVhbQplbmRvYmoKNiAwIG9iago8PC9UeXBlL1hPYmplY3QvU3VidHlwZS9JbWFnZS9JbnRl"
    "cnBvbGF0ZSB0cnVlL1dpZHRoIDEzOTQvSGVpZ2h0IDIyMzkvQml0c1BlckNvbXBvbmVudCAxL0lt"
    "YWdlTWFzawp0cnVlL0ZpbHRlclsvSkJJRzJEZWNvZGVdL0xlbmd0aCA4Mz4+c3RyZWFtCgAAAAAw"
    "AAEAAAATAAAFcgAACL8AAAEsAAABLBEAAAAAAAEnAAEAAAAqAAAFcgAACL8AAAAAAAAAAAIIA//9"
    "/wL+/v6rujaIBHRCjNTXNW4sz/+sCmVuZHN0cmVhbQplbmRvYmoKeHJlZgowIDcKMDAwMDAwMDAw"
    "MCA2NTUzNSBmIAowMDAwMDAwMDA5IDAwMDAwIG4gCjAwMDAwMDAwNTggMDAwMDAgbiAKMDAwMDAw"
    "MDExNSAwMDAwMCBuIAowMDAwMDAwMjQ1IDAwMDAwIG4gCjAwMDAwMDA5MDUgMDAwMDAgbiAKMDAw"
    "MDAwMDk4NCAwMDAwMCBuIAp0cmFpbGVyCjw8IC9TaXplIDcgL1Jvb3QgMSAwIFIgPj4Kc3RhcnR4"
    "cmVmCjEyMzYKJSVFT0YK"
)


def write_mask_repro(path):
    import base64

    path.write_bytes(base64.b64decode(MASK_REPRO_PDF))
    return path


def test_the_upstream_reproducer_flags(tmp_path):
    assert pdf_render.has_jbig2_mask(write_mask_repro(tmp_path / "repro.pdf"))


# --------------------------------------------------------------------------
# The backend: pypdfium2's page image in docling-parse's frame
# --------------------------------------------------------------------------
@pytest.fixture
def docling():
    pytest.importorskip("docling.backend.docling_parse_backend")
    pytest.importorskip("pypdfium2")
    from docling.backend.docling_parse_backend import (
        ThreadedDoclingParseDocumentBackend,
    )
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument
    from docling.datamodel.settings import DocumentLimits

    def pages(backend, pdf, page_range=None, **image):
        limits = DocumentLimits(page_range=page_range) if page_range else None
        document = InputDocument(
            path_or_stream=pdf, format=InputFormat.PDF, backend=backend, limits=limits
        )._backend
        try:
            return {
                page.page_no: page.get_page_image(**image)
                for page in document.iter_pages()
            }
        finally:
            document.unload()

    pages.default = ThreadedDoclingParseDocumentBackend
    pages.ours = pdf_render.pdfium_image_backend()
    return pages


def ink_box(image):
    """Where the dark pixels are."""
    return image.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()


def grey_difference(first, second):
    """Mean absolute grey difference of the two images, downscaled 4x."""
    from PIL import ImageChops, ImageStat

    size = (max(1, first.width // 4), max(1, first.height // 4))
    return ImageStat.Stat(
        ImageChops.difference(
            first.convert("L").resize(size), second.convert("L").resize(size)
        )
    ).mean[0]


def turned(tmp_path, rotation):
    """Two pages; the first turned by `/Rotate rotation`, on an A4 box so
    the sides are fractional points and the pixel rounding is exercised."""
    import pypdfium2 as pdfium

    source = write_pdf(tmp_path / "flat.pdf", ["A heading line", "Second page"])
    document = pdfium.PdfDocument(str(source))
    for page in document:
        page.set_mediabox(0, 0, 595.276, 841.89)
    document[0].set_rotation(rotation)
    pdf = tmp_path / f"turned{rotation}.pdf"
    document.save(str(pdf))
    document.close()
    return pdf


def test_the_backend_class_is_a_docling_parse_backend(docling):
    from docling.backend.docling_parse_backend import (
        ThreadedDoclingParseDocumentBackend,
    )

    assert issubclass(docling.ours, ThreadedDoclingParseDocumentBackend)
    assert docling.ours.__name__ == "PdfiumImageParseBackend"
    assert pdf_render.pdfium_image_backend() is docling.ours


# Measured 260923 on this fixture (docling 2.129, docling-parse 7.20,
# pypdfium2 5.13): sizes equal in every case; the text's ink box within
# 1 px; the 4x-downscaled grey difference 0.0-0.1 on a whole page and
# 2.7-4.0 on the text line's crop. A blank crop against the text line
# differs by about 35, so 10 separates "same text, other anti-aliasing"
# from "not the same picture".
TOLERANCE = 10.0


@pytest.mark.parametrize("rotation", [0, 90])
def test_the_page_image_matches_docling_parse_in_size_and_place(
    docling, tmp_path, rotation
):
    from docling_core.types.doc import BoundingBox, CoordOrigin

    pdf = turned(tmp_path, rotation)
    theirs = docling(docling.default, pdf, scale=1)
    ours = docling(docling.ours, pdf, scale=1)
    assert sorted(ours) == sorted(theirs) == [1, 2]
    for number in (1, 2):
        assert ours[number].mode == "RGB"
        assert ours[number].size == theirs[number].size
        assert grey_difference(ours[number], theirs[number]) < TOLERANCE
    # the turned page is turned in both, and its text is in the same place
    expected = (842, 596) if rotation else (596, 842)
    assert theirs[1].size == expected
    box_theirs, box_ours = ink_box(theirs[1]), ink_box(ours[1])
    assert box_theirs is not None
    assert all(abs(a - b) <= 2 for a, b in zip(box_theirs, box_ours))

    # A crop around the text line, at scale 2, in the displayed frame.
    left, top, right, bottom = box_theirs
    crop = BoundingBox(
        l=left - 5.3,
        t=top - 5.6,
        r=right + 5.45,
        b=bottom + 5.2,
        coord_origin=CoordOrigin.TOPLEFT,
    )
    theirs = docling(docling.default, pdf, (1, 1), scale=2, cropbox=crop)[1]
    ours = docling(docling.ours, pdf, (1, 1), scale=2, cropbox=crop)[1]
    assert ours.size == theirs.size
    assert ink_box(ours) is not None
    assert grey_difference(ours, theirs) < TOLERANCE


def test_a_page_range_renders_the_pages_it_names(docling, tmp_path):
    # docling-parse numbers a page within the whole file, from 1, when only
    # part of it is loaded; the pypdfium2 page is taken by that number.
    pdf = write_pdf(tmp_path / "three.pdf", ["First", None, "Third page text"])
    theirs = docling(docling.default, pdf, page_range=(3, 3), scale=1)
    ours = docling(docling.ours, pdf, page_range=(3, 3), scale=1)
    assert list(ours) == list(theirs) == [3]
    assert ink_box(ours[3]) is not None
    assert all(abs(a - b) <= 2 for a, b in zip(ink_box(theirs[3]), ink_box(ours[3])))


def test_unload_closes_the_pypdfium2_document(docling, tmp_path):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.document import InputDocument

    pdf = write_pdf(tmp_path / "one.pdf")
    backend = InputDocument(
        path_or_stream=pdf, format=InputFormat.PDF, backend=docling.ours
    )._backend
    assert backend._pdfium is not None
    backend.unload()
    assert backend._pdfium is None
    backend.unload()  # twice is harmless


def mean_grey(image):
    from PIL import ImageStat

    return ImageStat.Stat(image.convert("L")).mean[0]


def test_the_override_renders_the_masked_page_docling_parse_gets_wrong(
    docling, tmp_path
):
    # The assertion a  fails: on the
    # upstream reproducer docling-parse paints the unmasked image, ours
    # matches pypdfium2's own render.
    import pypdfium2 as pdfium
    from PIL import ImageChops, ImageStat

    pdf = write_mask_repro(tmp_path / "repro.pdf")
    theirs = docling(docling.default, pdf, scale=2)[1]
    ours = docling(docling.ours, pdf, scale=2)[1]
    document = pdfium.PdfDocument(str(pdf))
    try:
        direct = document[0].render(scale=2).to_pil()
    finally:
        document.close()
    assert mean_grey(theirs) < 240  # measured 231.72
    assert mean_grey(ours) > 250  # measured 255.00
    assert ours.size == theirs.size == direct.size
    difference = ImageChops.difference(ours.convert("L"), direct.convert("L"))
    assert ImageStat.Stat(difference).mean[0] < 1.0


@pytest.mark.parametrize("masked_first", [True, False])
def test_each_page_image_is_the_page_it_is_numbered(docling, tmp_path, masked_first):
    # The threaded parser hands pages back in any order; each image must be
    # its own page's. The two pages differ in size and in content: the
    # masked one is white, the typed one carries a dark text line.
    import pypdfium2 as pdfium

    repro = pdfium.PdfDocument(str(write_mask_repro(tmp_path / "repro.pdf")))
    typed = pdfium.PdfDocument(str(write_pdf(tmp_path / "typed.pdf", ["A typed line"])))
    mixed = pdfium.PdfDocument.new()
    for source in (repro, typed) if masked_first else (typed, repro):
        mixed.import_pages(source)
    pdf = tmp_path / "mixed.pdf"
    mixed.save(str(pdf))
    for document in (mixed, repro, typed):
        document.close()

    images = docling(docling.ours, pdf, scale=1)
    masked, typed_page = (1, 2) if masked_first else (2, 1)
    assert sorted(images) == [1, 2]
    assert images[masked].size == (348, 536)
    assert mean_grey(images[masked]) > 250
    assert images[typed_page].size == (612, 792)
    assert ink_box(images[typed_page]) is not None
