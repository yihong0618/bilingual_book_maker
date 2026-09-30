"""`--img-*` / `--classify-*`: which endpoint each extra step is asked on.

Packet F (260923). Two chains, one table each:

    image     --img-model  ->  provider img_model       ->  off
    classify  --classify-model  ->  provider classify_model  ->  the run's own

PIN (owner, 260923 22:30, docs/260923-feat-PDF_CHOICES_EXECUTION.md "Owner
rulings 22:30"): the image model is always designated explicitly; the run's
own model is never used for images by fallback, even when it reads them.
"""

from types import SimpleNamespace

import pytest

from book_maker import endpoints
from book_maker.endpoints import (
    CLASSIFY_ENDPOINT_UNSUPPORTED,
    IMG_ENDPOINT_UNSUPPORTED,
    JEV_DEFAULT_BASE,
    JEV_DEFAULT_MODEL,
    EndpointChoice,
    build_translator,
    resolve_classify_endpoint,
    resolve_image_endpoint,
    run_choice,
)
from book_maker.provider_loader import ProviderRoute

RUN_BASE = "https://api.openai.com/v1"
KEY_VARS = (
    "BBM_API_KEY",
    "OPENAI_API_KEY",
    "BBM_OPENAI_API_KEY",
    "JEV_API_KEY",
    "TYPESAFE_API_KEY",
    "CF_AIG_TOKEN",
    "IMG_KEY_VAR",
)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)


