"""Say, in the file itself, that the file is a machine translation.

One line, and nothing else. Owner ruling, 260906: a distributed book must
not be traceable back to the operator, but a reader must still be able to
see what could have gone wrong with the translation. So the whole visible
apparatus is a single small paragraph appended below the book's own intro —

    Translated by gpt-5.6-luna, 2026.

— placed on the title page, found through the EPUB 2 guide or the EPUB 3
landmarks, and falling back to the first linear document for a book that
declares neither.

Nothing is written into the package metadata any more. The `trl` and `bkp`
contributor credits, the `dc:description` stamp and every `bbm:` meta are
gone: each of them was a claim about who built the file rather than about
what the translation is, and none of them is worth what it says about the
person who ran the tool. What survives of them is the *recognition* half —
`is_prior_disclosure`, `is_our_colophon`, `prior_glossary_shas` — because
books stamped by earlier builds are out in the world and a rerun still has
to strip what they left behind.

Beside the line, on a plan or session run or when `--translation-metadata`
asks, goes the machine half: `bbm_translation_metadata.json` as a manifest
item, and the user's glossary beside it. That record is
`book_maker.translation_metadata`'s to compose and this module's to put in
the package.

Two rules keep this from colliding with the book it is stamping:

- **Nothing is recognised by its id or file name.** A publisher's own
  `colophon.xhtml`, or an `id="colophon"` on a real chapter, must survive
  untouched. What this tool wrote is recognised only by a marker it put
  inside the document — a generator meta on an old colophon page, a class
  on the credit line — and a contributor only by its value plus its role
  refine.
- **Every id and file name is allocated against what is already there.**
  If the book already uses the name this would take, the stamp takes the
  next one instead — a duplicate id is a book no reading system opens.

And what a previous run of this tool left behind is *owned*, not respected:
it is stripped on copy and rewritten from this run's facts, so a book
translated by model A and then again by model B does not claim both.
"""

import json
import re
from datetime import date
from hashlib import sha256
from html import escape

from ebooklib import epub

from book_maker import translation_metadata as tmeta
from book_maker.redaction import redact

TOOL_NAME = "bilingual_book_maker"

# MARC relators an older build wrote: "trl" (translator) on the tool credit,
# "bkp" (book producer) on the one that carried the build. Neither is
# written any more. They are listed because ownership is still decided from
# them: an entry carrying this tool's name refined by either role is a
# previous run's stamp, to be dropped.
CONTRIBUTOR_ROLE = "trl"
PRODUCER_ROLE = "bkp"
CONTRIBUTOR_ROLES = (CONTRIBUTOR_ROLE, PRODUCER_ROLE)

# The description an older build wrote: a label, then the model and the year
# in parentheses, then a fixed tail. Kept for the same reason the roles are
# — so a rerun recognises and drops it. Label and tail are both matched: a
# publisher's "Machine translation (French edition)" opens the same way and
# is a statement about the book, not a stamp.
AI_LABEL = "AI translation"
ENGINE_LABEL = "Machine translation"
DESCRIPTION_LABELS = (AI_LABEL, ENGINE_LABEL)
DESCRIPTION_TAIL = ").\nOriginal text unaltered; translation quality not verified."
_TAIL_WORDS = " ".join(DESCRIPTION_TAIL.split())

# The whole visible apparatus. A class, because that is what makes the
# paragraph ours to remove on a rerun: an id would collide with the book's,
# and the text itself is translated by the next run before anything gets to
# read it. Kept out of the book's own styling — an inline style, small and
# muted, so it reads as a note about the file rather than as a line of it.
CREDIT_CLASS = "bbm-translation-credit"
CREDIT_STYLE = "font-size:0.75em;opacity:0.6;margin-top:2em;"
CREDIT_PREFIX = "Translated by "

# What the guide or the landmarks call the page the line goes on. Compared
# with everything but letters and digits removed, so `title-page`,
# `titlepage` and `TitlePage` are one answer.
TITLE_PAGE_TYPES = frozenset({"titlepage"})

# The fixed services have no model to name, so the line names the service
# the way a reader would: "Google Translate", not "google". Anything not
# listed falls back to the key it is registered under, which is at least
# honest.
ENGINE_NAMES = {
    "google": "Google Translate",
    "deepl": "DeepL",
    "deeplfree": "DeepL",
    "caiyun": "Caiyun",
    "tencent": "Tencent TranSmart",
}

# What an older build's closing page called itself, in its own head. Nothing
# writes such a page any more; this is how a rerun finds one and drops it.
GENERATOR_MARK = "bilingual_book_maker translation note"

