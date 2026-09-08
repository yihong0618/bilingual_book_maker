"""The `.md` shape `--prompt` accepts, read natively.

The format is PromptDown's block form — https://github.com/btfranklin/promptdown
— and the credit for it is his. What is *not* here is the package: this tool
needs one shape of one format on one flag, and the reader below is shorter
than the import line's risk. The pinned release also read the block form only,
so nothing an operator could already use stops working.

What a file may say:

    # Any title                 (ignored: a name for the human reading it)

    ## System Message           (or `## Developer Message`, see below)

    Standing instructions.

    ## Style                    (optional; `--prompt`'s style section)

    Clipped, unsentimental.

    ## Conversation

    **User:**

    Translate {text} into {language}.

`## Developer Message` is read as the system message, silently. `developer`
is not a channel this tool has — `system` is the only operator channel on
every wire it speaks — and an operator whose file predates that ruling has
nothing to do about it, so there is nothing to say.

The table form of a conversation (a `| Role | Content |` grid) is refused
with the sentence that says what to write instead: it never worked here, and
a template read out of a table cell arrives with its newlines still spelled
`\\n`.
"""

import re

# `## Heading` — only the ones this reader knows are sections; anything else
# is refused by name rather than silently dropped, because a misspelled
# heading means the instruction in it was never sent. Up to three spaces of
# indentation, which is markdown's own rule for a heading and what an editor
# that reflows a list leaves behind.
_HEADING = re.compile(r"^ {0,3}##\s+(?P<name>.+?)\s*$")

# The same line indented past markdown's limit: a code block by the spec, and
# a mis-indented heading by every other reading. Refused when it names a
# section this reader knows, so the instruction under it cannot go missing in
# silence; left as content otherwise, because a `##` line inside a fenced
# example is the operator's own text.
_INDENTED_HEADING = re.compile(r"^ {4,}##\s+(?P<name>.+?)\s*$")

# The turns a conversation may open. Only these end the turn before them —
# the role name is a fixed set and the colon is required, because any other
# `**bold**` line is emphasis inside the operator's own template. A pattern
# that took any bold word for a role read `**Important**` as the start of a
# turn nobody asked for and dropped every line after it.
CONVERSATION_ROLES = ("user", "assistant", "system", "developer")

# `**User:**` or `**User**:`, with or without the turn's first line trailing.
_ROLE = re.compile(
    r"^\*\*\s*(?P<role>" + "|".join(CONVERSATION_ROLES) + r")\s*"
    r"(?::\s*\*\*|\*\*\s*:)\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

# Heading -> the `--prompt` section it fills.
SECTIONS = {
    "system message": "system",
    "developer message": "system",
    "style": "style",
    "conversation": "conversation",
}


class PromptFileError(ValueError):
    """A `.md` prompt file that cannot be read. Says what to write instead."""


def _blocks(body):
    """`{role: text}` for a conversation body, first turn of each role wins.

    Only a line matching `_ROLE` — one of `CONVERSATION_ROLES`, with its
    colon — closes the turn before it. Everything else is that turn's own
    text, bold or not.
    """
    turns = {}
    role = None
    lines = []

    def close():
        if role is not None and role not in turns:
            text = "\n".join(lines).strip()
            if text:
                turns[role] = text

    for line in body.splitlines():
        match = _ROLE.match(line.strip())
        if match:
            close()
            role = match.group("role").strip().lower()
            lines = [match.group("rest")]
        else:
            lines.append(line)
    close()
    return turns


def parse_prompt_markdown(text, source="the prompt file"):
    """A PromptDown block-form file as a `--prompt` config dict.

    Only the sections this tool has a channel for are returned, and only when
    they carry something: an empty `## Style` is the same as no style at all,
    which is what the shipped examples rely on.
    """
    sections = {}
    current = None
    # An editor's byte order mark sits on the first line, which in a file
    # with no title is the first heading — unstripped, it matched nothing and
    # the whole file parsed as no sections at all.
    for line in text.lstrip("\ufeff").splitlines():
        heading = _HEADING.match(line)
        if heading:
            name = heading.group("name").strip()
            current = SECTIONS.get(name.lower())
            if current is None:
                raise PromptFileError(
                    f"{source}: `## {name}` is not a section this reads. Use "
                    f"`## System Message`, `## Style` or `## Conversation`."
                )
            sections.setdefault(current, [])
            continue
        indented = _INDENTED_HEADING.match(line)
        if indented and indented.group("name").strip().lower() in SECTIONS:
            raise PromptFileError(
                f"{source}: `## {indented.group('name').strip()}` is indented "
                f"too far to be a heading — markdown reads four spaces as a "
                f"code block. Move it to the start of its line."
            )
        if current is not None:
            sections[current].append(line)

    body = {name: "\n".join(lines).strip() for name, lines in sections.items()}

    conversation = body.get("conversation", "")
    if "conversation" not in body:
        raise PromptFileError(
            f"{source}: no `## Conversation` section. Write the template in "
            f"block form -- a line reading `**User:**` followed by the "
            f"template, which must contain `{{text}}`."
        )
    if conversation.lstrip().startswith("|"):
        raise PromptFileError(
            f"{source}: the conversation is written as a table. Block form "
            f"only -- a line reading `**User:**` followed by the template, "
            f"which must contain `{{text}}`."
        )

    user = _blocks(conversation).get("user", "")
    if not user:
        raise PromptFileError(
            f"{source}: the conversation carries no user turn. Write a line "
            f"reading `**User:**` followed by the template, which must "
            f"contain `{{text}}`."
        )

    prompt = {"user": user}
    for section in ("system", "style"):
        if body.get(section):
            prompt[section] = body[section]
    return prompt
