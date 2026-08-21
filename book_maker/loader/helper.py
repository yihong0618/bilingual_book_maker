import posixpath
import re
import backoff
import logging
import uuid
from copy import copy

from bs4.element import Tag
from ebooklib import epub
from lxml import etree

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Elements whose parent accepts exactly one of them, and containers with a
# content model too strict for an appended sibling. A translated copy next
# to one of these is a book epubcheck rejects: <figure> takes one
# <figcaption>, and an EPUB 3 navigation document takes one heading before
# its <ol> and nothing but <a>/<span> inside an <li>.
SINGLETON_TAGS = frozenset(["figcaption", "caption", "legend", "summary"])

# Void elements: HTML5 spells them without a closing tag.
VOID_TAGS = frozenset(
    ["area", "br", "col", "embed", "hr", "img", "input", "source", "track", "wbr"]
)


def derive_translation_identity(new_book, source_book, *facets):
    """Give the translated book its own identifier — deterministic, and
    deliberately not the source's.

    The translation is a different book, so sharing the source identifier
    would make a library deduplicate one against the other. But ebooklib's
    default — a fresh uuid on every run — is wrong the other way:
    regenerate the same translation and every reader sees a brand-new
    book, duplicating library entries and orphaning reading positions. A
    UUIDv5 of the source identifier plus the facets that define this
    translation (target language, bilingual vs single) is stable across
    runs, distinct from the source, and distinct between facet
    combinations.

    The seed only has to be stable for the same source file, not
    semantically primary: ebooklib's reader lets the *last* identified
    `<dc:identifier>` win `uid`, so adopting it as the book's identity
    would corrupt `unique-identifier` on multi-identifier sources —
    deriving from it cannot.

    Returns the `id` attribute now spoken for, so the metadata copy can
    drop the colliding attribute from the source's own entry; None when
    the source has no identifier — there is nothing stable to derive
    from, so ebooklib's per-run uuid stands.
    """
    uid = getattr(source_book, "uid", None)
    if not uid:
        return None
    seed = "|".join(["bbook-maker", str(uid), *[str(f) for f in facets]])
    new_book.set_identifier(str(uuid.uuid5(uuid.NAMESPACE_URL, seed)))
    return new_book.IDENTIFIER_ID


def rebase_ncx_srcs(ncx_bytes, ncx_path):
    """Make a regenerated NCX's links resolve from where the NCX actually is.

    ebooklib's `_get_ncx()` writes navpoint srcs as item file_names — paths
    relative to the OPF root, which is the only place its own `EpubNcx()`
    ever lives. A book that keeps its NCX in a subdirectory (kusamakura:
    `xhtml/toc.ncx`) gets every src doubled on resolution
    (`xhtml/xhtml/…` — epubcheck RSC-007, a dead EPUB 2 table of contents),
    because the writer keeps the imported location but not its coordinate
    system. Step each src out of that directory instead. Root-located NCX
    bytes pass through untouched.
    """
    base = posixpath.dirname(ncx_path)
    if not base:
        return ncx_bytes
    tree = etree.fromstring(ncx_bytes)
    for el in tree.iter("{http://www.daisy.org/z3986/2005/ncx/}content"):
        src = el.get("src") or ""
        path, sep, frag = src.partition("#")
        if path and "://" not in path and not path.startswith("/"):
            el.set("src", posixpath.relpath(path, base) + sep + frag)
    return etree.tostring(tree, xml_declaration=True, encoding="utf-8")


def backfill_toc_hrefs(toc):
    """Give every `Section` an href, using its first descendant that has one.

    ebooklib's NCX writer says "CAN NOT HAVE EMPTY SRC HERE" and then writes
    `<content src=""/>` anyway for an hrefless `Section` — which is what a
    nav `<li>` labelled by a `<span>` rather than an `<a>` becomes
    (RSC-010): a table-of-contents entry that navigates nowhere on readers
    using the NCX. An NCX `content` must point somewhere, and the only
    honest target for a grouping entry is where the group starts.
    """

    def first_href(node):
        if isinstance(node, (tuple, list)):
            for child in node:
                found = first_href(child)
                if found:
                    return found
            return None
        if isinstance(node, epub.EpubHtml):
            return node.file_name
        return getattr(node, "href", None) or None

    for item in toc:
        if not isinstance(item, (tuple, list)):
            continue
        section, children = item[0], item[1]
        backfill_toc_hrefs(children)
        if isinstance(section, epub.Section) and not section.href:
            section.href = first_href(children) or ""

    return toc


