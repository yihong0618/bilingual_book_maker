"""Offline stand-in for the `google` translator, for CLI contract tests.

Python imports `sitecustomize` at interpreter startup, so putting this
directory on PYTHONPATH swaps the translator *before* `make_book.py` runs.
The subprocess still exercises the real CLI: argument parsing, mode
selection, loader wiring, output writing — everything except the network
call, which is the one part those tests were never about.

Why it matters: the CLI suite used to translate through the public Google
endpoint, so a proxy hiccup (observed: HTTP 502) failed tests that have
nothing to do with translation quality, and a run with no network could not
pass at all. Live translation is covered by tests/test_integration.py, which
is explicitly about talking to real providers.
"""

from book_maker.translator import MODEL_DICT


class OfflineTranslator:
    """Deterministic, no network. Mirrors the surface the loaders call."""

    TRANSLATION_ERROR_MARKER = None

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False
        self.is_test = False

    def rotate_key(self):
        pass

    def set_deployment_id(self, *args, **kwargs):
        pass

    def set_interval(self, *args, **kwargs):
        pass

    def set_model_list(self, *args, **kwargs):
        pass

    def translate(self, text, *args, **kwargs):
        return f"[offline]{text}"

    def translate_list(self, texts, *args, **kwargs):
        return [self.translate(str(t)) for t in texts]

    def translate_and_split_lines(self, text, *args, **kwargs):
        return [self.translate(line) for line in str(text).splitlines()]


MODEL_DICT["google"] = OfflineTranslator