# Calibre writes its record two ways: EPUB 2 `<meta name="calibre:…">` and,
# in books it has converted, whole elements in a namespace of its own.
CALIBRE = "calibre"


def translation_label(translator):
    """Which kind of thing did the work: a model, or a fixed service.

    The engines — the formats registered to talk to a fixed service rather
    than to a model — did a machine translation; everything else, a model
    named or not, did an AI translation. Looked up by the key the
    translator is registered under, the same way `Base.model_name` names a
    modelless service.
    """
    if translator is None:
        return AI_LABEL
    from book_maker.translator import FORMAT_DICT, LLM_FORMATS
    from book_maker.translator.base_translator import service_name

    key = service_name(translator)
    if key in FORMAT_DICT and key not in LLM_FORMATS:
        return ENGINE_LABEL
    return AI_LABEL


def model_id(translator):
    """What to record as the model, from the translator that will run.

    Never hard-coded: a book that says it was translated by a model it was
    not translated by is worse than one that says nothing. A run given a
    `--model_list` may use any of them — which one a given paragraph went
    to is not knowable from here — so all of them are named. That is the
    honest statement; picking the first would be a false one.
    """
    if translator is None:
        return "unspecified model"
    # `_model_names` and nothing else. It is the readable list kept by the
    # one translator that actually rotates (the openai route, beside a
    # `model_list` that is an itertools.cycle — iterating one never ends,
    # and a smoke run on 260902 found the write step of every openai cell
    # growing past 2.5 GB doing exactly that). `model_list` is not a
    # substitute: codex stores every name there and then sends `self.model`
    # for every request, so reading it would name models that never ran.
    models = getattr(translator, "_model_names", None)
    if isinstance(models, (list, tuple)):
        names = [str(name) for name in models if name]
        if len(names) > 1:
            return ", ".join(names)
    name = getattr(translator, "model_name", None)
    if name:
        return str(name)
    return str(getattr(translator, "model", None) or type(translator).__name__)


def credit_name(translator):
    """What the visible line names as the translator.

    A model id for a model, and the service's own name for the fixed
    services — "Translated by Google Translate, 2026." is what a reader
    understands; "Translated by google, 2026." is a registry key leaking
    onto the page.
    """
    if translation_label(translator) == ENGINE_LABEL:
        from book_maker.translator.base_translator import service_name

        return ENGINE_NAMES.get(service_name(translator), model_id(translator))
    return model_id(translator)


def credit_markup(name, when):
    """The one line, as XHTML.

    The name goes through `redact` on the way in. A model id is a string
    this process was handed and normally says nothing secret — but it is
    the only free text on the page, `--model` is one typo away from
    `--key`, and this line is printed in every copy of the book that leaves
    the machine.
    """
    return (
        f'<p class="{CREDIT_CLASS}" style="{CREDIT_STYLE}">'
        f"{escape(CREDIT_PREFIX)}{escape(redact(str(name)))}, {when.year}.</p>"
    )


def is_calibre_metadata(namespace, name, others):
    """Whether a copied metadata entry is calibre describing its own output.

    Matched on the namespace and on the `name`/`property` prefix, because
    the same record arrives either way depending on how the book was built.
    `ibooks:` and every other vendor prefix is left alone: only the entries
    that assert something about a file that no longer exists are dropped.
    """
    if CALIBRE in (namespace or "").lower():
        return True
    if CALIBRE in (name or "").lower().split(":")[0]:
        return True
    for attribute in ("name", "property"):
        value = (others or {}).get(attribute) or ""
        if value.lower().startswith(f"{CALIBRE}:"):
            return True
    return False


# ------------------------------------------------- recognising our own work

