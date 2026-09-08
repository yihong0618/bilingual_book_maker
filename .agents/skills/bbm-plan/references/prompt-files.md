# Prompt files: lint contract and git hygiene

Loaded from SKILL.md §1 once a candidate `prompt*` file is found **and the
user has said to use it**. Do not lint before asking.

## The contract (`book_maker/cli.py:parse_prompt_arg`)

- `.json`: an object with **only** the keys `user` (required), `system` and
  `style` (both optional). Any other key is rejected outright.
- The `user` template must contain the literal placeholder `{text}`.
  `{language}` is optional.
- `.txt` becomes the user template as-is, same `{text}` rule.
- `.md` is read as the PromptDown **block** form by the repo's own parser
  (no package involved): a `## System Message`, an optional `## Style`,
  and a `## Conversation` whose turns open with `**User:**` on its own
  line. The `| Role | Content |` table form is refused with an error
  naming the file and the block form. The repo's `prompt_md.prompt.md`
  sample is a valid block-form template to copy. `## Developer Message`
  is accepted as another name for the system section.
- Unknown `{placeholders}` in the `user` template are refused at parse
  time, before anything is paid; `{text}`, `{language}` and `{crlf}` are
  the full set. A literal brace is `{{` / `}}`.

```markdown
# Translation Prompt

## System Message

You are a professional translator. Keep the register of the original.

## Conversation

**User:**
Please translate the following text into {language}:

{text}
```

Fix or report lint problems before the paid run. The CLI would reject the
file at run start anyway, but a traceback after the user has already
approved the cost is the wrong place to learn about a missing `{text}`.

## Keep them out of git

Prompt files are the user's personal voice and often carry character names
or other personal terminology — same handling as `.env`. If the working directory is a
repo:

```bash
git check-ignore prompt.json .env
```

Add whatever comes back uncovered to `.git/info/exclude`. That file is
local-only; never edit the project's tracked `.gitignore` for this.

## Where the register goes

A style instruction belongs in the `style` section (or the `system`
message), stated once where a window starts — the run places it with the
standing instructions itself, never repeated per request. The `user`
template is sent for every unit — every word in it is paid for on every
request of the book. A user-written style also replaces the model's own
style notes in session handoffs.
