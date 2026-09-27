"""Whether an endpoint really reads the image in a request, asked once per model.

A second capability beside the schema probe in `capabilities.py`, and kept
apart from it on purpose: a schema verdict says nothing about images, and an
image the endpoint refuses (too large, wrong format, a text-only model) says
nothing about whether the same endpoint applies a JSON Schema to text. The
two never share a demotion counter.

The probe cannot be graded by the request being accepted. An endpoint in
front of a text-only model can drop the image part and answer the text part
alone, and every answer to a structured question would then look legal while
the evidence it was meant to rest on never arrived. So the probe shows the
model a short random string it can only read off the picture, never names it
anywhere else, and counts the endpoint as reading images only when the
string comes back.

Written against the OpenAI chat completions wire shape: content parts, an
`image_url` part carrying a `data:` URI.
"""

import base64
import io
import random
import re

from rich import print
from rich.markup import escape

from ..redaction import redact
from .capabilities import (
    PROBE_FATAL_ERRORS,
    PROBE_TRANSIENT_ERRORS,
    RUNG_REFUSAL_ERRORS,
    ProbeDeferred,
    _named_param,
)
from ..structured import render_schema_for_prompt


class VisionRequestFailed(Exception):
    """The endpoint refused the image in this request.

    Deliberately not a `RungRejected`: descending from `json_schema` to a
    plain prompt carries the same image and would be refused the same way,
    and counting the refusal against the rung would retire a schema rung that
    works for text. The caller decides what a request without its image
    evidence is worth.
    """


class QuestionTimedOut(Exception):
    """The question's deadline passed between two attempts at one request.

    Raised instead of sending again when a retry the loop would otherwise
    make (a refused optional field dropped, a refused `--no-thinking`
    spelling advanced) would start a request after the caller's deadline.
    Not a `RungRejected` (no rung was refused) and not a transport error
    (nothing timed out): the question is out of time, and the caller
    records it as such.
    """


# Readable at a glance and unambiguous in any font: no 0/O, no 1/I.
CHALLENGE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CHALLENGE_LENGTH = 4
CHALLENGE_SIZE = (200, 80)
CHALLENGE_FONT_SIZE = 48

# The lead's probe line, verbatim. It names no characters: the answer is on
# the picture only, so a model that never received the image cannot guess it
# (one in 32**4 by chance) and has a sanctioned way to say so.
IMAGE_PROBE_PROMPT = (
    "Reply with the characters shown in the image, in order, and nothing "
    "else. If there is no image in this message, reply NONE."
)

# Sent with every image request, probe and real alike, so the probe is asked
# on the shape the run uses. The cap is for reasoning models: measured
# 260923, gpt-5.6-luna answered the image probe with empty text at 200
# because the whole budget went to reasoning. An endpoint that refuses one of
# these is asked again without it (`refused_optional_param`).
VISION_REQUEST_PARAMS = {
    "max_completion_tokens": 2000,
    "reasoning_effort": "low",
}

# What a refusal of the image itself reads like, as opposed to one of the
# schema or of a parameter: a text-only model ("image_url is only supported
# by certain models"), a server that takes string content only ("content
# must be a string"), a malformed or oversized picture ("Invalid image"), a
# modality the model does not have.
IMAGE_REFUSAL_WORDS = re.compile(
    r"image|vision|multi-?modal|modalit|content[ _-]?part"
    r"|content must be (?:a )?string|content type",
    re.IGNORECASE,
)


def challenge_png(rng):
    """A PNG with `CHALLENGE_LENGTH` random characters on it, and the answer."""
    # Pillow comes with the pdf extra, and the image probe runs only on that
    # route: imported here so the base install (no Pillow) still loads the
    # translator.
    from PIL import Image, ImageDraw, ImageFont

    answer = "".join(rng.choice(CHALLENGE_ALPHABET) for _ in range(CHALLENGE_LENGTH))
    image = Image.new("RGB", CHALLENGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    draw.text(
        (CHALLENGE_SIZE[0] / 2, CHALLENGE_SIZE[1] / 2),
        answer,
        fill="black",
        font=ImageFont.load_default(size=CHALLENGE_FONT_SIZE),
        anchor="mm",
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue(), answer


def image_part(png_bytes, detail="high"):
    """An `image_url` content part carrying `png_bytes` as a data URI."""
    encoded = base64.b64encode(png_bytes).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}", "detail": detail},
    }


def text_part(text):
    return {"type": "text", "text": text}


def parts_with_schema(parts, schema):
    """`parts` plus the described schema as one more text part.

    The parts-aware twin of `structured.prompt_with_schema`: a new list, the
    caller's parts (the image among them) handed on as the same objects.
    """
    return [*parts, text_part(render_schema_for_prompt(schema))]


def names_image_refusal(error):
    """Whether a 400/422 is about the image or the content parts carrying it."""
    param = _named_param(str(error).lower())
    if param is not None and param.startswith("messages") and "content" in param:
        return True
    return bool(IMAGE_REFUSAL_WORDS.search(str(error)))


def refused_optional_param(error, sent):
    """Which of the optional fields in `sent` a 400 refuses, or None.

    Any complaint naming the field counts, value complaints included: these
    are conveniences (a reasoning budget, a cap sized for it), and a request
    without them is still the request.
    """
    text = str(error).lower()
    param = _named_param(text)
    for field in sent:
        if param == field or field in text:
            return field
    return None


def grade_image_reply(content, answer):
    """'verified' only when the characters on the picture came back."""
    if not content:
        return "unsupported"
    reply = re.sub(r"[^A-Z0-9]", "", content.strip().upper())
    return "verified" if reply == answer else "unsupported"


def probe_image(client, model, extra_body=None, *, rng=None, on_usage=None):
    """Ask `model` to read a random string off a picture: 'verified' | 'unsupported'.

    Same error taxonomy as `probe_structured_output`: no key, no access or no
    such model propagates; an outage raises `ProbeDeferred` so nothing is
    cached; a 400/422 naming the image or its content part is an answer
    ('unsupported'). A 400/422 about anything else is not an answer about
    images and propagates. Other failures (a 500 from a local server that
    chokes on the part) grade as unusable, as the schema probe grades them.

    `on_usage(completion)` meters the request; `rng` is for tests.
    """
    png, answer = challenge_png(rng or random.SystemRandom())
    content = [text_part(IMAGE_PROBE_PROMPT), image_part(png)]
    optional = dict(VISION_REQUEST_PARAMS)
    while True:
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                extra_body=extra_body or None,
                **optional,
            )
            break
        except PROBE_FATAL_ERRORS:
            raise
        except PROBE_TRANSIENT_ERRORS as e:
            raise ProbeDeferred(str(e)) from e
        except RUNG_REFUSAL_ERRORS as e:
            field = refused_optional_param(e, optional)
            if field is not None:
                # once per field: the loop ends when none is left to drop
                optional.pop(field)
                continue
            if names_image_refusal(e):
                _say_unsupported(model, f"image refused: {redact(e)}")
                return "unsupported"
            raise
        except Exception as e:
            _say_unsupported(model, f"request rejected: {redact(e)}")
            return "unsupported"

    if on_usage is not None:
        on_usage(completion)
    reply = completion.choices[0].message.content
    verdict = grade_image_reply(reply, answer)
    if verdict != "verified":
        shown = (reply or "").strip()[:40] or "an empty reply"
        _say_unsupported(model, f"answered {shown!r} to an image it could not read")
    return verdict


def _say_unsupported(model, reason):
    print(
        f"[yellow]ℹ '{escape(model)}' does not read images here "
        f"({escape(reason)})[/yellow]"
    )