def make_tag(name, **attrs):
    """A hand-built tag bs4 will serialize the way the spec spells it.

    bs4 learns which tags are void from a parser builder, and a tag built by
    hand has no builder: `Tag(name="br")` comes out as the *pair*
    `<br></br>`. XML accepts that and so does epubcheck, but an HTML5 parser
    reads the closing tag as a second `<br>`, so a reading system in
    compatibility mode shows a double line break.

    `soup.new_tag()` would ask the builder — but there is no soup to ask
    from here: `element.soup` is `None` on parsed nodes (bs4 4.14), so an
    element cannot hand us the tree it belongs to. Naming the void elements
    is the honest way to get the same answer.
    """
    return Tag(name=name, can_be_empty_element=name in VOID_TAGS, attrs=attrs or {})


def has_restricted_content_model(element):
    """Would a translated sibling of this element be invalid markup?"""
    if element.name in SINGLETON_TAGS:
        return True
    return element.find_parent("nav") is not None


def translation_host(element):
    """Which element a translation may be appended to.

    Usually the element itself. An EPUB 3 navigation <li> is the exception:
    its entire content model is "(a | span), ol?", so a translation
    appended to the <li> is exactly as invalid as one placed beside it —
    epubcheck rejects both with "element span not allowed here". The text
    an entry shows lives in its <a>/<span>, and so does its translation.
    """
    if element.name == "li" and element.find_parent("nav") is not None:
        label = element.find(["a", "span"], recursive=False)
        if label is not None:
            return label
    return element


def append_inline_translation(element, text, translation_style=""):
    """Put the translation *inside* the element it belongs to.

    Some containers accept exactly one of a thing: an EPUB 3 navigation
    document allows one heading before its <ol> and nothing but <a>/<span>
    inside an <li>; a <figure> allows one <figcaption>. Appending a
    translated sibling there produces a book epubcheck rejects, so the
    translation joins the element's own content instead, on its own line —
    a table-of-contents entry reads "Chapter 1" over "第一章" and stays
    valid. A <br/> rather than a space, because running two languages
    together on one line is exactly the crowding the bilingual sibling
    layout avoids everywhere else.
    """
    span = make_tag("span")
    if translation_style:
        span["style"] = translation_style
    span.string = text
    host = translation_host(element)
    # can_be_empty_element makes bs4 serialize the void form <br/>; a bare
    # Tag("br") comes out as the pair <br></br>, which an HTML5 parser
    # reads as two line breaks.
    host.append(Tag(name="br", can_be_empty_element=True))
    host.append(span)
    return span


def strip_duplicate_ids(element):
    """Remove every id from a cloned element and its descendants.

    A translated copy is a second rendering of the same content, not a
    second anchor for it. Leaving the ids in produces a document where two
    elements answer to one fragment identifier — epubcheck RSC-005, and an
    internal cross-reference that may land on the translation instead of
    the passage it cites.
    """
    if isinstance(element, Tag):
        element.attrs.pop("id", None)
        for descendant in element.descendants:
            if isinstance(descendant, Tag):
                descendant.attrs.pop("id", None)
    return element


