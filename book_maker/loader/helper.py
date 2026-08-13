import re
import backoff
import logging
from copy import copy

from bs4.element import Tag

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
    translation joins the element's own content instead — a table-of-
    contents entry reads "Chapter 1 第一章" and stays valid.
    """
    span = make_tag("span")
    if translation_style:
        span["style"] = translation_style
    span.string = f" {text}"
    translation_host(element).append(span)
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
