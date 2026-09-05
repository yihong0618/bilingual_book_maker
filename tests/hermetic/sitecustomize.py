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

import os
import sys
from pathlib import Path

# The checkout this file belongs to, ahead of everything else. `sitecustomize`
# is imported during interpreter startup, before the script's own directory is
# on `sys.path`, so a plain `import book_maker` here resolves against whatever
# is installed site-wide. With a stale `pip install -e` in the environment that
# is somebody else's checkout: the CLI subprocess then runs *that* code, and a
# test asserting this branch's behaviour fails — or worse, passes — for reasons
# nothing in the branch explains. Seen 260903, a whole worktree tested against
# another one's translator.
_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import book_maker
from book_maker.translator import FORMAT_DICT, ROUTE_DICT

if not Path(book_maker.__file__).resolve().is_relative_to(_REPO):
    # `site` imports this file inside `except Exception`, so raising here is
    # printed and swallowed and the run continues against the wrong code.
    # Leave by a door that cannot be caught.
    sys.stderr.write(
        f"hermetic harness loaded {book_maker.__file__}, outside {_REPO}\n"
    )
    os._exit(1)


class OfflineTranslator:
    """Deterministic, no network. Mirrors the surface the loaders call.

    It stands in for a fully context-capable route, not for the real
    `google` engine: the CLI's capability gates are part of the contract
    these tests exercise, and a stand-in that declared no context would
    make every context flag untestable offline.
    """

    TRANSLATION_ERROR_MARKER = None
    SUPPORTS_SESSION_CONTEXT = True
    SUPPORTS_PARALLEL_CONTEXT = True
    # it has none of the Batch API methods, and the CLI's refusal of --batch
    # on a route without them is part of the contract these tests exercise
    SUPPORTS_BATCH_API = False
    # It builds no request, like the real `google` engine it stands in for,
    # so --extra_body / --extra_headers have nothing to join. The CLI's
    # refusal of them on such a route is part of the contract these tests
    # exercise.
    SUPPORTS_REQUEST_EXTRAS = False
    context_paragraph_limit = 3

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False
        self.is_test = False
        # As Base.__init__ does: the key is a secret from the moment the
        # translator holds it, and every sink the CLI prints goes through
        # redact(); a stand-in that skipped this would let a CLI test pass
        # while the real run printed the key.
        from book_maker.redaction import remember

        key = kwargs.get("key", args[0] if args else None)
        if key:
            remember(*str(key).split(","))

    def rotate_key(self):
        pass

    @property
    def model_name(self):
        # the same answer the real translators give: the model, else the
        # api_format / provider key this stand-in is registered under
        from book_maker.translator.base_translator import service_name

        return getattr(self, "model", None) or service_name(self)

    def set_model_list(self, model_list=(), *args, **kwargs):
        # echoed so a CLI test can see which models the run selected —
        # including the one no --model named
        print(f"offline model list: {list(model_list)}")

    def translate(self, text, *args, **kwargs):
        return f"[offline]{text}"

    def translate_list(self, texts, *args, **kwargs):
        return [self.translate(str(t)) for t in texts]

    def translate_and_split_lines(self, text, *args, **kwargs):
        return [self.translate(line) for line in str(text).splitlines()]


class _OfflineClassifierSession:
    """A conversation that answers the plan's turns, offline.

    Every signature is book content, for the reason `structured_json` gives:
    what these tests need is a plan run that completes.
    """

    def budget(self):
        return 8000

    def start(self, trunk):
        print("offline classifier session started")

    def ask(self, text):
        units = text.count("occurrence(s)")
        return ",".join(["translate"] * units)


class OfflineLLM(OfflineTranslator):
    """An LLM-shaped endpoint: it has a capability verdict and can be asked
    questions. Only the openai route gets this — `google` and the other MT
    engines answer neither, which is what several CLI tests are about.
    """

    def _probe_verdict(self, model=None):
        # The real probe is a paid request against a real endpoint;
        # BBM_FAKE_PROBE stands in for its verdict so a CLI test can pick
        # which side of `--plan-classify auto` it is testing. The echo lets a
        # test assert the probe was *not* made.
        print("probe asked")
        verdict = os.environ.get("BBM_FAKE_PROBE") or False
        if verdict == "unavailable":
            # Asking for the verdict is also what confirms the endpoint will
            # serve the model, so this is where that refusal reaches the CLI.
            from book_maker.translator.capabilities import ModelUnavailable

            raise ModelUnavailable(
                "This endpoint served none of the models ['ghost-model']."
            )
        return verdict

    # An SDK-backed request, like the openai route this stands in for.
    SUPPORTS_REQUEST_EXTRAS = True

    def set_request_extras(self, extra_body=None, extra_headers=None):
        # echoed so a CLI test can see what the run would have sent — header
        # names only, for the reason the CLI redacts them: a header is where
        # a credential goes, and a test log is still a log
        from book_maker.redaction import redact, remember

        remember(*(extra_headers or {}).values())
        names = sorted(extra_headers or {})
        print(
            f"offline extras: body={redact(str(extra_body or {}))} "
            f"header names={names}"
        )

    def supports_structured_json(self):
        return True

    def classify_session(self, model=None):
        """The classifier conversation the real openai route offers.

        Its presence is the contract: an endpoint with no JSON verdict now
        plans over a plain session instead of dropping to tag mode, and a
        stand-in without one would make that untestable offline. It answers
        the format exactly; what a *bad* reply does is
        tests/test_session_classify.py's subject.
        """
        return _OfflineClassifierSession()

    def structured_json(self, prompt, schema, model=None, accept=None):
        # Every signature is book content: what the classifier does with a
        # real model is tests/test_structured_classify.py's subject, and what
        # these tests need is a plan run that completes.
        return {
            key: {"verdict": "translate", "content_type": "prose"}
            for key in schema["schema"]["required"]
        }


FORMAT_DICT["google"] = OfflineTranslator
# The openai format too: it is the default route, so the contract tests for
# what a bare command does have no other way to stay offline.
FORMAT_DICT["openai"] = OfflineLLM
ROUTE_DICT["orcarouter"] = OfflineLLM
