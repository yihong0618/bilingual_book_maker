"""Provider-neutral pieces of a structured-JSON request.

Plan classification needs one thing from a translator: a JSON object whose
fields carry legal values. It does *not* need the endpoint to have applied a
JSON Schema. Everything here exists so that the weakest possible channel — a
single prompt in, a single string out — can still deliver that, and so the
ladder above it can tell "the endpoint refused the request" apart from "the
endpoint answered badly".

Nothing in this module imports a provider SDK; each translator maps its own
exceptions onto `RungRejected` and supplies its own rungs. It sits above both
layers rather than inside `translator/` so that the loader's classifier can
share the vocabulary without importing every provider package to get it.
"""

import json


class RungRejected(Exception):
    """This rung's request shape was refused; a lower rung may still work.

    Raised only for capability answers (a 400 on `response_format`, an
    unsupported parameter, a schema the endpoint will not compile). Auth,
    quota and transport errors must propagate untouched — descending a rung
    cannot fix them, and retrying every rung against a dead key wastes the
    user's money.
    """


class StructuredJSONFailed(Exception):
    """No rung produced a JSON object at all.

    Carries one note per rung so the terminal error names what each attempt
    actually did, instead of collapsing to "cannot produce JSON".
    """


def extract_json_object(text):
    """Pull the first balanced JSON object out of a model reply.

    Endpoints below strict decoding wrap answers in prose or ``` fences
    no matter how firmly the prompt says not to. Returns None when there
    is no parseable object — the caller decides what that means.
    """
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break  # try the next opening brace
        start = text.find("{", start + 1)
    return None


# Keys that mean "this object is a JSON Schema", not "this object is an answer".
# `schema` and `name`/`strict` are here because our request envelope is
# `{"name": …, "strict": true, "schema": {…}}` — a model echoing what it was
# sent may hand back either the whole envelope or the inner body.
_SCHEMA_KEYS = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "$schema",
    "schema",
    "title",
    "description",
    "strict",
    "name",
}


def unwrap_schema_echo(obj):
    """Return the answers when a model echoed the schema envelope around them.

    Measured on gpt-5.6-luna, 3 of 15 live trials: asked for "a JSON object of
    this exact shape" and handed a JSON *Schema*, the model replied with the
    envelope — `{"type": "object", "properties": {<the real answers>}, ...}` —
    and every answer sat one level too deep. That parses cleanly, so nothing
    downstream noticed; a whole page of paid-for verdicts was discarded in
    silence.

    Unwrapping is safe because it cannot invent an answer: if the model echoed
    the schema *definitions* rather than answers, the unwrapped values are
    property specs and the caller's lint rejects them exactly as before.
    """
    original = obj
    # At most two layers: the request envelope, then the schema body inside
    # it. Anything deeper is not an echo of what we sent.
    for _ in range(2):
        if not isinstance(obj, dict) or not obj or not set(obj) <= _SCHEMA_KEYS:
            break
        inner = obj.get("properties")
        if isinstance(inner, dict) and inner:
            return inner
        body = obj.get("schema")
        if not isinstance(body, dict) or not body:
            break
        obj = body
    return original


def _placeholder(spec):
    """A short `<...>` stand-in for a free-text field, from its description."""
    text = (spec.get("description") or "").split(",")[0].strip()
    if len(text) > 48:
        text = text[:48].rsplit(" ", 1)[0]
    return f"<{text.lower()}>" if text else "<...>"


def _example_value(spec):
    if not isinstance(spec, dict):
        return "<...>"
    if spec.get("enum"):
        # A placeholder, not enum[0]: a filled-in example of a *decision*
        # field would anchor every answer on whichever value came first.
        return "<one of: " + ", ".join(str(v) for v in spec["enum"]) + ">"
    kind = spec.get("type")
    if kind == "object":
        props = spec.get("properties") or {}
        return {name: _example_value(sub) for name, sub in props.items()}
    if kind == "array":
        return [_example_value(spec.get("items") or {})]
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return True
    return _placeholder(spec)


def render_schema_for_prompt(schema, example_properties=2):
    """Describe `schema` to a model that cannot be handed one.

    A filled-in *example instance* plus the constraints in prose — never the
    serialized schema. Handing a model a JSON Schema and asking for "this
    exact shape" is ambiguous, and the measured consequence is
    `unwrap_schema_echo`'s docstring: the model answers with the envelope.
    """
    body = schema.get("schema", schema)
    props = body.get("properties") or {}
    required = body.get("required") or list(props)

    example = {
        name: _example_value(spec)
        for name, spec in list(props.items())[:example_properties]
    }
    lines = [
        "Answer with a single JSON object — no prose, no markdown fences, "
        "no explanation.",
    ]
    if required:
        keys = ", ".join(json.dumps(k, ensure_ascii=False) for k in required)
        lines.append(
            f"It must have exactly {len(required)} top-level "
            f"key(s), one for each of: {keys}"
        )
    if example:
        lines.append("Shaped like this example, with your own answers:")
        lines.append(json.dumps(example, ensure_ascii=False, indent=2))

    for name, spec in _enum_constraints(props):
        allowed = ", ".join(json.dumps(v) for v in spec["enum"])
        lines.append(f'"{name}" must be exactly one of: {allowed}')

    lines.append(
        "Return the answers themselves — do not return this description, "
        'and do not wrap them in a "properties" object.'
    )
    return "\n".join(lines)


def _enum_constraints(props):
    """Every (field name, spec) carrying an enum, deduplicated by name.

    One level deep is enough: the classify schema pins its enum on the nested
    `verdict` field, and repeating the same constraint once per signature
    would bury the rest of the instructions.
    """
    seen = {}
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("enum"):
            seen.setdefault(name, spec)
        for sub_name, sub in (spec.get("properties") or {}).items():
            if isinstance(sub, dict) and sub.get("enum"):
                seen.setdefault(sub_name, sub)
    return list(seen.items())


def prompt_with_schema(prompt, schema):
    """The bottom rung's payload: the question plus a described schema."""
    return f"{prompt}\n\n{render_schema_for_prompt(schema)}"


def run_rungs(rungs, accept=None, on_reject=None):
    """Descend `rungs` until one answers acceptably.

    `rungs` is an ordered sequence of (name, callable) from most to least
    constrained. Returns the first object `accept` approves; failing that, the
    last object any rung parsed (the caller lints it — a partial answer is
    worth more than a discarded page); failing that, raises
    `StructuredJSONFailed` with one note per rung.

    Descent is failure-driven: the probe verdict only chooses where to start.
    `on_reject(name)` is called when a rung's request shape is refused, so the
    caller can stop paying for that rung on later requests.
    """
    notes = []
    fallback = None
    for name, rung in rungs:
        try:
            obj = rung()
        except RungRejected as exc:
            notes.append(f"{name}: request refused ({exc})")
            if on_reject is not None:
                on_reject(name)
            continue
        if obj is None:
            notes.append(f"{name}: no JSON object in the reply")
            continue
        if accept is None or accept(obj):
            return obj
        notes.append(f"{name}: JSON did not answer the request")
        fallback = obj
    if fallback is not None:
        return fallback
    raise StructuredJSONFailed("; ".join(notes) or "no rung was available")