def _opts(**kw):
    base = dict(
        img_model=None,
        img_base_url=None,
        img_key=None,
        classify_model=None,
        classify_base_url=None,
        classify_key=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run(api_format="openai", base=RUN_BASE, key="sk-run"):
    return run_choice("gpt-run", base, key, api_format)


def _provider(**kw):
    route = ProviderRoute("openai", RUN_BASE, ["gpt-run"], "OPENAI_API_KEY")
    for name, value in kw.items():
        setattr(route, name, value)
    return route


# ------------------------------------------------------------------ image


class TestTheImageChain:
    def test_the_flag_wins_over_the_provider(self):
        choice = resolve_image_endpoint(
            _opts(img_model="cli-vision"), _run(), _provider(img_model="prov-vision")
        )
        assert (choice.model, choice.source) == ("cli-vision", "cli")

    def test_the_provider_answers_when_the_flag_is_absent(self):
        choice = resolve_image_endpoint(
            _opts(), _run(), _provider(img_model="prov-vision")
        )
        assert (choice.model, choice.source) == ("prov-vision", "provider")
        # no address of its own: the run's endpoint, the run's key
        assert (choice.api_base, choice.key, choice.api_format) == (
            RUN_BASE,
            "sk-run",
            "openai",
        )

    def test_none_turns_the_provider_s_model_off(self):
        assert (
            resolve_image_endpoint(
                _opts(img_model="none"), _run(), _provider(img_model="prov-vision")
            )
            is None
        )

    def test_nothing_named_is_off_even_when_the_run_model_reads_images(self):
        # PIN 260923 22:30: never the run's model by fallback. A run whose
        # own translator would pass the image probe still gets no image
        # choice without a flag or a provider field.
        class Verified:
            def vision_verdict(self, model=None):
                return "verified"

        run = _run()
        assert Verified().vision_verdict() == "verified"
        assert resolve_image_endpoint(_opts(), run, None) is None
        assert resolve_image_endpoint(_opts(), run, _provider()) is None

    def test_a_base_without_a_model_is_refused(self):
        with pytest.raises(SystemExit, match="no --img-model was given"):
            resolve_image_endpoint(_opts(img_base_url="https://gw/v1"), _run(), None)

    def test_an_anthropic_address_is_refused_in_the_lead_s_words(self):
        with pytest.raises(SystemExit) as err:
            resolve_image_endpoint(
                _opts(img_model="claude-x", img_base_url="https://api.anthropic.com"),
                _run(),
                None,
            )
        assert str(err.value) == IMG_ENDPOINT_UNSUPPORTED.format(
            base="https://api.anthropic.com", api_format="anthropic"
        )

    def test_a_run_route_without_an_image_channel_is_refused(self):
        with pytest.raises(SystemExit, match="resolves to the anthropic format"):
            resolve_image_endpoint(
                _opts(img_model="claude-x"),
                _run(api_format="anthropic", base="https://api.anthropic.com"),
                None,
            )

    def test_jev_cannot_see_images(self):
        with pytest.raises(SystemExit, match="resolves to the jev format"):
            resolve_image_endpoint(_opts(img_model="jev"), _run(), None)


class TestTheKeyRule:
    def test_same_base_takes_the_run_s_key(self):
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url=RUN_BASE + "/"), _run(), None
        )
        assert choice.key == "sk-run"

    def test_another_base_reads_its_format_s_environment(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="https://gw.example/v1"), _run(), None
        )
        assert (choice.api_base, choice.key) == ("https://gw.example/v1", "sk-env")

    def test_another_base_with_no_key_anywhere_stops(self):
        with pytest.raises(SystemExit, match="No API key"):
            resolve_image_endpoint(
                _opts(img_model="v", img_base_url="https://gw.example/v1"),
                _run(),
                None,
            )

    def test_a_local_base_needs_no_key(self):
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="http://localhost:11434/v1"),
            _run(),
            None,
        )
        assert choice.key == "local"

    def test_the_flag_key_wins(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="https://gw.example/v1", img_key="sk-f"),
            _run(),
            None,
        )
        assert choice.key == "sk-f"

    def test_the_provider_s_key_variable_at_the_entry_s_own_address(self, monkeypatch):
        # the run's key on the run's address first; the entry's variable
        # where the choice calls the address the entry names
        monkeypatch.setenv("IMG_KEY_VAR", "sk-var")
        entry = _provider(img_model="v", img_env_key="IMG_KEY_VAR")
        assert resolve_image_endpoint(_opts(), _run(), entry).key == "sk-run"
        assert resolve_image_endpoint(_opts(), _run(key=""), entry).key == "sk-var"
        own = _provider(
            img_model="v",
            img_base_url="https://vision.example/v1",
            img_env_key="IMG_KEY_VAR",
        )
        assert resolve_image_endpoint(_opts(), _run(), own).key == "sk-var"

    def test_a_gateway_entry_naming_the_jev_variable_is_an_explicit_binding(
        self, monkeypatch
    ):
        """PIN (lead 260924, Codex re-verify of packet F): the reserved
        names are read implicitly only at typesafe.ai; a provider entry
        that names `JEV_API_KEY` as its own gateway's key variable has
        named it explicitly for that address, and it is sent there -- the
        same rule as any other `*_env_key` at the entry's own address."""
        monkeypatch.setenv("JEV_API_KEY", "sk-gateway")
        gateway = _provider(
            classify_model="typesafe-ai/jev",
            classify_base_url="https://ai-gateway.example/typesafe",
            classify_env_key="JEV_API_KEY",
        )
        choice = resolve_classify_endpoint(_opts(), _run(), gateway)
        assert choice.api_format == "jev"
        assert choice.key == "sk-gateway"
        # without the entry's binding the same variable is not sent there
        unbound = _provider(
            classify_model="typesafe-ai/jev",
            classify_base_url="https://ai-gateway.example/typesafe",
        )
        with pytest.raises(SystemExit, match="not a typesafe.ai address"):
            resolve_classify_endpoint(_opts(), _run(), unbound)

    @pytest.mark.parametrize("chain", ["img", "classify"])
    def test_an_api_base_override_never_carries_the_entry_s_key(
        self, monkeypatch, chain
    ):
        """PIN (lead ruling 260923, Codex review finding 1): a key is bound
        to an address. The entry names RUN_BASE; `--api_base` moved the run
        to another host; the sidecar inherits that host, so the entry's
        variable must not be sent there."""
        monkeypatch.setenv("IMG_KEY_VAR", "sk-entry")
        entry = _provider(**{f"{chain}_model": "m", f"{chain}_env_key": "IMG_KEY_VAR"})
        resolve = (
            resolve_image_endpoint if chain == "img" else resolve_classify_endpoint
        )
        moved = "https://other-host.example/v1"
        # the run's own key belongs to the run's effective address
        choice = resolve(_opts(), _run(base=moved, key="sk-moved"), entry)
        assert (choice.api_base, choice.key) == (moved, "sk-moved")
        # with no run key there, the format's variable, never the entry's
        monkeypatch.setenv("OPENAI_API_KEY", "sk-format")
        choice = resolve(_opts(), _run(base=moved, key=""), entry)
        assert choice.key == "sk-format"
        monkeypatch.delenv("OPENAI_API_KEY")
        flag = "--img-key" if chain == "img" else "--classify-key"
        with pytest.raises(SystemExit, match=flag) as refused:
            resolve(_opts(), _run(base=moved, key=""), entry)
        assert "sk-entry" not in str(refused.value)

    def test_a_dry_run_resolves_no_key(self):
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="https://gw.example/v1"),
            _run(),
            None,
            with_key=False,
        )
        assert choice.key is None


# --------------------------------------------------------------- classify