class EPUBBookLoaderHelper:
    def __init__(
        self, translate_model, accumulated_num, translation_style, context_flag
    ):
        self.translate_model = translate_model
        self.accumulated_num = accumulated_num
        self.translation_style = translation_style
        self.context_flag = context_flag

    def insert_trans(self, p, text, translation_style="", single_translate=False):
        if text is None:
            text = ""
        if (
            p.string is not None
            and p.string.replace(" ", "").strip() == text.replace(" ", "").strip()
        ):
            return
        if not single_translate and has_restricted_content_model(p):
            # single-translate extracts the original, so it never creates
            # the second sibling this rule exists to prevent
            append_inline_translation(p, text, translation_style)
            return
        new_p = copy(p)
        new_p.string = text
        if translation_style != "":
            new_p["style"] = translation_style
        if not single_translate:
            # The copy would otherwise carry the original's id and every id
            # inside it, so the document ends up with two elements answering
            # to the same anchor: epubcheck rejects it (RSC-005), and an
            # internal link may land on the translation instead of the text
            # it points at. When the original is extracted there is no
            # duplicate, so single-translate keeps its ids.
            strip_duplicate_ids(new_p)
        p.insert_after(new_p)
        if single_translate:
            p.extract()

    @backoff.on_exception(
        backoff.expo,
        Exception,
        on_backoff=lambda details: logger.warning(f"retry backoff: {details}"),
        on_giveup=lambda details: logger.warning(f"retry abort: {details}"),
        jitter=None,
    )
    def translate_with_backoff(self, text, context_flag=False):
        return self.translate_model.translate(text, context_flag)

    def deal_new(self, p, wait_p_list, single_translate=False):
        self.deal_old(wait_p_list, single_translate, self.context_flag)
        self.insert_trans(
            p,
            shorter_result_link(self.translate_with_backoff(p.text, self.context_flag)),
            self.translation_style,
            single_translate,
        )

    def deal_old(self, wait_p_list, single_translate=False, context_flag=False):
        if not wait_p_list:
            return

        result_txt_list = self.translate_model.translate_list(
            [p.text for p in wait_p_list]
        )

        for i in range(len(wait_p_list)):
            if i < len(result_txt_list):
                p = wait_p_list[i]
                self.insert_trans(
                    p,
                    shorter_result_link(result_txt_list[i]),
                    self.translation_style,
                    single_translate,
                )

        wait_p_list.clear()


url_pattern = r"(http[s]?://|www\.)+(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"


# Compiled once at import. `is_pure_url` is called on every translatable
# segment a partition produces — six figures on the corpus's worst book —
# and re.compile per call pays a cache lookup for a constant pattern.
_URL_RE = re.compile(url_pattern)
_URL_TAIL_RE = re.compile(r".*" + url_pattern + r"$")


def is_text_link(text):
    return bool(_URL_RE.match(text.strip()))


def is_pure_url(text):
    """The text is a URL and nothing else.

    `is_text_link` prefix-matches, which tag mode can afford. A partition
    cannot: ``https://example.org — see Appendix A`` starts with a URL, and
    prefix-matching threw away the prose after it. Anchored at both ends,
    a URL only skips when it is the whole of what is being judged.
    """
    return bool(_URL_RE.fullmatch(text.strip()))


def is_text_tail_link(text, num=80):
    text = text.strip()
    return bool(_URL_TAIL_RE.match(text)) and len(text) < num


def shorter_result_link(text, num=20):
    match = re.search(url_pattern, text)

    if not match or len(match.group()) < num:
        return text

    return re.compile(url_pattern).sub("...", text)


def is_text_source(text):
    return text.strip().startswith("Source: ")


def is_text_list(text, num=80):
    text = text.strip()
    return re.match(r"^Listing\s*\d+", text) and len(text) < num


def is_text_figure(text, num=80):
    text = text.strip()
    return re.match(r"^Figure\s*\d+", text) and len(text) < num


def is_text_digit_and_space(s):
    for c in s:
        if not c.isdigit() and not c.isspace():
            return False
    return True


def is_text_isbn(s):
    pattern = r"^[Ee]?ISBN\s*\d[\d\s]*$"
    return bool(re.match(pattern, s))


def not_trans(s):
    return any(
        [
            is_text_link(s),
            is_text_tail_link(s),
            is_text_source(s),
            is_text_list(s),
            is_text_figure(s),
            is_text_digit_and_space(s),
            is_text_isbn(s),
        ]
    )
