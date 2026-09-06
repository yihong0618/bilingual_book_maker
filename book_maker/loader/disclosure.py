"""Say, in the file itself, that the file is a machine translation.

The rule this follows is the one the rest of the branch follows: preserve
what identifies the work and its rights, rewrite what describes *this*
file, drop what is now false. So `dc:creator`, `dc:rights` and the source's
own `dc:description` are never touched — the tool is appended as a
*contributor* with the MARC relator for translator, and its own description
sits beside the publisher's. Calibre's metadata is the "now false" case: it
records the file calibre built, which this is not.

The colophon says the same thing to a reader, on one page at the end,
because metadata is not something a reader sees. It is written as a log —
one heading, then `Title: content` a line at a time — rather than as prose:
a page of facts about a file should not arrive dressed as a chapter.

Beside it, on a plan or session run or when `--provenance` asks, goes the
machine half: the `bbm:` metas and the book-producer credit that say which
build, which model, which endpoint host and which command produced the file,
and `bbm_provenance.json` saying it a second time as a manifest item, because
a conversion rewrites the package document and keeps the files. That record is
`book_maker.provenance`'s to compose and this module's to put in the package,
under the same rules everything else here follows.

Two rules keep this from colliding with the book it is stamping:

- **Nothing is recognised by its id or file name.** A publisher's own
  `colophon.xhtml`, or an `id="colophon"` on a real chapter, must survive
  untouched. What this tool wrote is recognised only by a marker it put
  inside the document, and a contributor only by its value plus its role
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

from book_maker import provenance as prov

TOOL_NAME = "bilingual_book_maker"

# MARC relator "trl" — translator.
CONTRIBUTOR_ID = "bbm-trl"
CONTRIBUTOR_ROLE = "trl"
MARC_SCHEME = "marc:relators"

# Both roles this tool ever claims: it translated the text (`trl`) and, when
# the machine record is written, it produced the file (`bkp`). Listed
# together because ownership is decided once: an entry carrying this tool's
# name refined by either role is a previous run's stamp, to be rewritten.
CONTRIBUTOR_ROLES = (CONTRIBUTOR_ROLE, prov.PRODUCER_ROLE)

# The description this tool writes: a label, then the model and the year in
# parentheses, then a fixed tail. The label says what did the work — a
# model is an "AI translation", an engine such as Google or DeepL a
# "Machine translation". Label and tail are both matched when deciding
# whether a description is one of ours: a publisher's "Machine translation
# (French edition)" opens the same way and is a statement about the book,
# not a stamp to be replaced.
AI_LABEL = "AI translation"
ENGINE_LABEL = "Machine translation"
DESCRIPTION_LABELS = (AI_LABEL, ENGINE_LABEL)
DESCRIPTION_TAIL = ").\nOriginal text unaltered; translation quality not verified."
_TAIL_WORDS = " ".join(DESCRIPTION_TAIL.split())

# A fixed-layout book requires every spine document to declare its page
# dimensions, and epubcheck enforces it (HTM-046). The note has none to
# declare — it is text of whatever length the model id makes it — so it
# says instead that it is not laid out like the rest of the book. That is
# what the fixed-layout books in the corpus do for their own prose pages
# (cole-voyage-of-life-tol.epub ships exactly this property on an itemref),
# and it beats inventing a page size for someone else's book: the nine
# fixed-layout books in `epub-sample` carry four different viewports
# between them, and four of the nine declare none anywhere.
FIXED_LAYOUT_PROPERTY = "rendition:layout"
PRE_PAGINATED = "pre-paginated"
REFLOWABLE_PROPERTY = "rendition:layout-reflowable"

COLOPHON_ID = "bbm-translation-note"
COLOPHON_STEM = "bbm_translation_note"
COLOPHON_FILE = f"{COLOPHON_STEM}.xhtml"
COLOPHON_TITLE = "Translation note"
# What the page calls itself to the reader. The document `<title>` stays
# "Translation note" — it is what a reading system shows in a tab and what
# the item is called in the manifest — but the heading on the page says the
# thing the page is for: credits, not a warning label (owner call, 260905).
COLOPHON_HEADING = "Translation Credits"
UNREVIEWED = "This translation has not been reviewed by a human translator."
NOTE_LABEL = "Note"

# What makes a colophon *ours*, written into the document and read back out
# of it. An id or a file name is a coincidence waiting to happen; a
# generator marker in the head is a statement.
GENERATOR_MARK = "bilingual_book_maker translation note"

# Calibre writes its record two ways: EPUB 2 `<meta name="calibre:…">` and,
# in books it has converted, whole elements in a namespace of its own.
CALIBRE = "calibre"


def translation_label(translator):
    """Which label the description opens with, from the translator that ran.

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
    """Whether this document is a translation note *this tool* wrote.

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

    Two spellings, because the two roles say different amounts. The `trl`
    credit is the bare tool name; the `bkp` credit carries the build that
    produced the file (`bilingual_book_maker 0449d78`), which is the whole
    reason a machine record exists. Both are ours to rewrite; a book that
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
    """The checksums a previous run's glossary metas vouch for.

    The embedded glossary is the one thing this tool writes that carries no
    marker of its own — it is the user's file, byte for byte, and putting
    anything inside it would make it not that. So ownership is decided by
    the record instead: an item is a previous run's glossary only if a
    `bbm:glossary-sha256` meta in the same package names its exact bytes.
    That is a stronger test than the id or the file name would have been,
    and it leaves a book carrying a `bbm_glossary.txt` of its own alone.
    """
    return _vouched_shas(book, prov.GLOSSARY_SHA_META)


def prior_provenance_shas(book):
    """The checksums a previous run's `bbm:provenance-sha256` metas vouch for."""
    return _vouched_shas(book, prov.PROVENANCE_SHA_META)


