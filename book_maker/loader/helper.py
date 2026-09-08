import inspect
import posixpath
import re
import zipfile

from rich import print
from dataclasses import dataclass
import logging
import uuid
from copy import copy

from bs4.element import Tag
from ebooklib import epub
from lxml import etree
from tenacity import retry, retry_if_exception, wait_exponential

from book_maker.translator.base_translator import BatchMismatch
from book_maker.utils import language_code

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# The longest single wait between attempts, in seconds. Waits grow
# exponentially up to this and then stay there: five minutes is long enough
# to sit out a rate limit and short enough that a provider coming back is
# noticed within one.
RETRY_WAIT_CAP = 300

# Errors that will not clear by waiting, matched by class name so this module
# imports no provider SDK: a rejected key, a request the endpoint will refuse
# in exactly the same words next time, a model that does not exist. Every
# other failure — 429, timeout, dropped connection, 5xx, a bare
# RequestException — is weather, and is waited out.
FATAL_ERROR_NAMES = frozenset(
    {
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "InvalidRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
        "ModelUnavailable",
    }
)


# Failures that are ours, not the provider's, and that the next attempt would
# reproduce exactly. A `TypeError` here is this process calling `translate`
# with an argument list the route does not have — patient retrying of that is
# an infinite loop, not tolerance. Deliberately narrow: `ValueError` is *not*
# in here, because reply parsing raises those on an answer the next attempt
# may well get right. KeyboardInterrupt and SystemExit are never swallowed —
# Ctrl-C during an hours-long wait has to stop the run.
FATAL_ERROR_TYPES = (TypeError, KeyboardInterrupt, SystemExit)


def _accepts_context(translate):
    """Whether a route's `translate` takes anything besides the text.

    `inspect.signature` of the bound method, so `self` is already out of the
    count. A signature that cannot be read — a C callable, a test double, a
    route with no `translate` at all — is assumed to take it, which is what
    every LLM route does and what this call did unconditionally before.
    """
    try:
        parameters = list(inspect.signature(translate).parameters.values())
    except (TypeError, ValueError):
        return True
    if any(p.kind is p.VAR_POSITIONAL for p in parameters):
        return True
    positional = [
        p for p in parameters if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) > 1


def _is_retryable(error):
    """Whether waiting could plausibly change the answer."""
    if isinstance(error, FATAL_ERROR_TYPES):
        return False
    return type(error).__name__ not in FATAL_ERROR_NAMES


def _say_retrying(retry_state):
    """One line per retry, so a long tolerance does not look like a hang."""
    error = retry_state.outcome.exception()
    logger.warning(
        "retrying after %s (%s) — attempt %d, waiting %.0fs",
        type(error).__name__,
        error,
        retry_state.attempt_number,
        retry_state.next_action.sleep,
    )


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


CONTAINER_PATH = "META-INF/container.xml"
CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
OPF_NS = "{http://www.idpf.org/2007/opf}"


DC_NS = "{http://purl.org/dc/elements/1.1/}"


@dataclass(frozen=True)
class PackageInfo:
    """What the source's `<package>` element says, read from the zip.

    ebooklib's reader drops both of these, each for its own reason, and
    both matter:

    - `prefixes`: EPUB 3 resolves a `property` like `tdm:reservation`
      through the package's `prefix` attribute. A translated book that
      copies the metas without it declares nothing they refer to and the
      property is undefined (epubcheck OPF-028).
    - `unique_identifier`: the value of the `dc:identifier` the package
      *names* as the book's identity. ebooklib's `book.uid` is the last
      identified `dc:identifier` it happened to read instead (see
      `derive_translation_identity`), which is a different string on any
      book carrying an ISBN after its UUID — and the font-obfuscation key
      is derived from this one, so the difference decides whether an
      embedded font comes out readable.

    `opf_dir` is where the package document lives, the coordinate system
    every manifest href is relative to.
    """

    prefixes: dict
    unique_identifier: str | None
    opf_dir: str


EMPTY_PACKAGE = PackageInfo(prefixes={}, unique_identifier=None, opf_dir="")