class TestTheClassifyChain:
    def test_the_run_is_the_last_link(self):
        run = _run()
        assert resolve_classify_endpoint(_opts(), run, None) is run
        assert resolve_classify_endpoint(_opts(), run, _provider()) is run

    def test_the_provider_beats_the_run(self):
        choice = resolve_classify_endpoint(
            _opts(), _run(), _provider(classify_model="prov-clf")
        )
        assert (choice.model, choice.source, choice.api_base) == (
            "prov-clf",
            "provider",
            RUN_BASE,
        )

    def test_the_flag_beats_the_provider(self):
        choice = resolve_classify_endpoint(
            _opts(classify_model="cli-clf"),
            _run(),
            _provider(classify_model="prov-clf"),
        )
        assert (choice.model, choice.source) == ("cli-clf", "cli")

    def test_a_base_without_a_model_is_refused(self):
        with pytest.raises(SystemExit, match="no --classify-model was given"):
            resolve_classify_endpoint(
                _opts(classify_base_url="https://gw/v1"), _run(), None
            )

    def test_an_anthropic_address_is_refused_in_the_lead_s_words(self):
        with pytest.raises(SystemExit) as err:
            resolve_classify_endpoint(
                _opts(
                    classify_model="c", classify_base_url="https://api.anthropic.com"
                ),
                _run(),
                None,
            )
        assert str(err.value) == CLASSIFY_ENDPOINT_UNSUPPORTED.format(
            base="https://api.anthropic.com", api_format="anthropic"
        )

    def test_a_model_alone_keeps_the_run_s_route_whatever_it_is(self):
        # --plan-classify-model worked on every LLM route; its new name must
        # too. The classify model on an anthropic run is asked there.
        run = _run(api_format="anthropic", base="https://api.anthropic.com")
        choice = resolve_classify_endpoint(_opts(classify_model="c"), run, None)
        assert (choice.api_format, choice.api_base, choice.key) == (
            "anthropic",
            "https://api.anthropic.com",
            "sk-run",
        )

    @pytest.mark.parametrize("mode", ["agent", "all", "model", "auto"])
    def test_plan_classify_does_not_switch_the_chain_off(self, mode):
        # packet F (owner 260923 22:40): agent|all select the session backend
        # of whichever translator the chain picked; they are not a link in it
        options = _opts(classify_model="cli-clf")
        options.plan_classify = mode
        assert resolve_classify_endpoint(options, _run(), None).model == "cli-clf"


