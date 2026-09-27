"""The translation identity is what was typed, not the whole namespace.

PIN (Codex, packet H review 260924, record
docs/260924-fix-HELP_COMPAT_README_DRIFT_AND_CLASSIFY_MIN_CONFIDENCE.md):
`option_identity` used to walk the whole parsed namespace, so every new
parser field re-fingerprinted every finished PDF bundle -- a completed one
would be translated and paid for again, an interrupted one would refuse to
resume with SETTINGS_CHANGED. Now a value equal to the parser's default is
not written down, so a flag added later leaves old bundles alone; and the
image and classify fields, which shape the extraction (hashed through
`source`) or nothing at all on the Markdown run, are left out even when
typed.
"""

import argparse

from book_maker.pipeline.translate import (
    SIDECAR_FIELDS,
    option_identity,
    parse_bbm_options,
)

COMMAND = [
    "--model",
    "gpt-5.6-luna",
    "--language",
    "ja",
    "--test",
]  # zh-hans is the default


def _names(identity):
    return {name for name, _value in identity}


def test_the_sidecar_fields_are_not_in_the_identity():
    options = parse_bbm_options(
        COMMAND
        + [
            "--img-model",
            "gpt-5.6-luna",
            "--classify-model",
            "jev",
            "--classify-min-confidence",
            "0.7",
        ]
    )
    names = _names(option_identity(options))
    assert names.isdisjoint(SIDECAR_FIELDS)
    assert {"model", "language", "test"} <= names


def test_a_bundle_from_before_the_sidecar_flags_still_matches():
    # the namespace a pre-flag parser produced: the same command, none of
    # the sidecar fields present at all
    now = parse_bbm_options(COMMAND)
    before = argparse.Namespace(
        **{k: v for k, v in vars(now).items() if k not in SIDECAR_FIELDS}
    )
    assert option_identity(before) == option_identity(now)


def test_typing_a_sidecar_flag_does_not_change_the_identity():
    plain = parse_bbm_options(COMMAND)
    typed = parse_bbm_options(COMMAND + ["--classify-min-confidence", "0.7"])
    assert option_identity(plain) == option_identity(typed)


def test_the_identity_holds_only_what_differs_from_the_defaults():
    names = _names(option_identity(parse_bbm_options(COMMAND)))
    assert names == {"model", "language", "test"}


def test_a_default_typed_out_is_the_same_run():
    plain = parse_bbm_options(COMMAND)
    typed = parse_bbm_options(COMMAND + ["--test_num", "10", "--source_lang", "auto"])
    assert option_identity(plain) == option_identity(typed)


def test_a_field_the_parser_does_not_know_is_kept():
    # a manifest from a later parser, read by this one: the unknown field is
    # not silently dropped as if it were a default
    options = parse_bbm_options(COMMAND)
    setattr(options, "future_flag", "on")
    assert ("future_flag", "on") in option_identity(options)


def test_a_bundle_from_a_parser_that_lacked_a_field_still_matches():
    # the namespace an earlier parser produced for the same command: one of
    # today's fields missing entirely (it is at its default in the new one)
    now = parse_bbm_options(COMMAND)
    before = argparse.Namespace(
        **{k: v for k, v in vars(now).items() if k != "ocr_replace_layer"}
    )
    assert option_identity(before) == option_identity(now)