def read_package(epub_path):
    """Parse the source's package document straight out of the zip.

    Anything unreadable — not a zip, no container, no package — comes back
    as `EMPTY_PACKAGE` rather than raising: every caller has a defined
    answer for "the book does not say", and the reader that opens the book
    next reports a broken file far better than this would.
    """
    try:
        with zipfile.ZipFile(epub_path) as archive:
            container = etree.fromstring(archive.read(CONTAINER_PATH))
            rootfile = container.find(f".//{CONTAINER_NS}rootfile")
            full_path = rootfile.get("full-path") if rootfile is not None else None
            if not full_path:
                return EMPTY_PACKAGE
            package = etree.fromstring(archive.read(full_path))
    except Exception:
        return EMPTY_PACKAGE

    # The attribute is a flat whitespace-separated list of `name: uri`
    # pairs. A trailing name with no URI is not a mapping and is dropped
    # rather than guessed at.
    tokens = (package.get("prefix") or "").split()
    prefixes = {}
    for index in range(0, len(tokens) - 1, 2):
        name = tokens[index]
        if name.endswith(":"):
            prefixes[name[:-1]] = tokens[index + 1]

    named = package.get("unique-identifier")
    unique_identifier = None
    for element in package.iter(f"{DC_NS}identifier"):
        if named and element.get("id") == named:
            unique_identifier = (element.text or "").strip()
            break

    return PackageInfo(
        prefixes=prefixes,
        unique_identifier=unique_identifier or None,
        opf_dir=posixpath.dirname(full_path),
    )


def package_prefixes(epub_path):
    """The `prefix` declarations on the source's `<package>`, as name -> URI."""
    return read_package(epub_path).prefixes


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


def append_inline_translation(element, text, translation_style="", language=None):
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
    stamp_translation(span, element, language)
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


LANG_ATTRS = ("xml:lang", "lang")


def language_tag(language):
    """The tag `lang=` may carry for a --language value, or None.

    The CLI hands loaders the prompt wording — "simplified chinese" — which
    is what the model is asked for, not a language tag. Kept here under the
    name every caller already uses; the rule itself lives beside the tables
    it reads, in `book_maker.utils`, because `--language TAG:NAME` has to
    answer the same question before any loader exists.
    """
    return language_code(language)


def stamp_translation(node, source, language):
    """A translation node the loader made declares its language as its source did.

    A bare <span> inside `<p lang="de">` reads as German. Only the attributes
    the source element itself carries are set, the rule `restamp_language`
    follows for clones: a book that declares no languages gets no new ones.
    """
    if not language or not isinstance(node, Tag) or not isinstance(source, Tag):
        return node
    for attr in LANG_ATTRS:
        if attr in source.attrs:
            node[attr] = language
    return node


def restamp_language(element, language):
    """A translated copy is in the target language, whatever its source said.

    Only attributes the original carried are touched: a sibling that never
    declared a language keeps not declaring one.
    """
    if not language or not isinstance(element, Tag):
        return element
    for attr in LANG_ATTRS:
        if attr in element.attrs:
            element[attr] = language
    return element