class TestJevIsAClassifyEndpoint:
    def test_the_literal_id_selects_the_default_model_and_host(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        choice = resolve_classify_endpoint(_opts(classify_model="jev"), _run(), None)
        assert (choice.api_format, choice.model, choice.api_base, choice.key) == (
            "jev",
            JEV_DEFAULT_MODEL,
            JEV_DEFAULT_BASE,
            "jev-secret",
        )

    def test_a_versioned_id_is_passed_through(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev-1.13.0"), _run(), None
        )
        assert (choice.api_format, choice.model) == ("jev", "jev-1.13.0")

    def test_a_gateway_s_namespaced_id_needs_its_key_named(self, monkeypatch):
        """PIN (lead ruling 260923, Codex finding 2): JEV_API_KEY is sent
        only to a typesafe.ai host. Vercel's AI Gateway serves the
        classifier as `typesafe-ai/jev`; there the key is passed."""
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        gateway = dict(
            classify_model="typesafe-ai/jev",
            classify_base_url="https://ai-gateway.vercel.sh/typesafe",
        )
        with pytest.raises(SystemExit, match="--classify-key") as refused:
            resolve_classify_endpoint(_opts(**gateway), _run(), None)
        assert "jev-secret" not in str(refused.value)
        # the action first, then why no variable is sent (smoke 260926)
        assert str(refused.value).endswith(
            "Pass --classify-key: https://ai-gateway.vercel.sh/typesafe is not "
            "a typesafe.ai address, so JEV_API_KEY and TYPESAFE_API_KEY are "
            "not sent there."
        )
        choice = resolve_classify_endpoint(
            _opts(**gateway, classify_key="vck-flag"), _run(), None
        )
        assert (choice.api_format, choice.model, choice.api_base, choice.key) == (
            "jev",
            "typesafe-ai/jev",
            "https://ai-gateway.vercel.sh/typesafe",
            "vck-flag",
        )

    def test_another_host(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        options = _opts(classify_model="jev", classify_base_url="https://jev.example/")
        with pytest.raises(SystemExit, match="not a typesafe.ai address"):
            resolve_classify_endpoint(options, _run(), None)
        options.classify_key = "k-flag"
        choice = resolve_classify_endpoint(options, _run(), None)
        assert (choice.api_base, choice.key) == ("https://jev.example", "k-flag")

    def test_a_typesafe_host_reads_the_jev_variable(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev", classify_base_url="https://eu.api.typesafe.ai"),
            _run(),
            None,
        )
        assert choice.key == "jev-secret"

    def test_a_jev_id_at_an_anthropic_address_is_refused(self, monkeypatch):
        """PIN (Codex finding 2): the address's format is inferred first."""
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        with pytest.raises(SystemExit) as refused:
            resolve_classify_endpoint(
                _opts(
                    classify_model="jev", classify_base_url="https://api.anthropic.com"
                ),
                _run(),
                None,
            )
        assert str(refused.value) == CLASSIFY_ENDPOINT_UNSUPPORTED.format(
            base="https://api.anthropic.com", api_format="anthropic"
        )

    def test_the_translation_key_is_never_sent_to_typesafe(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("BBM_API_KEY", "sk-bbm")
        with pytest.raises(SystemExit, match="JEV_API_KEY"):
            resolve_classify_endpoint(_opts(classify_model="jev"), _run(), None)

    def test_the_flag_key_and_the_provider_variable(self, monkeypatch):
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev", classify_key="k-flag"), _run(), None
        )
        assert choice.key == "k-flag"
        monkeypatch.setenv("IMG_KEY_VAR", "k-var")
        choice = resolve_classify_endpoint(
            _opts(),
            _run(),
            _provider(classify_model="jev", classify_env_key="IMG_KEY_VAR"),
        )
        assert (choice.key, choice.source) == ("k-var", "provider")


CF_BASE = "https://gateway.ai.cloudflare.com/v1/acct/gw/custom-typesafe"


def _sent_headers(base, key):
    """The headers one Jev request to `base` carries."""
    from book_maker.classifier import JevBackend, Question

    sent = []

    class Response:
        status_code = 200

        def json(self):
            return {"answers": {}, "usage": {}}

    def post(url, json, headers, timeout):
        sent.append(headers)
        return Response()

    JevBackend("jev-latest", key, base, post=post, log=print).ask(
        Question(
            prompt="P",
            candidates={"a": ("translate", "skip")},
            per_candidate={"a": "A"},
        )
    )
    ((headers,),) = [sent]
    return headers


class TestJevCompatibleEndpoints:
    """Packet J (owner 260924): "it should support non official endpoints
    ... accept 3 params but default to official jev". The same three
    classify flags reach TypeSafe's Jev and a gateway in front of it.

    PIN (owner 260929, docs/260929-fix-JEV_WIRE_TYPESAFE_ONLY.md): the
    third-party host rows, the `/classifier` path, the `-classifier` id
    rule and the keyless address are removed; they came from an unverified
    note, not from a primary source."""

    @pytest.mark.parametrize(
        "model, base, wire",
        [
            ("jev", "", True),
            ("jev-latest", "", True),
            ("jev-1.13.0", "", True),
            ("typesafe-ai/jev", "https://ai-gateway.vercel.sh/typesafe", True),
            ("JEV-Preview", "", True),
            ("m", "https://api.typesafe.ai", True),
            ("m", "https://eu.api.typesafe.ai", True),
            ("m", "https://self.example/v1/systemone/", True),
            # not the wire
            ("gpt-5.6-luna", "", False),
            ("jevons", "", False),
            ("jev/gpt-4o", "", False),
            ("classifier-7b", "", False),
            ("gpt-5.6-luna", "https://api.openai.com/v1", False),
            ("m", "https://nottypesafe.ai", False),
            # PIN (owner 260929): a `-classifier` id and a `/classifier`
            # path are no longer the Jev wire
            ("my-classifier", "https://self.example/v1", False),
            ("acme/tiny-classifier", "", False),
            ("m", "https://self.example/v1/classifier", False),
        ],
    )
    def test_the_detection_matrix(self, model, base, wire):
        assert endpoints.is_jev_wire(model, base) is wire

    @pytest.mark.parametrize(
        "base, url",
        [
            ("", "https://api.typesafe.ai/v1/systemone"),
            (
                "https://ai-gateway.vercel.sh/typesafe",
                "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
            ),
            (
                "https://jev.self.example/v1/systemone/",
                "https://jev.self.example/v1/systemone",
            ),
            # PIN (lead 260924, packet J fix round): `/systemone` after
            # /v1, which is not doubled
            ("https://jev.self.example/v1/", "https://jev.self.example/v1/systemone"),
            ("https://jev.self.example", "https://jev.self.example/v1/systemone"),
            ("https://api.typesafe.ai", "https://api.typesafe.ai/v1/systemone"),
            # Cloudflare AI Gateway, TypeSafe as custom provider `typesafe`:
            # the gateway forwards `/v1/systemone` to api.typesafe.ai
            (CF_BASE, CF_BASE + "/v1/systemone"),
        ],
    )
    def test_the_request_url_for_every_base_shape(self, base, url):
        assert endpoints.jev_request_url(base) == url

    def test_no_base_is_the_official_jev(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        choice = resolve_classify_endpoint(_opts(classify_model="jev"), _run(), None)
        assert endpoints.jev_request_url(choice.api_base) == (
            "https://api.typesafe.ai/v1/systemone"
        )
        assert choice.model == "jev-latest"

    def test_a_classifier_id_is_an_ordinary_chat_model(self, monkeypatch):
        """PIN (owner 260929): with the third-party rule gone, an id ending
        in `-classifier` is asked like any other model at its address."""
        options = _opts(
            classify_model="acme/tiny-classifier",
            classify_base_url="https://llm.example/v1",
            classify_key="k-flag",
        )
        choice = resolve_classify_endpoint(options, _run(), None)
        assert (choice.api_format, choice.api_base, choice.key) == (
            "openai",
            "https://llm.example/v1",
            "k-flag",
        )

    def test_the_gateway_needs_an_explicit_key(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        options = _opts(
            classify_model="typesafe-ai/jev",
            classify_base_url="https://ai-gateway.vercel.sh/typesafe",
        )
        with pytest.raises(SystemExit, match="--classify-key") as refused:
            resolve_classify_endpoint(options, _run(), None)
        for secret in ("jev-secret", "sk-openai", "sk-run"):
            assert secret not in str(refused.value)

    def test_a_systemone_address_without_a_key_is_refused(self, monkeypatch):
        """PIN (owner 260929): no Jev-wire address is keyless any more."""
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        with pytest.raises(SystemExit, match="--classify-key") as refused:
            resolve_classify_endpoint(
                _opts(
                    classify_model="m",
                    classify_base_url="https://jev.self.example/v1/systemone",
                ),
                _run(),
                None,
            )
        assert "jev-secret" not in str(refused.value)

    def test_cloudflare_needs_a_key_or_its_gateway_token(self, monkeypatch):
        """PIN (owner 260929): at a Cloudflare AI Gateway the TypeSafe
        variables are not read; the refusal names --classify-key and
        CF_AIG_TOKEN (a gateway that stores the key)."""
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        options = _opts(classify_model="jev", classify_base_url=CF_BASE)
        with pytest.raises(SystemExit, match="--classify-key") as refused:
            resolve_classify_endpoint(options, _run(), None)
        assert "CF_AIG_TOKEN" in str(refused.value)
        assert "jev-secret" not in str(refused.value)

    def test_cloudflare_with_a_gateway_token_and_no_key_sends_none(self, monkeypatch):
        """PIN (owner 260929): an authenticated gateway that stores the
        provider key (BYOK) is asked with no Authorization of ours."""
        monkeypatch.setenv("CF_AIG_TOKEN", "cf-gateway-token")
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev", classify_base_url=CF_BASE), _run(), None
        )
        assert (choice.api_format, choice.model, choice.api_base, choice.key) == (
            "jev",
            "jev-latest",
            CF_BASE,
            "",
        )
        choice = resolve_classify_endpoint(
            _opts(
                classify_model="jev", classify_base_url=CF_BASE, classify_key="k-flag"
            ),
            _run(),
            None,
        )
        assert choice.key == "k-flag"

    def test_the_gateway_token_is_sent_only_to_cloudflare(self, monkeypatch):
        """PIN (owner 260929): CF_AIG_TOKEN is bound to
        gateway.ai.cloudflare.com; no other Jev address receives it."""
        monkeypatch.setenv("CF_AIG_TOKEN", "cf-gateway-token")
        cf = _sent_headers(CF_BASE, "k-flag")
        assert cf["Authorization"] == "Bearer k-flag"
        assert cf["cf-aig-authorization"] == "Bearer cf-gateway-token"
        assert "Authorization" not in _sent_headers(CF_BASE, "")
        for base in (
            "https://api.typesafe.ai",
            "https://ai-gateway.vercel.sh/typesafe",
            "https://gateway.ai.cloudflare.com.example/v1/a/g/custom-typesafe",
        ):
            assert "cf-aig-authorization" not in _sent_headers(base, "k-flag")

    def test_the_gateway_token_needs_https(self, monkeypatch):
        """PIN (Codex review 260929): never sent over plain http."""
        monkeypatch.setenv("CF_AIG_TOKEN", "cf-gateway-token")
        plain = CF_BASE.replace("https://", "http://")
        assert endpoints.cf_aig_token(plain) == ""
        assert "cf-aig-authorization" not in _sent_headers(plain, "k-flag")

    def test_the_transport_follows_no_redirect_and_no_netrc(
        self, monkeypatch, tmp_path
    ):
        """PIN (Codex review 260929): the real Requests transport. A `.netrc`
        default entry adds no Authorization (BYOK stays keyless, our Bearer
        is not replaced), and a redirect is not followed."""
        import requests

        from book_maker.classifier import _requests_post

        netrc = tmp_path / "netrc"
        netrc.write_text("default login netrc-user password netrc-password\n")
        netrc.chmod(0o600)
        monkeypatch.setenv("NETRC", str(netrc))
        seen = []

        def send(session, prepared, **kw):
            seen.append((dict(prepared.headers), kw.get("allow_redirects")))
            response = requests.Response()
            response.status_code = 200
            return response

        monkeypatch.setattr(requests.Session, "send", send)
        _requests_post(CF_BASE, {}, {"cf-aig-authorization": "Bearer t"}, 5)
        _requests_post(CF_BASE, {}, {"Authorization": "Bearer k"}, 5)
        (byok, follow_a), (keyed, follow_b) = seen
        assert "Authorization" not in byok
        assert keyed["Authorization"] == "Bearer k"
        assert (follow_a, follow_b) == (False, False)

    def test_a_redirect_is_fatal_and_named(self, monkeypatch):
        from book_maker.classifier import JevBackend, JevFatal, Question

        monkeypatch.setenv("CF_AIG_TOKEN", "cf-gateway-token")

        class Response:
            status_code = 307
            text = ""
            headers = {"location": "https://elsewhere.example/v1/systemone"}

        backend = JevBackend(
            "jev-latest", "k", CF_BASE, post=lambda *a, **k: Response(), log=print
        )
        with pytest.raises(JevFatal, match="elsewhere.example"):
            backend._send({})

    def test_the_gateway_token_is_redacted(self, monkeypatch):
        from book_maker.classifier import JevBackend
        from book_maker.redaction import redact

        monkeypatch.setenv("CF_AIG_TOKEN", "cf-gateway-token")
        JevBackend("jev-latest", "", CF_BASE, post=None, log=print)
        assert "cf-gateway-token" not in redact("401: bad cf-gateway-token")

    def test_the_request_carries_the_key_as_a_bearer(self):
        from book_maker.classifier import JevBackend, Question

        sent = []

        class Response:
            status_code = 200

            def json(self):
                return {"answers": {}, "usage": {}}

        def post(url, json, headers, timeout):
            sent.append((url, headers))
            return Response()

        base = "https://jev.self.example/v1/systemone"
        choice = resolve_classify_endpoint(
            _opts(classify_model="m", classify_base_url=base, classify_key="k-flag"),
            _run(),
            None,
        )
        backend = JevBackend(
            choice.model, choice.key, choice.api_base, post=post, log=print
        )
        backend.ask(
            Question(
                prompt="P",
                candidates={"a": ("translate", "skip")},
                per_candidate={"a": "A"},
            )
        )
        ((url, headers),) = sent
        assert url == base
        assert headers["Authorization"] == "Bearer k-flag"


# ---------------------------------------------------------- the translator


def test_build_translator_carries_the_run_s_settings():
    from book_maker.translator.base_translator import PriceTable

    prices = PriceTable({"vision-x": {"input": 1, "output": 2}})
    options = SimpleNamespace(
        context_compact_at=None,
        no_context_compact=False,
        temperature=1.0,
        source_lang="auto",
        extra_body='{"foo": 1}',
        extra_headers='{"X-Gateway": "secret"}',
        price_table=prices,
        no_thinking=True,
        quiet=True,
    )
    same = EndpointChoice("vision-x", RUN_BASE, "sk", "openai", "cli", own_base=False)
    t = build_translator(same, options, "Simplified Chinese")
    assert t.model == "vision-x"
    assert t.usage.prices is prices
    assert t.no_thinking is True
    assert t.extra_body == {"foo": 1}
    assert t.extra_headers == {"X-Gateway": "secret"}

    other = EndpointChoice(
        "vision-x", "https://gw.example/v1", "sk", "openai", "cli", own_base=True
    )
    t = build_translator(other, options, "Simplified Chinese")
    # a header block is where a gateway's credential rides; it does not
    # travel to another host
    assert not t.extra_headers
    assert not t.extra_body
    assert t.no_thinking is True


# --------------------------------------------------------------------------
# The CLI's side: the old flag name, the mode it implies, the loader
# --------------------------------------------------------------------------


def _parsed(*argv):
    from book_maker.cli import normalize_options, parse_args

    options = parse_args(["--book_name", "b.epub", *argv])
    normalize_options(options)
    return options


def test_the_old_flag_name_is_the_same_option_and_the_new_one_wins():
    """PIN (owner 260923 22:40, packet F; lead 260923 on precedence):
    --plan-classify-model is the old name of --classify-model, kept for old
    command lines; typed together, the current name wins, and messages name
    the flag that was typed."""
    assert _parsed("--plan-classify-model", "old").classify_model == "old"
    assert (
        _parsed("--plan-classify-model", "old").classify_model_flag
        == "--plan-classify-model"
    )
    both = _parsed("--classify-model", "new", "--plan-classify-model", "old")
    assert both.classify_model == "new"
    assert both.classify_model_flag == "--classify-model"
    assert _parsed("--classify-model", "new").classify_model_flag == "--classify-model"


def test_the_old_flag_is_hidden_from_the_help():
    from book_maker.cli import build_parser

    parser = build_parser()
    text = parser.format_help()
    options = [line.split()[0] for line in text.splitlines() if line.startswith("  -")]
    assert "--classify-model" in options
    assert "--plan-classify-model" not in options
    # the help of the new flag names the old one (HELP_CLASSIFY_MODEL); read
    # off the action, because argparse wraps at a hyphen ("--plan-" /
    # "classify-model") wherever the terminal width puts the line break
    help_text = next(a.help for a in parser._actions if a.dest == "classify_model")
    assert "--plan-classify-model is the old name" in help_text


def test_the_endpoint_help_says_what_the_resolution_does():
    # PIN (packet H items 7-8, 260924; the lead's text, from the wiki's
    # second pass, docs/260923-docs-WIKI_MODERNIZE.md): no classification
    # step exists on the PDF route yet, Jev is named, and the key rule has
    # the provider entry's step and the address binding
    from book_maker.endpoints import (
        HELP_CLASSIFY_KEY,
        HELP_CLASSIFY_MODEL,
        HELP_IMG_KEY,
    )

    assert "structure decisions" not in HELP_CLASSIFY_MODEL
    assert "(the PDF route has no classification step yet)" in HELP_CLASSIFY_MODEL
    assert "'jev' asks TypeSafe's Jev classifier" in HELP_CLASSIFY_MODEL
    assert "by its URL in --classify-base-url." in HELP_CLASSIFY_MODEL
    assert "the provider entry's img_env_key" in HELP_IMG_KEY
    assert "never sent to an address it was not given for" in HELP_IMG_KEY
    assert "same default rule as --img-key" in HELP_CLASSIFY_KEY


def test_the_classify_endpoint_help_names_jev_urls_and_host_keys():
    # PIN (packet H item 13, 260924, the lead's text): a Jev-compatible
    # server is reached by --classify-base-url, and TypeSafe's key
    # variables are read only at typesafe.ai (docs/260923-feat-ENDPOINT_OVERRIDES_CLASSIFIER_JEV.md)
    from book_maker.endpoints import HELP_CLASSIFY_BASE_URL, HELP_CLASSIFY_KEY

    assert "OpenAI-compatible only" not in HELP_CLASSIFY_BASE_URL
    assert "or a Jev-compatible classifier's URL" in HELP_CLASSIFY_BASE_URL
    assert "a path ending in /systemone is used as is" in HELP_CLASSIFY_BASE_URL
    assert HELP_CLASSIFY_KEY.endswith(
        " JEV_API_KEY or TYPESAFE_API_KEY is read only at a typesafe.ai "
        "address; a Cloudflare AI Gateway's token, CF_AIG_TOKEN, only at "
        "gateway.ai.cloudflare.com."
    )


def test_a_classifier_implies_model_mode_on_an_epub_only():
    from book_maker.cli import resolve_classify_mode

    options = _parsed("--classify-model", "m")
    assert resolve_classify_mode(options, "epub") == ("model", False)
    # the PDF route's inner run translates Markdown: plan mode is not asked
    assert resolve_classify_mode(options, "md")[0] == "none"
    # agent and all stay what was typed
    agent = _parsed("--classify-model", "m", "--plan-classify", "agent")
    assert resolve_classify_mode(agent, "epub")[0] == "agent"


class _Meter:
    def __init__(self, line):
        self.line = line

    def summary(self):
        return self.line


def _loader():
    from book_maker.loader.epub_loader import EPUBBookLoader

    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = SimpleNamespace(usage_summary=lambda: "run line")
    loader.classify_translator = None
    return loader


def test_a_classifier_of_its_own_gets_its_own_usage_line(capsys):
    from book_maker.classifier import Classifier

    loader = _loader()
    loader._print_usage()
    assert capsys.readouterr().out.split("\n") == ["run line", ""]

    translator = SimpleNamespace(usage=_Meter("tokens: in 5"), model="c-model")
    loader.classify_translator = Classifier(
        translator, None, backends=[], base="http://c/v1", separate=True
    )
    loader._print_usage()
    out = capsys.readouterr().out.split("\n")
    assert out[:2] == ["run line", "Classifier (c-model at http://c/v1): tokens: in 5"]

    # the run's own classifier is on the run's meter: one line
    loader.classify_translator = Classifier(translator, None, backends=[])
    loader._print_usage()
    assert capsys.readouterr().out.split("\n") == ["run line", ""]


def test_plan_classification_goes_through_the_injected_classifier(monkeypatch):
    from book_maker.loader import epub_loader

    seen = {}

    def classify_plan(ledger, translator, model=None):
        seen["translator"] = translator
        return {}, []

    monkeypatch.setattr(epub_loader, "classify_plan", classify_plan)
    loader = _loader()
    loader.plan_classify_model = None
    injected = object()
    loader.classify_translator = injected
    ledger = SimpleNamespace(decide=lambda *a, **k: None)
    try:
        loader._classify_plan(ledger, None, "plan.json")
    except Exception:
        pass  # whatever follows the call is not this test's business
    assert seen["translator"] is injected
    loader.classify_translator = None
    try:
        loader._classify_plan(ledger, None, "plan.json")
    except Exception:
        pass
    assert seen["translator"] is loader.translate_model


# ---------------------------------------------------- --classify-min-confidence
#
# PIN (owner ruling 260924, "make it a cli flag and we give them evaled
# value, so it's their problem"; packet H item 14; the 0.95 measurement is
# docs/260924-eval-JEV_CONFIDENCE_THRESHOLD.md): the Jev gate is a flag,
# precedence flag > BBM_JEV_MIN_CONFIDENCE > JEV_MIN_CONFIDENCE, one
# validation rule for flag and variable. Provider entries get no field.


def _jev_choice():
    from book_maker.endpoints import EndpointChoice

    return EndpointChoice(
        "jev-latest", "https://api.typesafe.ai", "k", "jev", "cli", own_base=True
    )


def _jev_backend(options):
    from book_maker.endpoints import build_classifier

    classifier = build_classifier(_jev_choice(), None, options, "zh-hans")
    return classifier.backends["jev"]


def test_the_flag_reaches_the_jev_backend(monkeypatch):
    monkeypatch.delenv("BBM_JEV_MIN_CONFIDENCE", raising=False)
    options = _parsed("--classify-model", "jev", "--classify-min-confidence", "0.8")
    assert options.classify_min_confidence == 0.8
    assert _jev_backend(options).min_confidence == 0.8


def test_the_flag_beats_the_variable(monkeypatch):
    monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", "0.7")
    options = _parsed("--classify-model", "jev", "--classify-min-confidence", "0.8")
    assert _jev_backend(options).min_confidence == 0.8


def test_the_variable_beats_the_constant(monkeypatch):
    from book_maker.classifier import JEV_MIN_CONFIDENCE

    monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", "0.7")
    options = _parsed("--classify-model", "jev")
    assert options.classify_min_confidence is None
    assert _jev_backend(options).min_confidence == 0.7
    monkeypatch.delenv("BBM_JEV_MIN_CONFIDENCE")
    assert _jev_backend(options).min_confidence == JEV_MIN_CONFIDENCE == 0.95


@pytest.mark.parametrize("raw", ["1.5", "-0.1", "nan", "high"])
def test_an_out_of_range_flag_stops_at_parse_time(raw, capsys):
    # argument validation, so nothing is built and nothing is asked
    from book_maker.cli import parse_args

    with pytest.raises(SystemExit) as stopped:
        parse_args(["--book_name", "b.epub", "--classify-min-confidence", raw])
    assert stopped.value.code == 2
    err = " ".join(capsys.readouterr().err.split())
    assert (
        f"--classify-min-confidence must be a number from 0 to 1; got {raw!r}." in err
    )


def test_the_flag_and_the_variable_share_one_rule(monkeypatch):
    from book_maker.classifier import jev_min_confidence

    monkeypatch.setenv("BBM_JEV_MIN_CONFIDENCE", "1.5")
    with pytest.raises(SystemExit) as stopped:
        jev_min_confidence()
    assert str(stopped.value) == (
        "BBM_JEV_MIN_CONFIDENCE must be a number from 0 to 1; got '1.5'."
    )


def test_the_flag_help_is_the_leads_text():
    from book_maker.cli import build_parser
    from book_maker.endpoints import HELP_CLASSIFY_MIN_CONFIDENCE

    action = next(
        a for a in build_parser()._actions if a.dest == "classify_min_confidence"
    )
    assert action.help == HELP_CLASSIFY_MIN_CONFIDENCE
    assert action.metavar == "P" and action.default is None
    assert HELP_CLASSIFY_MIN_CONFIDENCE.startswith(
        "Confidence gate for a Jev-compatible classifier, 0 to 1:"
    )
    assert "Default 0.95, measured 260924" in HELP_CLASSIFY_MIN_CONFIDENCE
