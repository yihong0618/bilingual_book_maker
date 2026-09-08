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
# heading means the instruction in it was never sent.
_HEADING = re.compile(r"^##\s+(?P<name>.+?)\s*$")

# `**User:**`, with or without the turn's first line trailing it.
_ROLE = re.compile(
    r"^\*\*\s*(?P<role>[A-Za-z][A-Za-z ]*?)\s*:?\s*\*\*:?\s*(?P<rest>.*)$"
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
    """`{role: text}` for a conversation body, first turn of each role wins."""
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
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            name = heading.group("name").strip().lower()
            current = SECTIONS.get(name)
            if current is None:
                raise PromptFileError(
                    f"{source}: `## {heading.group('name').strip()}` is not a "
                    f"section this reads. Use `## System Message`, `## Style` "
                    f"or `## Conversation`."
                )
            sections.setdefault(current, [])
            continue
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