class EPUBBookLoaderHelper:
    def __init__(
        self,
        translate_model,
        accumulated_num,
        translation_style,
        context_flag,
        language=None,
    ):
        self.translate_model = translate_model
        self.accumulated_num = accumulated_num
        self.translation_style = translation_style
        self.context_flag = context_flag
        # Asked once, here, because the answer is a property of the route and
        # cannot change during a run — and because getting it wrong is not
        # survivable: this call used to hand a second positional to every
        # route, and the fixed MT engines (`DeepLFree.translate(self, text)`
        # and the rest) answered TypeError on the first paragraph. A
        # deterministic TypeError under a retry that never gives up is an
        # infinite loop, so `_is_retryable` refuses that class too.
        # `getattr`, because a route (or a test double) that only ever goes
        # through `translate_list` need not have a `translate` at all, and
        # asking about one that is not there must not fail construction.
        self.translate_takes_context = _accepts_context(
            getattr(translate_model, "translate", None)
        )
        # The loader hands over the tag it settled on; run through the same
        # rule anyway, so a caller that still passes prompt wording gets what
        # `lang=` accepts rather than a value a validator rejects.
        self.language = language_tag(language)

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
            return append_inline_translation(p, text, translation_style, self.language)
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
        restamp_language(new_p, self.language)
        p.insert_after(new_p)
        if single_translate:
            p.extract()
        # the node the translation was written into, for callers that have to
        # find their way back to it (marker restore); None when nothing was
        # written, which the early returns above cover
        return new_p

    # Owner ruling (260907): this retry is **patient**, and gives up only on
    # a fatal error. The user base runs flaky providers where a 429 or a
    # first token can be minutes or hours away, and an attempt cap there
    # abandons a paid book over weather. So: no attempt cap, no total-time
    # cap, waits that grow to `RETRY_WAIT_CAP` and stay there.
    #
    # What the old `backoff.on_exception(backoff.expo, Exception)` got wrong
    # was not the patience — it was retrying the errors that will never
    # clear. A rejected key or a model that does not exist retried forever
    # with doubling waits, and the run neither finished nor stopped. Those
    # are `FATAL_ERROR_NAMES` now and propagate on the first one.
    #
    # Loud on purpose: an hours-long tolerance that says nothing looks
    # exactly like a hang. (This comment is the in-tree record of the
    # ruling — worktrees do not carry the repo's CLAUDE.md.)
    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=RETRY_WAIT_CAP),
        before_sleep=_say_retrying,
        # No `stop`: tenacity's default is `stop_never`, which is the point.
        # `reraise` so a fatal error reaches the caller as itself, and so the
        # behaviour stays right if a stop is ever added above.
        reraise=True,
    )
    def translate_with_backoff(self, text, context_flag=False):
        if self.translate_takes_context:
            return self.translate_model.translate(text, context_flag)
        return self.translate_model.translate(text)

    def deal_new(self, p, wait_p_list, single_translate=False):
        self.deal_old(wait_p_list, single_translate, self.context_flag)
        self.insert_trans(
            p,
            shorter_result_link(self.translate_with_backoff(p.text, self.context_flag)),
            self.translation_style,
            single_translate,
        )

    def deal_old(self, wait_p_list, single_translate=False, context_flag=False):
        flush_waiting(
            self.translate_model,
            wait_p_list,
            self.insert_trans,
            self.translation_style,
            single_translate,
        )


def flush_waiting(model, wait_p_list, insert, translation_style, single_translate):
    """Translate the accumulated paragraphs in one request, then insert each.

    `insert` is what differs between the two callers — the helper's
    `insert_trans` and the loader's `_insert_trans_preserving_tags` — and it
    is the only thing that ever did. A reply shorter than the batch leaves
    the tail untouched; the list is emptied either way, so the caller's
    accumulator does not carry paragraphs it has already asked about.
    """
    if not wait_p_list:
        return

    result_txt_list = translate_list_or_singles(model, [p.text for p in wait_p_list])

    for i in range(len(wait_p_list)):
        if i < len(result_txt_list):
            insert(
                wait_p_list[i],
                shorter_result_link(result_txt_list[i]),
                translation_style,
                single_translate,
            )

    wait_p_list.clear()


url_pattern = r"(http[s]?://|www\.)+(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"


# Compiled once at import. `is_pure_url` is called on every translatable
# segment a partition produces — six figures on the corpus's worst book —
# and re.compile per call pays a cache lookup for a constant pattern.
_URL_RE = re.compile(url_pattern)
_URL_TAIL_RE = re.compile(r".*" + url_pattern + r"$")


def translate_list_or_singles(model, texts):
    """`model.translate_list(texts)`, with tag mode's own alignment fallback.

    Plan mode answers a `BatchMismatch` with `_translate_texts_aligned`: it
    halves the chunk and asks again, ~2x the batch instead of N singles. Tag
    mode has no such ladder — `--accumulated_num > 1` calls `translate_list`
    straight from `deal_old` — so the exception would escape and end a run
    that used to repair itself. One local sweep of `translate()` calls is
    what that repair was; it stays here, where the ladder cannot reach.
    """
    if not hasattr(model, "translate_list"):
        return [model.translate(text) for text in texts]
    try:
        return model.translate_list(texts)
    except BatchMismatch as e:
        print(
            f"[yellow]batch of {len(texts)} came back misaligned ({e}); "
            f"translating one by one[/yellow]"
        )
        return [model.translate(text) for text in texts]


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
    return all(c.isdigit() or c.isspace() for c in s)


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
