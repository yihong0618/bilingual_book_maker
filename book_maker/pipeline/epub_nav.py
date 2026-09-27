"""The EPUB3 navigation document, checked against the outline it came from.

Pandoc builds the navigation; this module is the reason a book is allowed to
be published with it. A reflowable EPUB whose table of contents is missing,
truncated or pointing at nothing is not a reading edition, and every one of
those has been a silent success elsewhere: `--toc-depth` quietly drops the
deeper headings, a renamed content document leaves the links dangling, and a
reader that cannot find a nav simply shows no contents at all.

Nothing here parses Markdown. The outline it compares against is the one
Pandoc's own AST reported for the same document (`preflight.Report.outline`),
so the two sides of the comparison are the same reader's answer, and the
only thing being tested is whether the writer carried it through.

The route is the one a reading system takes, not a guess at file names:

    META-INF/container.xml -> the package document
    the package's manifest -> the item whose properties include `nav`
    that document            -> its `nav` element with `epub:type="toc"`

`epub:type="landmarks"` is deliberately not followed. It carries an entry
*for* the table of contents (`<a href="#toc" epub:type="toc">`), and reading
it as part of the contents would count a pointer to the nav as a chapter.
"""

import posixpath
import xml.etree.ElementTree as ElementTree
from urllib.parse import unquote, urlparse

from .errors import PipelineError
from .messages import NAV_INVALID

XHTML_NS = "http://www.w3.org/1999/xhtml"
OPS_NS = "http://www.idpf.org/2007/ops"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"

CONTAINER = "META-INF/container.xml"


def refuse(detail, stage):
    raise PipelineError(NAV_INVALID + detail, stage=stage)


def validate_navigation(archive, outline, *, stage):
    """Check the navigation of an open EPUB. Returns the entries it found.

    `outline` is `[(level, text, identifier), ...]` in document order. A
    document with no headings of its own still has to navigate: Pandoc gives
    it one book-level entry, and that entry is required to work like any
    other.
    """
    package = _package_path(archive, stage)
    nav_path = _nav_path(archive, package, stage)
    entries = _toc_entries(archive, nav_path, stage)
    if not entries:
        refuse(f"the table of contents in {nav_path} has no entries", stage)
    _resolve_entries(archive, nav_path, entries, stage)
    _compare_to_outline(entries, outline, nav_path, stage)
    return entries


# -- the route to the nav document ---------------------------------------
def _read(archive, name, stage):
    try:
        return archive.read(name)
    except KeyError:
        refuse(f"{name} is not in the package", stage)


def _parse(archive, name, stage):
    data = _read(archive, name, stage)
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as err:
        refuse(f"{name} is not well-formed XML: {err}", stage)


def _package_path(archive, stage):
    """The package document named by the container, as a member name."""
    root = _parse(archive, CONTAINER, stage)
    for rootfile in root.iter(f"{{{CONTAINER_NS}}}rootfile"):
        full_path = (rootfile.get("full-path") or "").strip()
        if full_path:
            path = _normalize(unquote(full_path))
            if path not in archive.namelist():
                refuse(
                    f"{CONTAINER} names a package document that is not in the "
                    f"package: {full_path}",
                    stage,
                )
            return path
    refuse(f"{CONTAINER} names no package document", stage)


def _nav_path(archive, package, stage):
    """The manifest item declared with the `nav` property."""
    root = _parse(archive, package, stage)
    base = posixpath.dirname(package)
    for item in root.iter(f"{{{OPF_NS}}}item"):
        if "nav" not in (item.get("properties") or "").split():
            continue
        href = (item.get("href") or "").strip()
        if not href:
            refuse(f"{package} declares a nav item with no href", stage)
        path = _normalize(posixpath.join(base, unquote(href)))
        if path not in archive.namelist():
            refuse(
                f"{package} declares the nav document {href}, which is not in "
                f"the package",
                stage,
            )
        return path
    refuse(f"{package} declares no navigation document", stage)


def _toc_entries(archive, nav_path, stage):
    """`(depth, text, href)` for the entries under `epub:type="toc"`."""
    root = _parse(archive, nav_path, stage)
    toc = None
    for nav in root.iter(f"{{{XHTML_NS}}}nav"):
        if "toc" in (nav.get(f"{{{OPS_NS}}}type") or "").split():
            toc = nav
            break
    if toc is None:
        refuse(f'{nav_path} has no nav element with epub:type="toc"', stage)
    ordered = toc.find(f"{{{XHTML_NS}}}ol")
    if ordered is None:
        refuse(f"the table of contents in {nav_path} has no list", stage)
    entries = []
    _walk_list(ordered, 0, entries, nav_path, stage)
    return entries


