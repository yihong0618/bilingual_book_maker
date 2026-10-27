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

The contents are bilingual: an entry whose heading has a translation reads
`original / translation` (owner 261026). Pandoc names an entry after its
heading only and has no attribute for a label of its own, so the entries
are rewritten after it (`epub_bilingual_nav`), from the translation Pandoc
rendered right after the heading. `heading_translation` is that lookup, and
the check that follows the rewrite asks the same question: an entry whose
heading has a distinct translation must carry it, and every other entry
must read as its heading.
"""

import posixpath
import xml.etree.ElementTree as ElementTree
from urllib.parse import unquote, urlparse

from .errors import PipelineError
from .messages import NAV_INVALID

XHTML_NS = "http://www.w3.org/1999/xhtml"
OPS_NS = "http://www.idpf.org/2007/ops"
MATHML_NS = "http://www.w3.org/1998/Math/MathML"
CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"

CONTAINER = "META-INF/container.xml"

# What the reading edition wraps a translation in (`reading_edition`), and
# what joins a heading to its translation in the contents.
TRANSLATION_CLASS = "bbm-translation"
NAV_SEPARATOR = " / "
HEADING_TAGS = {f"h{level}" for level in range(1, 7)}


def refuse(detail, stage):
    raise PipelineError(NAV_INVALID + detail, stage=stage)


def validate_navigation(archive, outline, *, stage, bilingual=False):
    """Check the navigation of an open EPUB. Returns the entries it found.

    `outline` is `[(level, text, identifier), ...]` in document order. A
    document with no headings of its own still has to navigate: Pandoc gives
    it one book-level entry, and that entry is required to work like any
    other. `bilingual` checks the contents after `epub_bilingual_nav` has
    written each heading's translation into its entry.
    """
    package = _package_path(archive, stage)
    nav_path = _nav_path(archive, package, stage)
    entries = _toc_entries(archive, nav_path, stage)
    if not entries:
        refuse(f"the table of contents in {nav_path} has no entries", stage)
    _resolve_entries(archive, nav_path, entries, stage)
    translations = (
        [_entry_translation(archive, nav_path, href, stage) for _, _, href in entries]
        if bilingual
        else None
    )
    _compare_to_outline(entries, outline, nav_path, stage, translations)
    return entries


# -- a heading's translation ---------------------------------------------
def heading_translation(root, fragment):
    """The translation rendered right after the heading `#fragment` names.

    Pandoc puts a heading's id on the `<section>` it opens, the heading
    being that section's first element; a heading without a section carries
    the id itself. The translation is the element immediately after the
    heading, and only if it is a `bbm-translation` div: one further down
    belongs to something else. Works on ElementTree and lxml trees alike
    (lxml keeps comments as children; they are not elements here). Returns
    the div, or None.
    """
    target = None
    for element in root.iter():
        if isinstance(element.tag, str) and element.get("id") == fragment:
            target = element
            break
    if target is None:
        return None
    heading = target
    if _local(target.tag) not in HEADING_TAGS:
        children = _element_children(target)
        if not children or _local(children[0].tag) not in HEADING_TAGS:
            return None
        heading = children[0]
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        children = _element_children(parent)
        for position, child in enumerate(children):
            if child is not heading:
                continue
            following = children[position + 1 : position + 2]
            if (
                following
                and _local(following[0].tag) == "div"
                and TRANSLATION_CLASS in (following[0].get("class") or "").split()
            ):
                return following[0]
            return None
    return None


def translation_text(division):
    """A translation div as one line of text: its paragraphs, space-joined."""
    return _normal(" ".join(_text(child) for child in _element_children(division)))


def labelled(original, translation):
    """Whether `translation` adds anything to the entry `original`."""
    translation = _normal(translation)
    return bool(translation) and translation != _normal(original)


def _entry_translation(archive, nav_path, href, stage):
    path, _, fragment = unquote(href).partition("#")
    if not fragment:
        return ""
    target = _normalize(posixpath.join(posixpath.dirname(nav_path), path))
    division = heading_translation(_parse(archive, target, stage), fragment)
    return "" if division is None else translation_text(division)


def _element_children(element):
    return [child for child in element if isinstance(child.tag, str)]


def _local(tag):
    return tag.rpartition("}")[2] if isinstance(tag, str) else ""


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
def _compare_to_outline(entries, outline, nav_path, stage, translations=None):
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
        if translations is not None and labelled(
            wanted_text, translations[position - 1]
        ):
            wanted_text = (
                _normal(wanted_text) + NAV_SEPARATOR + translations[position - 1]
            )
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
    return _normal("".join(_pieces(element)))


def _pieces(element):
    # A formula reads as its TeX, which is what the outline holds for it:
    # Pandoc's MathML carries the glyphs and the TeX annotation both.
    if element.tag == f"{{{MATHML_NS}}}math":
        tex = element.find(
            f".//{{{MATHML_NS}}}annotation[@encoding='application/x-tex']"
        )
        yield "".join(element.itertext()) if tex is None else (tex.text or "")
        return
    yield element.text or ""
    for child in element:
        if isinstance(child.tag, str):  # an lxml comment is not text
            yield from _pieces(child)
        yield child.tail or ""


def _normal(text):
    return " ".join((text or "").split())


def _normalize(path):
    """A package member name, with `./` and `a/../b` resolved away.

    Never made absolute and never allowed to climb: a `../` that leaves the
    container simply names nothing in the archive, and is refused by the
    caller that looked it up.
    """
    return posixpath.normpath(path)
