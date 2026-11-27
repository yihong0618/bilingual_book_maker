#!/usr/bin/env python3
"""Build the original EPUB 3 fixture using only the Python standard library."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent


def build(destination=None):
    destination = Path(destination) if destination else HERE / "table-columns.epub"
    members = {
        "mimetype": b"application/epub+zip",
        "META-INF/container.xml": b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        "EPUB/package.opf": b"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:031f331c-377d-4bbb-bcca-76f2b227a6ec</dc:identifier>
    <dc:title>Table Columns: An Original Test Fixture</dc:title>
    <dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="content"/></spine>
</package>""",
        "EPUB/nav.xhtml": b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">
  <head><title>Contents</title></head>
  <body><nav epub:type="toc"><h1>Contents</h1><ol>
    <li><a href="content.xhtml#title">Table columns</a></li>
  </ol></nav></body>
</html>""",
        "EPUB/content.xhtml": (HERE / "content.xhtml").read_bytes(),
    }
    with ZipFile(destination, "w") as archive:
        for name, data in members.items():
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_STORED if name == "mimetype" else ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return destination


if __name__ == "__main__":
    print(build())
