"""The contents of a `--to-epub` book, in both languages.

Pandoc names every contents entry after its heading, and the heading is the
original: the translation is the `bbm-translation` div the reading edition
puts right after it, which Pandoc renders as running text. Nothing Pandoc
reads changes an entry's label (pandoc 3.11 ignores `toc-text`, `title`,
`data-toc`, `toc-title` and `label` on a heading), so the finished package
is rewritten instead: each entry whose heading has a distinct translation
becomes `original / translation` (owner 261026).

The translation is copied from the chapter as Pandoc rendered it, so its
emphasis and its maths (MathML) are Pandoc's own, not a second reading of
the Markdown. Only inline content goes into a link: a paragraph is
unwrapped, a nested link is unwrapped, ids are dropped (they would repeat
the chapter's), and an image becomes its alt text. The NCX, which holds
plain text, gets the same label as text, a formula as its TeX.

Everything else in the package is copied byte for byte. The nav item is
given the `mathml` property whenever the nav holds a formula: a translated
label can bring the first one, and Pandoc (3.11) leaves the property off
even for its own, from a heading with maths in it.
"""

import posixpath
import zipfile
from copy import deepcopy
from urllib.parse import unquote

from lxml import etree

from .epub_nav import (
    NAV_SEPARATOR,
    OPF_NS,
    OPS_NS,
    XHTML_NS,
    _nav_path,
    _normalize,
    _package_path,
    _text,
    heading_translation,
    label_runs,
    labelled,
    translation_text,
)

NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
NCX_MEDIA_TYPE = "application/x-dtbncx+xml"


def relabel_navigation(source, target, *, stage):
    """Write `source` to `target` with bilingual contents. Returns the count.

    The count is the number of contents entries that gained a translation.
    """
    with zipfile.ZipFile(source) as archive:
        package = _package_path(archive, stage)
        nav_path = _nav_path(archive, package, stage)
        chapters = _Chapters(archive)
        nav, relabelled = _relabel_nav(archive.read(nav_path), nav_path, chapters)
        replaced = {nav_path: nav}
        ncx_path = _ncx_path(archive, package)
        if ncx_path is not None:
            replaced[ncx_path] = _relabel_ncx(
                archive.read(ncx_path), ncx_path, chapters
            )
        if b"<math" in nav:
            opf = _declare_mathml(archive.read(package), package, nav_path)
            if opf is not None:
                replaced[package] = opf
        with zipfile.ZipFile(target, "w") as written:
            # `mimetype` stays first and stored: the infolist keeps Pandoc's
            # order and each entry's own compression.
            for info in archive.infolist():
                data = replaced.get(info.filename)
                written.writestr(info, archive.read(info) if data is None else data)
    return relabelled


class _Chapters:
    """Content documents parsed once each, by member name."""

    def __init__(self, archive):
        self.archive = archive
        self.parsed = {}

    def translation(self, base, href):
        """The translation div after the heading `href` names, or None."""
        path, _, fragment = unquote(href).partition("#")
        if not fragment:
            return None
        member = _normalize(posixpath.join(base, path))
        if member not in self.parsed:
            try:
                data = self.archive.read(member)
            except KeyError:
                return None
            self.parsed[member] = etree.fromstring(data)
        return heading_translation(self.parsed[member], fragment)


def _relabel_nav(data, nav_path, chapters):
    root = etree.fromstring(data)
    base = posixpath.dirname(nav_path)
    count = 0
    for nav in root.iter(f"{{{XHTML_NS}}}nav"):
        if "toc" not in (nav.get(f"{{{OPS_NS}}}type") or "").split():
            continue
        for anchor in list(nav.iter(f"{{{XHTML_NS}}}a")):
            division = chapters.translation(base, anchor.get("href") or "")
            if division is None or not labelled(
                _text(anchor), translation_text(division)
            ):
                continue
            _append(anchor, NAV_SEPARATOR, _inline(division))
            count += 1
        break
    return _serialize(root), count


def _relabel_ncx(data, ncx_path, chapters):
    root = etree.fromstring(data)
    base = posixpath.dirname(ncx_path)
    for point in root.iter(f"{{{NCX_NS}}}navPoint"):
        content = point.find(f"{{{NCX_NS}}}content")
        label = point.find(f"{{{NCX_NS}}}navLabel/{{{NCX_NS}}}text")
        if content is None or label is None:
            continue
        division = chapters.translation(base, content.get("src") or "")
        if division is None:
            continue
        translation = translation_text(division)
        if labelled(label.text or "", translation):
            label.text = (label.text or "").strip() + NAV_SEPARATOR + translation
    return _serialize(root)


def _inline(division):
    """The div's content as inline nodes: `[text, element, ...]`.

    One run per block (`label_runs`), space-joined, so a quotation or a list
    in the translation keeps its words and its MathML but not its box.
    """
    holder = etree.Element(f"{{{XHTML_NS}}}span")
    for position, run in enumerate(label_runs(division)):
        pieces = [" "] if position else []
        pieces += [p if isinstance(p, str) else deepcopy(p) for p in run]
        for piece in pieces:
            if isinstance(piece, str):
                _append(holder, piece, [])
            else:
                piece.tail = None
                holder.append(piece)
    etree.strip_tags(holder, f"{{{XHTML_NS}}}a")
    for image in list(holder.iter(f"{{{XHTML_NS}}}img")):
        _replace_with_text(image, image.get("alt") or "")
    for element in holder.iter():
        if isinstance(element.tag, str):
            element.attrib.pop("id", None)
    etree.strip_elements(holder, etree.Comment, with_tail=False)
    holder.text = (holder.text or "").lstrip()
    if len(holder):
        holder[-1].tail = (holder[-1].tail or "").rstrip()
    else:
        holder.text = holder.text.rstrip()
    return [holder.text or ""] + list(holder)


def _append(element, text, nodes):
    """Append `text` and then `nodes` (a `_inline` list or elements) to `element`."""
    pieces = list(nodes)
    if pieces and isinstance(pieces[0], str):
        text += pieces.pop(0)
    if len(element):
        element[-1].tail = (element[-1].tail or "") + text
    else:
        element.text = (element.text or "") + text
    for node in pieces:
        element.append(node)


def _replace_with_text(element, text):
    parent = element.getparent()
    joined = text + (element.tail or "")
    previous = element.getprevious()
    if previous is not None:
        previous.tail = (previous.tail or "") + joined
    else:
        parent.text = (parent.text or "") + joined
    parent.remove(element)


def _ncx_path(archive, package):
    root = etree.fromstring(archive.read(package))
    base = posixpath.dirname(package)
    for item in root.iter(f"{{{OPF_NS}}}item"):
        if item.get("media-type") == NCX_MEDIA_TYPE and item.get("href"):
            member = _normalize(posixpath.join(base, unquote(item.get("href"))))
            if member in archive.namelist():
                return member
    return None


def _declare_mathml(data, package, nav_path):
    """The package with `mathml` on the nav item, or None if it has it."""
    root = etree.fromstring(data)
    base = posixpath.dirname(package)
    for item in root.iter(f"{{{OPF_NS}}}item"):
        href = item.get("href") or ""
        if _normalize(posixpath.join(base, unquote(href))) != nav_path:
            continue
        properties = (item.get("properties") or "").split()
        if "mathml" in properties:
            return None
        item.set("properties", " ".join(properties + ["mathml"]))
        return _serialize(root)
    return None


def _serialize(root):
    return etree.tostring(
        root.getroottree(),
        xml_declaration=True,
        encoding="UTF-8",
        doctype=root.getroottree().docinfo.doctype or None,
    )