_HEAD_RE = re.compile(rb"<head\b[^>]*>(.*?)</head>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(rb"<meta\b[^>]*?/?>", re.IGNORECASE)
_GENERATOR_RE = re.compile(rb"""\bname\s*=\s*(["'])generator\1""", re.IGNORECASE)
_MARK_RE = re.compile(
    rb"""\bcontent\s*=\s*(["'])"""
    + re.escape(GENERATOR_MARK.encode("utf-8"))
    + rb"""\1""",
    re.IGNORECASE,
)


def is_our_colophon(item):
    """Whether this document is a closing note an *older build* wrote.

    Nothing writes such a page any more, but books carrying one exist, and
    a rerun drops it rather than translating it and shipping it beside this
    run's credit line.

    Read from the head's generator meta, never from the id or the file
    name: a book of its own may legitimately carry either, and mistaking
    one for ours would delete a page of the book. The head is isolated
    first so a marker quoted in the body text cannot pass for a
    declaration.
    """
    content = getattr(item, "content", None)
    if not content:
        return False
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    head = _HEAD_RE.search(content)
    if head is None:
        return False
    return any(
        _GENERATOR_RE.search(meta) and _MARK_RE.search(meta)
        for meta in _META_RE.findall(head.group(1))
    )


def _iter_metadata(book):
    """(namespace, name, value, others) for every metadata entry."""
    for namespace, metas in book.metadata.items():
        if not isinstance(metas, dict):
            continue
        for name, values in metas.items():
            for entry in values:
                if isinstance(entry, tuple):
                    value = entry[0]
                    others = entry[1] if len(entry) > 1 else None
                else:
                    value, others = entry, None
                yield namespace, name, value, others


def is_tool_credit(value):
    """Whether a contributor value is this tool naming itself.

    Two spellings, because the two roles an older build wrote said
    different amounts. The `trl` credit was the bare tool name; the `bkp`
    credit carried the build that produced the file
    (`bilingual_book_maker 0449d78`). Both are ours to remove; a book that
    credits this project in a sentence of its own — "with thanks to
    bilingual_book_maker and its contributors" — is neither.
    """
    text = (value or "").strip()
    return text == TOOL_NAME or text.startswith(f"{TOOL_NAME} ")


def tool_contributor_ids(book):
    """The ids of `dc:contributor` entries a previous run of this tool wrote.

    Ours is the tool's name refined by one of this tool's roles — both
    halves, so a book that merely credits this project in its own
    contributor list is not mistaken for a stamp and quietly deleted.
    """
    refined = {
        (others or {}).get("refines", "").lstrip("#")
        for _, name, value, others in _iter_metadata(book)
        if name == "meta"
        and (others or {}).get("property") == "role"
        and (value or "").strip() in CONTRIBUTOR_ROLES
    }
    refined.discard("")
    return {
        (others or {}).get("id")
        for _, name, value, others in _iter_metadata(book)
        if name == "contributor"
        and is_tool_credit(value)
        and (others or {}).get("id") in refined
    }


def prior_glossary_shas(book):
    """The checksums a previous run's record vouches for.

    The embedded glossary is the one thing this tool writes that carries no
    marker of its own — it is the user's file, byte for byte, and putting
    anything inside it would make it not that. So ownership is decided from
    outside: an item is a previous run's glossary only if something in the
    same package that *is* recognisably ours names its exact bytes. That is
    a stronger test than the id or the file name would have been, and it
    leaves a book carrying a `bbm_glossary.txt` of its own alone.

    Two places are asked, because two shapes of book arrive here. A record
    names the checksum inside `bbm_translation_metadata.json`; a book
    stamped by an older build names it in a `bbm:glossary-sha256` meta
    instead, and that meta is stripped on copy — so it is read here, before
    the copy loop runs.
    """
    shas = {
        str((others or {}).get("content") or "").strip()
        for _, name, _, others in _iter_metadata(book)
        if name == "meta"
        and (others or {}).get("name") == tmeta.LEGACY_GLOSSARY_SHA_META
        and (others or {}).get("content")
    }
    shas.discard("")
    for item in book.get_items():
        record = _our_record(item)
        if record is None:
            continue
        vouched = str(record.get(tmeta.GLOSSARY_SHA_KEY) or "").strip()
        if vouched:
            shas.add(vouched)
    return shas


def _item_bytes(item):
    content = getattr(item, "content", None)
    if content is None:
        # `is None`, not falsy: an empty file is still a file, it has a
        # checksum like any other, and a rerun must still drop it.
        return None
    if isinstance(content, str):
        return content.encode("utf-8", "ignore")
    return content


def is_prior_glossary(item, shas):
    """Whether a manifest item is the glossary a previous run embedded."""
    if not shas:
        return False
    content = _item_bytes(item)
    return content is not None and sha256(content).hexdigest() in shas


def is_prior_translation_metadata(item):
    """Whether a manifest item is the record a previous run wrote.

    The marker inside the file, and nothing else. It is the one test that
    still works when a conversion has thrown the package document away —
    which is the whole reason the file exists — and it recognises a record
    written by any build of this tool, old shape or new. A book shipping a
    `bbm_translation_metadata.json` of its own does not answer to it and is
    left alone.
    """
    return _our_record(item) is not None


def _our_record(item):
    """`item` parsed as one of our records, or None if it is not one.

    Every manifest item is offered here, so the cheap rejections come
    first: a record is JSON, and JSON that is one of ours is an object.
    """
    content = _item_bytes(item)
    if content is None or content.lstrip()[:1] != b"{":
        return None
    try:
        record = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if (
        isinstance(record, dict)
        and record.get(tmeta.RECORD_MARK_KEY) == tmeta.RECORD_MARK
    ):
        return record
    return None


def is_prior_disclosure(name, value, others, owned_ids):
    """Whether a copied entry is a previous run's stamp, to be dropped.

    A previous run's claim is this tool's to remove, not to preserve:
    translate model A's output with model B and only B did the work in
    front of the reader. Nothing is written back in its place — this build
    declares nothing in the package metadata — so every branch here is a
    removal.
    """
    attributes = others or {}
    if (
        name == "contributor"
        and is_tool_credit(value)
        and attributes.get("id") in owned_ids
    ):
        return True
    if (
        name == "meta"
        and attributes.get("property") == "role"
        and attributes.get("refines", "").lstrip("#") in owned_ids
    ):
        return True
    if name == "meta" and str(attributes.get("name") or "").startswith(tmeta.PREFIX):
        # The whole `bbm:` prefix is this tool's, so the test is the prefix
        # and nothing else: a run by another model, from another build,
        # against another endpoint must not leave the previous run's
        # answers standing.
        return True
    if name == "description":
        # Compared with whitespace collapsed: the tail's line break is
        # layout, and a stamp written before it existed (one sentence, one
        # space) is still ours to remove.
        text = " ".join((value or "").split())
        if text.endswith(_TAIL_WORDS) and any(
            text.startswith(f"{label} (") for label in DESCRIPTION_LABELS
        ):
            return True
    return False


# ------------------------------------------------- the previous credit line

# Our own paragraph, and only ours: the class is what a previous run put
# there, and `.*?` never has to cross another `<p>` because the line has no
# nested markup. Any leading indentation and the newline after it go with
# it, so a book run through the tool ten times does not accumulate blank
# lines at the end of its title page.
_CREDIT_RE = re.compile(
    rb"[ \t]*<p\b[^>]*\bclass\s*=\s*([\"'])[^\"']*\b"
    + re.escape(CREDIT_CLASS.encode("utf-8"))
    + rb"\b[^\"']*\1[^>]*>.*?</p\s*>[ \t]*\r?\n?",
    re.IGNORECASE | re.DOTALL,
)
_CREDIT_MARKER = CREDIT_CLASS.encode("utf-8")


def strip_prior_credit(book):
    """Remove the credit line a previous run appended, wherever it sits.

    Done on the *source* book, before a word of it is translated, which is
    the only place it can be done at all: left in, the line is a paragraph
    like any other — the next run translates it, inserts the translation
    beside it, and no later pass can tell the two apart. Unconditional, and
    deliberately not behind `--no_disclosure`: what a previous run said
    about itself is this tool's to remove either way, and the flag exists
    to stop the file making a claim, not to preserve last run's.

    Returns the number of documents changed.
    """
    changed = 0
    for item in book.get_items():
        content = _item_bytes(item)
        if content is None or _CREDIT_MARKER not in content:
            continue
        stripped = _CREDIT_RE.sub(b"", content)
        if stripped != content:
            item.content = stripped
            changed += 1
    return changed


def has_credit(book):
    """Whether this book already carries a credit line from this run.

    The stamp is idempotent because one write route stamps the same book
    twice; this is what makes the second call a no-op.
    """
    return any(
        _CREDIT_MARKER in (_item_bytes(item) or b"") for item in book.get_items()
    )


# ------------------------------------------------- where the line goes

_ANCHOR_RE = re.compile(rb"<a\b[^>]*>", re.IGNORECASE)
_TYPE_ATTR = re.compile(rb"""\bepub:type\s*=\s*(["'])([^"']*)\1""", re.IGNORECASE)
_HREF_ATTR = re.compile(rb"""\bhref\s*=\s*(["'])([^"']*)\1""", re.IGNORECASE)
_BODY_END_RE = re.compile(rb"</body\s*>", re.IGNORECASE)

DOCUMENT_SUFFIXES = (".xhtml", ".html", ".htm")


def _normalise_type(kind):
    return "".join(ch for ch in str(kind or "").lower() if ch.isalnum())


def _is_document(item):
    if isinstance(item, epub.EpubNav):
        # ebooklib's generated navigation. It is a table of contents, not a
        # page of the book, and a line appended to it reads as an entry.
        return False
    if "nav" in (getattr(item, "properties", None) or ()):
        # The same document as read from a real book, where it arrives as
        # an ordinary EpubHtml carrying the `nav` property.
        return False
    if getattr(item, "media_type", None) == "application/xhtml+xml":
        return True
    return str(getattr(item, "file_name", "") or "").lower().endswith(DOCUMENT_SUFFIXES)


def _item_by_href(book, href):
    """The manifest item a guide or landmark href points at, or None."""
    target = str(href or "").split("#", 1)[0].strip()
    if not target:
        return None
    items = list(book.get_items())
    for item in items:
        if (getattr(item, "file_name", "") or "") == target:
            return item
    # A guide written relative to a different directory than the manifest
    # is common enough in real books to be worth the second pass, and a
    # basename collision costs a line on the wrong page, never a broken
    # book.
    base = target.rsplit("/", 1)[-1]
    for item in items:
        if (getattr(item, "file_name", "") or "").rsplit("/", 1)[-1] == base:
            return item
    return None


def _guide_title_page(book, guide=None):
    """The title page the EPUB 2 `<guide>` names, if it names one.

    `guide` is passed in because ebooklib parses the source's guide but the
    rebuilt book is not given one, so the only copy of it is the one the
    loader kept.
    """
    for entries in (guide, getattr(book, "guide", None)):
        for entry in entries or ():
            if not isinstance(entry, dict):
                continue
            if _normalise_type(entry.get("type")) in TITLE_PAGE_TYPES:
                item = _item_by_href(book, entry.get("href"))
                if item is not None:
                    return item
    return None


def _landmark_title_page(book):
    """The title page the EPUB 3 landmarks name, if they name one.

    ebooklib parses the EPUB 2 guide and stops there — landmarks live in
    the navigation document, as ordinary markup — so this reads them out of
    it. An `epub:type="titlepage"` on an anchor is a landmarks entry and
    nothing else, so the nav document does not have to be identified first.
    """
    for item in book.get_items():
        content = _item_bytes(item)
        if content is None or b"titlepage" not in content.lower():
            continue
        for anchor in _ANCHOR_RE.findall(content):
            kind = _TYPE_ATTR.search(anchor)
            if kind is None:
                continue
            types = {
                _normalise_type(part)
                for part in kind.group(2).decode("utf-8", "ignore").split()
            }
            if not types & TITLE_PAGE_TYPES:
                continue
            href = _HREF_ATTR.search(anchor)
            if href is None:
                continue
            target = _item_by_href(book, href.group(2).decode("utf-8", "ignore"))
            if target is not None:
                return target
    return None


def _linear_spine_documents(book):
    """Every document in reading order, skipping what a reader never opens."""
    for entry in getattr(book, "spine", None) or ():
        target, linear = entry if isinstance(entry, tuple) else (entry, "yes")
        if str(linear).lower() == "no":
            continue
        item = book.get_item_with_id(target) if isinstance(target, str) else target
        if item is not None and _is_document(item):
            yield item


def _credit_candidates(book, guide=None):
    """The documents that could carry the line, best first.

    The title page as the guide or the landmarks name it, then the first
    linear document — which for a book that declares neither is the title
    page in all but name, and for one that does is at least the first thing
    a reader opens.
    """
    seen = set()
    for item in (
        _guide_title_page(book, guide),
        _landmark_title_page(book),
        *_linear_spine_documents(book),
    ):
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        yield item


def _with_credit(item, markup):
    """`item`'s content with the line before its last `</body>`, or None.

    None means this document cannot take it — no body to append to — and
    the caller should try the next candidate rather than write a broken
    page.
    """
    content = _item_bytes(item)
    if content is None:
        return None
    end = None
    for match in _BODY_END_RE.finditer(content):
        end = match
    if end is None:
        return None
    return (
        content[: end.start()] + markup.encode("utf-8") + b"\n" + content[end.start() :]
    )


def credit_placement(book, markup, guide=None):
    """`(item, content)` for the document that takes the line, or `(None, None)`."""
    for candidate in _credit_candidates(book, guide):
        content = _with_credit(candidate, markup)
        if content is not None:
            return candidate, content
    return None, None


# ------------------------------------------------------------- allocating


def taken_ids(book):
    """Every id already spoken for in the package: metadata and manifest."""
    ids = {
        (others or {}).get("id")
        for _, _, _, others in _iter_metadata(book)
        if (others or {}).get("id")
    }
    for item in book.get_items():
        item_id = item.get_id()
        if item_id:
            ids.add(item_id)
    return ids


def _suffixes():
    yield ""
    counter = 2
    while True:
        yield f"-{counter}"
        counter += 1


def taken_file_names(book):
    return {
        item.file_name for item in book.get_items() if getattr(item, "file_name", None)
    }


def allocate_names(book, stem, extension, base_id, ids=None, files=None):
    """An id and a file name neither of which the book already uses.

    Both move together: a reader that finds `bbm_glossary-2.txt` should
    find it under the matching id, not under a third name.
    """
    ids = taken_ids(book) if ids is None else ids
    files = taken_file_names(book) if files is None else files
    for suffix in _suffixes():
        item_id = f"{base_id}{suffix}"
        file_name = f"{stem}{suffix}{extension}"
        if item_id not in ids and file_name not in files:
            return item_id, file_name


def allocate_glossary_names(book, ids=None, files=None):
    return allocate_names(
        book, tmeta.GLOSSARY_STEM, ".txt", tmeta.GLOSSARY_ID, ids, files
    )


def allocate_translation_metadata_names(book, ids=None, files=None):
    return allocate_names(
        book,
        tmeta.TRANSLATION_METADATA_STEM,
        ".json",
        tmeta.TRANSLATION_METADATA_ID,
        ids,
        files,
    )


# ------------------------------------------------------------- the stamp


def entry_is_our_colophon(source_book, entry):
    """A spine entry may be an item or, on a book read from disk, a bare
    idref string that only the source book can resolve."""
    target = entry[0] if isinstance(entry, tuple) else entry
    if isinstance(target, str):
        target = source_book.get_item_with_id(target) if source_book else None
    return target is not None and is_our_colophon(target)


def stamp_disclosure(book, model, when=None, guide=None, translation_metadata=None):
    """Add the credit line, and the machine record, once.

    Called on the finished book just before it is written, not while it is
    being built: `--model_list` rotation means the model a run actually
    used is not known until the last paragraph is done.

    Idempotent, because one route writes the book twice.

    Everything that reads the book is done before anything writes to it, so
    a failure leaves the book exactly as it arrived. The caller is allowed
    to give up on the stamp and write the book anyway (see
    `EPUBBookLoader._stamp_disclosure`), and a half-applied stamp is the one
    outcome that would make that worse than useless: a record naming a
    model with no line in front of the reader says the opposite of what the
    ruling asks for.

    `translation_metadata`, when given, is a `TranslationMetadata`: the
    reader-invisible half of the same statement, written under the same
    all-or-nothing rule. It rides with the disclosure rather than having a
    switch of its own — `--no_disclosure` says the file must not claim to be
    a machine translation, and a record naming the model claims exactly
    that, in the one place a script would look.

    Returns the document the line landed on.
    """
    if has_credit(book):
        return None

    # ---- read the book, decide everything, touch nothing
    when = when or date.today()
    markup = credit_markup(model, when)
    target, content = credit_placement(book, markup, guide)
    if target is None:
        raise ValueError(
            "the book has no document that can carry the translation credit"
        )

    ids = taken_ids(book)
    files = taken_file_names(book)
    glossary_item = record_item = None
    if translation_metadata is not None:
        # Settled here, so the commit half only appends: `record()` hashes
        # the glossary, and that does not belong between the line and the
        # manifest.
        record_bytes = translation_metadata.record(when)
        record_id, record_file = allocate_translation_metadata_names(
            book, ids=ids, files=files
        )
        ids.add(record_id)
        files.add(record_file)
        record_item = epub.EpubItem(
            uid=record_id,
            file_name=record_file,
            media_type=tmeta.TRANSLATION_METADATA_MEDIA_TYPE,
            content=record_bytes,
        )
        if translation_metadata.glossary_bytes is not None:
            glossary_id, glossary_file = allocate_glossary_names(
                book, ids=ids, files=files
            )
            glossary_item = epub.EpubItem(
                uid=glossary_id,
                file_name=glossary_file,
                media_type=tmeta.GLOSSARY_MEDIA_TYPE,
                content=translation_metadata.glossary_bytes,
            )

    # ---- commit: an assignment and two appends, nothing that can fail
    target.content = content
    if record_item is not None:
        # Manifest only, never the spine: both are evidence about the
        # translation, not pages of the book.
        book.add_item(record_item)
    if glossary_item is not None:
        book.add_item(glossary_item)
    return target