def _walk_list(ordered, depth, entries, nav_path, stage):
    for item in ordered.findall(f"{{{XHTML_NS}}}li"):
        anchor = item.find(f"{{{XHTML_NS}}}a")
        if anchor is None:
            refuse(f"an entry at depth {depth} in {nav_path} is not a link", stage)
        text = _text(anchor)
        href = (anchor.get("href") or "").strip()
        if not text:
            refuse(f"an entry at depth {depth} in {nav_path} has no label", stage)
        if not href:
            refuse(f"the entry {text!r} in {nav_path} has no href", stage)
        entries.append((depth, text, href))
        for nested in item.findall(f"{{{XHTML_NS}}}ol"):
            _walk_list(nested, depth + 1, entries, nav_path, stage)


# -- every entry points at something that is there ------------------------
def _resolve_entries(archive, nav_path, entries, stage):
    names = set(archive.namelist())
    identifiers = {}
    base = posixpath.dirname(nav_path)
    for _, text, href in entries:
        if urlparse(href).scheme:
            refuse(f"the entry {text!r} points outside the package: {href}", stage)
        path, _, fragment = unquote(href).partition("#")
        target = _normalize(posixpath.join(base, path)) if path else nav_path
        if target not in names:
            refuse(
                f"the entry {text!r} points at {href}, which is not in the " f"package",
                stage,
            )
        if not fragment:
            continue
        if target not in identifiers:
            identifiers[target] = _identifiers(archive, target, stage)
        if fragment not in identifiers[target]:
            refuse(
                f"the entry {text!r} points at #{fragment} in {target}, which "
                f"has no such anchor",
                stage,
            )


def _identifiers(archive, name, stage):
    root = _parse(archive, name, stage)
    found = set()
    for element in root.iter():
        for attribute in ("id", "name"):
            value = element.get(attribute)
            if value:
                found.add(value)
    return found


# -- the contents are the document's own outline --------------------------
def _compare_to_outline(entries, outline, nav_path, stage):
    """The entries, against the headings the same document reported.

    A document with no headings has no outline to reproduce; Pandoc gives it
    a single book-level entry, which the structural checks above have
    already resolved. Anything else has to match heading for heading, in
    order, at the same nesting -- coverage, order and hierarchy in one
    comparison, because a truncated contents and a reordered one are the
    same kind of wrong book.
    """
    if not outline:
        return
    expected = nest(outline)
    if len(entries) != len(expected):
        refuse(
            f"the Markdown has {len(expected)} heading(s) and the table of "
            f"contents in {nav_path} has {len(entries)} entry/entries",
            stage,
        )
    for position, (entry, heading) in enumerate(zip(entries, expected), start=1):
        depth, text, href = entry
        wanted_depth, wanted_text, identifier = heading
        if _normal(text) != _normal(wanted_text):
            refuse(
                f"entry {position} reads {text!r} where the heading reads "
                f"{wanted_text!r}",
                stage,
            )
        if depth != wanted_depth:
            refuse(
                f"entry {position} ({text!r}) is nested {depth} deep where the "
                f"heading is {wanted_depth} deep",
                stage,
            )
        if identifier and unquote(href).partition("#")[2] != identifier:
            refuse(
                f"entry {position} ({text!r}) points at {href} rather than at "
                f"the heading #{identifier}",
                stage,
            )


def nest(outline):
    """`(level, text, id)` headings as `(depth, text, id)` nesting.

    H1..H6 is a hierarchy with holes in it: a document may go straight from
    an H1 to an H3, and the H3 belongs under the H1 rather than under a
    level that was never written. A heading is therefore nested under the
    nearest preceding heading shallower than itself, which is what Pandoc's
    own section nesting does and what the navigation has to show.
    """
    nested = []
    open_levels = []
    for level, text, identifier in outline:
        while open_levels and open_levels[-1] >= level:
            open_levels.pop()
        nested.append((len(open_levels), text, identifier))
        open_levels.append(level)
    return nested


def _text(element):
    return _normal("".join(element.itertext()))


def _normal(text):
    return " ".join((text or "").split())


def _normalize(path):
    """A package member name, with `./` and `a/../b` resolved away.

    Never made absolute and never allowed to climb: a `../` that leaves the
    container simply names nothing in the archive, and is refused by the
    caller that looked it up.
    """
    return posixpath.normpath(path)
