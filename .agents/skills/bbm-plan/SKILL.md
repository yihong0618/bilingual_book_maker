---
name: bbm-plan
description: Translate a book into a bilingual edition with bilingual_book_maker ("bbm") - an EPUB through plan mode (greedy partition, an agent-reviewed classification plan, then a full resumable run), a PDF into a bilingual, reflowable EPUB (--to-epub), or a TXT, SRT or Markdown file. Use when the user says "translate this book", "translate this epub/pdf", asks for "plan mode" or "bbm" translation, or wants a book translated well (running heads, page numbers and apparatus skipped deliberately) rather than a quick --translate-tags pass.
---

# bbm-plan: translate a book with bilingual_book_maker

This file is the router. Do the intake below, then load **one** flow file
for the book in hand and follow it. The other flow files stay unread.

Repo: the repository this skill ships in (`make_book.py` at its root). Run
every command from the repo root. The user documentation is the MkDocs site
in `docs/` of this checkout; the flow files point at the page to read for
anything they do not spell out. `python make_book.py --help` is the
authority on every flag, and `docs/cmd.md` lists them all in one place.

## Your stance

You are the trained CLI operator. You pick every flag from the flow file's
recommendations and state each choice with a one-line reason; the
recommended defaults are what you pass unless the user asks for something
else or the book argues otherwise. The user hands over a book, credentials
and (if they have one) a prompt file, approves the plan and the cost, and
gets a bilingual book back. They are never asked to experiment with flags,
halt semantics or resume mechanics.

**The EPUB flow uses `--plan-classify agent`.** The plan arrives with its
uncertain signatures set to `"action": null`, and the translate run
refuses to start while any null remains, so *you*, the coding agent, own
the classification against the real samples. Do it in the main agent with
full session context; never delegate plan editing to a subagent or a
small/fast model. The one exception is the user naming a classifier
themselves (Jev, or a model): then follow "A classifier the user names" in
`references/epub-plan-mode.md`, and still read its skips.

All state lives on disk (`bbm_providers.json`, `.env`, the flow's own
working files, the resume cache, `run.log`), so any step can be redone
after a crash or in a new session.

## Intake

1. **Route and credentials: always read `references/route-setup.md`**
   before the first command. It probes what the user already has, asks one
   question (route, model, prompt file), binds the `ROUTE` and `CONTEXT`
   arrays every flow uses, and probes the endpoint for a sub-cent before
   anything is paid. **Keys never go on the command line**: the handoff
   block reprints the whole command, and argparse echoes it back on a
   mistyped flag. If a key ever reaches the terminal, say so at once and
   tell the user to rotate it.
2. **Book path** and **target language** (`--language`, e.g. `zh-hans`,
   `ja`, `Simplified Chinese`). For a small language the tables may not
   know, pass both halves: `--language "ain:Ainu"` (tag before the colon,
   name after; the tag list is `docs/languages.md`). `--source_lang` states
   the source language for a book whose short lines or names could be
   misdetected.
3. **Their prompt file**, asked in the route question. If they hand one
   over, lint it before the first paid run: `references/prompt-files.md`.
   If they say no, offer one sentence of what a style instruction would buy
   them and move on. Independently, look for an existing template in the
   book's directory and the repo root (`prompt.json`, `prompt.txt`,
   `prompt*.md`, `prompt_template*`). **A candidate must carry a diff**: in
   a git repo only untracked or modified-against-HEAD files count; cleanly
   tracked `prompt*` files are the repo's shipped examples, not the user's
   voice. Found one? **Ask before doing anything with it**; never adopt or
   ignore it silently.
4. Anything they want changed from the recommended defaults: a visual style
   for the translation, specific chapters or pages only.

**Bilingual is the assumption; do not ask.** The output keeps every
original next to its translation unless the user asked for a
translated-only book in so many words ("just the Chinese", "replace the
original", "not bilingual", "single language"). *"Translate this book to
Chinese"* is not that request; it is the ordinary ask, and it means a
bilingual book. Never infer single-language output (`--single_translate`)
from the target language being named, from the book being short, or from
the user sounding brisk. Say which one you are producing in the same line
where you state the flag choices, so a user who wanted the other one can
say so before anything is paid for.

## Branch by the book

| the book is | read, and follow | the user page behind it |
|---|---|---|
| an `.epub` | `references/epub-plan-mode.md` | `docs/formats/epub.md` |
| a `.pdf` | `references/pdf-route.md` | `docs/features/pdf-to-epub.md` |
| a `.txt`, `.srt` or `.md` | `references/plain-formats.md` | `docs/formats/txt.md`, `docs/formats/srt.md`, `docs/formats/md.md` |

A file with another extension is not a book this tool reads; say so. A
folder of chapters is several books: ask whether they want one run per
file.

## Stating the choices

Before the first paid run, one short block to the user:

- the command you will run, with one line per non-default flag saying why;
- bilingual or translated-only, in so many words;
- the checkpoint the flow file puts before the full run (what the user
  will be shown, and when), and any test run you skip or add;
- which allowance is spent: the API key's account, or the ChatGPT plan on
  the codex route.

Then wait for the approval of the cost, and run.

## Context hygiene

- **Never** let a translation run stream into the conversation: every paid
  run gets `--quiet` where the format takes it *and* a log-file redirect
  (`> run.log 2>&1`), in the background, polled with `tail -5 run.log`.
- Everything the next step needs is on disk; nothing critical lives only in
  conversation.
- **Compaction threshold**, *your* context, not the run's: for a small book
  do not compact at all. Only compact when context is genuinely pressured
  (roughly 70% used), at most once, at the natural boundary: after the
  flow's checkpoint, before the full run.

## Reference files

Load each one only when the step that needs it runs.

- **`references/route-setup.md`**: credential probe, the route question,
  `ROUTE`/`CONTEXT` by format, the endpoint probe, the codex route, local
  models, the route and context flag menu, route failure lines. Every flow.
- **`references/providers.md`**: route table, probe recipes (`route_env`),
  per-format capability caveats. Read from route-setup whenever the
  endpoint is not a plain OpenAI-shaped host.
- **`references/prompt-files.md`**: the `--prompt` lint checklist and
  keeping a user's prompt out of git.
- **`references/epub-plan-mode.md`**: plan, classify, optional smoke, full
  run, halt/resume, deliver, failure lines. EPUB only.
- **`references/pdf-route.md`**: the whole PDF flow. PDF only.
- **`references/plain-formats.md`**: the default command per TXT, SRT and
  Markdown file, deliver, failure lines.
