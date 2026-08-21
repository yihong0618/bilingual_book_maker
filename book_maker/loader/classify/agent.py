"""Agent entry: no API call — a coding agent rules, the CLI just sets it up.

The pipeline writes the plan JSON (signature rows carry samples, counts and
char totals) and prints a self-contained instruction block for the user to
paste into a coding-agent session, then stops before translating. Stopping
is the point: translating first would spend the whole book before anyone
looked at the questions.

The plan JSON is the contract — the agent edits `action` fields with its
ordinary file tools. There is deliberately no editing CLI to learn: the
run-time action lint and the coverage gate already validate the result,
and a second interface would be one more thing to keep in sync.
"""


def build_agent_prompt(plan_path, book_path, rerun_command, unresolved=None):
    """The paste-able block printed when a run stops for want of decisions.

    Self-contained on purpose: it must work in a session that has never
    seen this repository and has no skill installed. `unresolved` scopes the
    instruction to exactly the rows still open — after a partial model run
    that is a handful of rows, not the whole book, and saying "decide
    everything" would invite re-deciding what was already decided.
    """
    if unresolved:
        listed = "\n".join(f"      {key}" for key in unresolved[:40])
        more = (
            f"\n      ... and {len(unresolved) - 40} more"
            if len(unresolved) > 40
            else ""
        )
        scope = (
            f"\n{len(unresolved)} row(s) are still undecided — a classifier "
            f"could not settle them:\n{listed}{more}\n"
        )
    else:
        scope = ""
    return f"""\
{scope}\
────────────────────────────────────────────────────────────────────────
Paste the block below into a coding-agent session (Claude Code, Codex, …)
────────────────────────────────────────────────────────────────────────
I am translating an EPUB with bilingual_book_maker in plan mode. The
translation plan is at:

    {plan_path}

It lists every HTML tag signature in {book_path}. Rows whose "action" is
null are the plan's open questions: decide each one. The translate run
will refuse to start while any null remains — there is no default to
fall back on, and answering none of them is not an option.

How to read a signature row:
  key         scope + tag + classes, e.g. "block:p.calibre_13".
              "block:" is a block of text of that shape. "inline:" is
              markup inside a sentence — skipping it leaves its text in
              place, untranslated, and splits the sentence around it, so
              skip one only when it is genuinely apparatus.
  units       how many occurrences the book has
  chars       how much text they hold in total (pct = share of the book)
  mean_chars  average occurrence length — running heads and labels run short
  samples     up to 5 real excerpts, spread across the whole book
  parents     (inline rows) which blocks it appears inside
  conditional_css  CSS that hides it only on some devices — evidence that
              it may be device-specific duplicate apparatus, not a verdict
  action      null (decide!), "translate", or "skip"
  decided_by  who decided: "llm", "agent", "user"
  content_type  what the decider called this text

What to do, for every null row:
1. Read its samples and name what the text is — prose, verse, dialogue,
   heading, caption, running head, page or line number, manuscript
   sigla, cross-reference label, publisher boilerplate, decorative
   marker. Name first, then rule: deciding before naming invites
   rationalizing. Put that name in "content_type".
2. Set "action" to "skip" for text a reader does not want translated:
   running heads, page or line numbers, sigla, cross-reference labels,
   boilerplate, decorative markers.
3. Set "action" to "translate" for real book content: prose, verse,
   dialogue, headings, captions. When the samples do not settle it,
   choose "translate" — translating something unnecessary is cheap,
   losing content is not.
4. If a row's samples show more than one kind of content, choose
   "translate": the verdict applies to every occurrence of that
   signature, and there is no per-occurrence override.
5. Set "decided_by" to "agent" on every row you answer.
6. Edit only "action", "decided_by" and "content_type". Never touch
   "key", "book_sha256" or "schema_version": they are what keeps the
   plan matched to this book.
7. Want more evidence than the samples give? Read the book's own markup:
   unzip -p "{book_path}" <file-from-the-plan> | grep -n '<class>'

You may also change a non-null row if its samples convince you, but the
nulls are the required work.

When you are done, tell me and I will run:

    {rerun_command}

which picks up the edited plan automatically. A coverage gate and an
action lint will reject a malformed plan, so mistakes fail loudly rather
than silently dropping text.
────────────────────────────────────────────────────────────────────────"""