def _vouched_shas(book, meta_name):
    return {
        str((others or {}).get("content") or "").strip()
        for _, name, _, others in _iter_metadata(book)
        if name == "meta"
        and (others or {}).get("name") == meta_name
        and (others or {}).get("content")
    }


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


def is_prior_provenance(item, shas):
    """Whether a manifest item is the record a previous run wrote.

    Two ways, and either is enough. The checksum is the glossary's test,
    kept for consistency; the marker inside the file is the colophon's, and
    it is the one that still works when the metas are gone — which is the
    whole reason the file exists. A book shipping a `bbm_provenance.json`
    of its own answers to neither and is left alone.
    """
    content = _item_bytes(item)
    if content is None:
        return False
    if shas and sha256(content).hexdigest() in shas:
        return True
    try:
        record = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    return (
        isinstance(record, dict)
        and record.get(prov.RECORD_MARK_KEY) == prov.RECORD_MARK
    )


def is_prior_disclosure(name, value, others, owned_ids):
    """Whether a copied entry is a previous run's stamp, to be replaced.

    A previous run's claim is this tool's to rewrite, not to preserve:
    translate model A's output with model B and only B did the work in
    front of the reader.
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
    if name == "meta" and str(attributes.get("name") or "").startswith(prov.PREFIX):
        # The machine record. The whole `bbm:` prefix is this tool's, so the
        # test is the prefix and nothing else: a run by another model, from
        # another build, against another endpoint must not leave the previous
        # run's answers standing beside its own.
        return True
    if name == "description":
        # Compared with whitespace collapsed: the tail's line break is
        # layout, and a stamp written before it existed (one sentence, one
        # space) is still ours to replace.
        text = " ".join((value or "").split())
        if text.endswith(_TAIL_WORDS) and any(
            text.startswith(f"{label} (") for label in DESCRIPTION_LABELS
        ):
            return True
    return False


# ------------------------------------------------------------- allocating


def is_fixed_layout(book):
    """Whether the whole package is pre-paginated.

    Package level only. A book that is reflowable by default and pins
    individual documents pre-paginated leaves the note reflowable like
    everything else it did not name, which is already right.
    """
    return any(
        name == "meta" and (others or {}).get("property") == FIXED_LAYOUT_PROPERTY
        # A `refines` makes the entry a statement about one document, not
        # about the package: a book that pins a single page pre-paginated
        # is still reflowable everywhere it did not say otherwise.
        and not (others or {}).get("refines") and (value or "").strip() == PRE_PAGINATED
        for _, name, value, others in _iter_metadata(book)
    )


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


def allocate_contributor_id(book, base=CONTRIBUTOR_ID, ids=None):
    """An id the book does not already use.

    `ids` lets a caller allocating more than one in a row pass the set it is
    growing: two ids allocated independently against the same book would
    both be handed the same free name.
    """
    ids = taken_ids(book) if ids is None else ids
    for suffix in _suffixes():
        candidate = f"{base}{suffix}"
        if candidate not in ids:
            return candidate


def allocate_names(book, stem, extension, base_id, ids=None, files=None):
    """An id and a file name neither of which the book already uses.

    Both move together: a reader that finds `bbm_translation_note-2.xhtml`
    should find it under the matching id, not under a third name.
    """
    ids = taken_ids(book) if ids is None else ids
    files = taken_file_names(book) if files is None else files
    for suffix in _suffixes():
        item_id = f"{base_id}{suffix}"
        file_name = f"{stem}{suffix}{extension}"
        if item_id not in ids and file_name not in files:
            return item_id, file_name


def allocate_colophon_names(book, ids=None, files=None):
    return allocate_names(book, COLOPHON_STEM, ".xhtml", COLOPHON_ID, ids, files)


def allocate_glossary_names(book, ids=None, files=None):
    return allocate_names(
        book, prov.GLOSSARY_STEM, ".txt", prov.GLOSSARY_ID, ids, files
    )


def allocate_provenance_names(book, ids=None, files=None):
    return allocate_names(
        book, prov.PROVENANCE_STEM, ".json", prov.PROVENANCE_ID, ids, files
    )


# ------------------------------------------------------------- the stamp


def build_colophon(
    model, language, source_identifier=None, when=None, item_id=None, file_name=None
):
    """The one page that says, to a reader, what this file is.

    A log, not a document: one heading, then `Title: content` on a line of
    its own, in the order a person asks the questions. No list markup, no
    bold, no nesting — a page of facts about a file should not arrive
    dressed as a chapter, and a reader flicking to the end of a book wants
    to read five lines, not parse a layout.
    """
    when = when or date.today()
    # Model, date, the note — nothing more. The tool credit, the source
    # identifier and the languages are all in the machine record; a reader
    # flicking to the last page gets the three facts that concern *them*.
    lines = [
        ("Model", model),
        ("Date", when.isoformat()),
        (NOTE_LABEL, UNREVIEWED),
    ]

    rows = "\n".join(
        f"    <p>{escape(label)}: {escape(str(value))}</p>" for label, value in lines
    )
    document = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        "  <head>\n"
        f"    <title>{COLOPHON_TITLE}</title>\n"
        f'    <meta name="generator" content="{GENERATOR_MARK}"/>\n'
        "  </head>\n"
        "  <body>\n"
        f"    <h1>{COLOPHON_HEADING}</h1>\n"
        f"{rows}\n"
        "  </body>\n</html>\n"
    )

    item = epub.EpubHtml(
        uid=item_id or COLOPHON_ID,
        file_name=file_name or COLOPHON_FILE,
        title=COLOPHON_TITLE,
    )
    item.content = document.encode("utf-8")
    return item


def entry_is_our_colophon(source_book, entry):
    """A spine entry may be an item or, on a book read from disk, a bare
    idref string that only the source book can resolve."""
    target = entry[0] if isinstance(entry, tuple) else entry
    if isinstance(target, str):
        target = source_book.get_item_with_id(target) if source_book else None
    return target is not None and is_our_colophon(target)


def stamp_disclosure(
    book,
    model,
    language,
    source_identifier=None,
    when=None,
    label=AI_LABEL,
    provenance=None,
):
    """Add the credit, the description, the colophon and the machine record, once.

    Called on the finished book just before it is written, not while it is
    being built: `--model_list` rotation means the model a run actually
    used is not known until the last paragraph is done.

    Idempotent, because one route writes the book twice.

    Everything that reads the book is done before anything writes to it, so
    a failure leaves the book exactly as it arrived. The caller is allowed
    to give up on the stamp and write the book anyway (see
    `EPUBBookLoader._stamp_disclosure`), and a half-applied stamp is the one
    outcome that would make that worse than useless: a credit naming a
    translator with no note behind it says less than saying nothing.

    `provenance`, when given, is a `book_maker.provenance.Provenance`: the
    invisible half of the same statement, written under the same all-or-
    nothing rule. It rides with the disclosure rather than having a switch of
    its own — `--no_disclosure` says the file must not claim to be a machine
    translation, and `bbm:model` claims exactly that, in the one place a
    script would look.
    """
    if any(is_our_colophon(item) for item in book.get_items()):
        return None

    # ---- read the book, decide everything, touch nothing
    #
    # Including the two things the commit half assumes about the book, which
    # is the only way the commit half can fail. `add_metadata` indexes
    # `book.metadata[namespace]` as a dict of lists, and `_iter_metadata`
    # deliberately tolerates a namespace that is not one — so such a book can
    # reach here, and committing would raise between the credit and the note.
    # The OPF namespace is checked with them because the machine record is
    # written there, as `<meta name= content=>`.
    for namespace in (epub.NAMESPACES["DC"], epub.NAMESPACES["OPF"], None):
        existing = book.metadata.get(namespace)
        if existing is not None and not isinstance(existing, dict):
            raise TypeError(
                f"the book's {namespace or 'default'} metadata is a "
                f"{type(existing).__name__}, not a dict of entries; nothing "
                f"can be added to it"
            )
    if not hasattr(book.spine, "append"):
        raise TypeError(
            f"the book's spine is a {type(book.spine).__name__}; the "
            f"translation note cannot be appended to it"
        )

    when = when or date.today()
    # One growing set for every id this stamp allocates: two allocations run
    # independently against the same book would both be handed the same free
    # name, and a duplicate id is a book no reading system opens.
    ids = taken_ids(book)
    files = taken_file_names(book)
    contributor_id = allocate_contributor_id(book, ids=ids)
    ids.add(contributor_id)
    item_id, file_name = allocate_colophon_names(book, ids=ids, files=files)
    ids.add(item_id)
    files.add(file_name)

    producer_id = glossary_item = record_item = None
    producer_credit = None
    provenance_metas = ()
    if provenance is not None:
        producer_id = allocate_contributor_id(book, base=prov.PRODUCER_ID, ids=ids)
        ids.add(producer_id)
        # Both settled here, so the commit half only appends: `metas()`
        # hashes the glossary and `producer()` reads the build, and neither
        # belongs between the credit and the note.
        provenance_metas = provenance.metas()
        producer_credit = provenance.producer()
        # The file is built from the very list the metas are written from, so
        # the two forms cannot drift; its own checksum is then appended as a
        # meta, which is why it could not have been in the file.
        record_bytes = provenance.record(provenance_metas)
        provenance_metas = provenance_metas + [
            (prov.PROVENANCE_SHA_META, sha256(record_bytes).hexdigest())
        ]
        record_id, record_file = allocate_provenance_names(book, ids=ids, files=files)
        ids.add(record_id)
        files.add(record_file)
        record_item = epub.EpubItem(
            uid=record_id,
            file_name=record_file,
            media_type=prov.PROVENANCE_MEDIA_TYPE,
            content=record_bytes,
        )
        if provenance.glossary_bytes is not None:
            glossary_id, glossary_file = allocate_glossary_names(
                book, ids=ids, files=files
            )
            glossary_item = epub.EpubItem(
                uid=glossary_id,
                file_name=glossary_file,
                media_type=prov.GLOSSARY_MEDIA_TYPE,
                content=provenance.glossary_bytes,
            )

    description = f"{label} ({model}, {when.year}{DESCRIPTION_TAIL}"
    item = build_colophon(
        model,
        language,
        source_identifier,
        when,
        item_id=item_id,
        file_name=file_name,
    )
    if is_fixed_layout(book):
        # Read back by the spine writer: ebooklib emits `idref` and `linear`
        # and nothing else, so the property is carried on the item until the
        # OPF is built. See `_write_opf_spine_patch` in epub_loader.
        item.spine_properties = [REFLOWABLE_PROPERTY]

    # ---- commit: appends to a list and a dict, nothing that can decide to fail
    book.add_metadata("DC", "contributor", TOOL_NAME, {"id": contributor_id})
    book.add_metadata(
        None,
        "meta",
        CONTRIBUTOR_ROLE,
        {
            "refines": f"#{contributor_id}",
            "property": "role",
            "scheme": MARC_SCHEME,
        },
    )
    book.add_metadata("DC", "description", description)
    if provenance is not None:
        for meta_name, content in provenance_metas:
            book.add_metadata(
                "OPF", "meta", None, {"name": meta_name, "content": content}
            )
        book.add_metadata("DC", "contributor", producer_credit, {"id": producer_id})
        book.add_metadata(
            None,
            "meta",
            prov.PRODUCER_ROLE,
            {
                "refines": f"#{producer_id}",
                "property": "role",
                "scheme": MARC_SCHEME,
            },
        )
        # Manifest only, never the spine: both are evidence about the
        # translation, not pages of the book.
        book.add_item(record_item)
        if glossary_item is not None:
            book.add_item(glossary_item)
    book.add_item(item)
    book.spine.append(item)
    return item
