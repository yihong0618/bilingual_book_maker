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


def build_agent_prompt(plan_path, book_path, rerun_command):
    """The paste-able block printed when --plan-classify agent stops.

    Self-contained on purpose: it must work in a session that has never
    seen this repository and has no skill installed.
    """
    return f"""\
────────────────────────────────────────────────────────────────────────
Paste the block below into a coding-agent session (Claude Code, Codex, …)
────────────────────────────────────────────────────────────────────────
I am translating an EPUB with bilingual_book_maker in plan mode. The
translation plan is at:

    {plan_path}

It lists every HTML tag signature in {book_path}. Please decide, for each
signature, whether its text should be translated or kept as is.

How to read a signature row:
  signature   the tag and class the text lives in, e.g. "p.calibre_13"
  units       how many blocks in the book have this shape
  chars       how much text they hold in total
  samples     up to 5 real excerpts — judge from these
  action      "translate" (default) or "skip"
  decided_by  present when a model, not you, chose the action

What to do:
1. Read the file and look at each signature's samples.
2. Set "action" to "skip" for text a reader does not want translated:
   running heads, page or line numbers, manuscript sigla, cross-reference
   labels, publisher boilerplate, decorative markers.
3. Leave "action" as "translate" for real book content: prose, verse,
   dialogue, headings, captions. When the samples do not settle it, leave
   it alone — translating something unnecessary is cheap, losing content
   is not.
4. Edit only "action" fields. Never touch "book_sha256" or
   "schema_version": they are what keeps the plan matched to this book.
5. Want more evidence than the samples give? Read the book's own markup:
   unzip -p "{book_path}" <file-from-the-plan> | grep -n '<class>'

When you are done, tell me and I will run:

    {rerun_command}

which picks up the edited plan automatically. A coverage gate and an
action lint will reject a malformed plan, so mistakes fail loudly rather
than silently dropping text.
────────────────────────────────────────────────────────────────────────"""
