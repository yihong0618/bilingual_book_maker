"""Datalab Convert, the hosted engine of the `--to-epub` route.

The alternative to docling (`--pdf-parser datalab`): no models run here,
the PDF is sent to Datalab and its Markdown, equations as TeX and figures
as pictures, comes back into the bundle through the importer, which
rewrites Datalab's own forms (`datalab_markdown`). Retired 260921 when
docling became the only parser, restored 261026 as a second engine (owner:
"an alternative to local ocr and pdf processing stage ... datalab").

The whole reason this file is careful is that a submission costs money per
book. So:

* the job handle is written to disk the moment it exists, before the first
  poll, and a later run polls that handle instead of submitting again;
* a submission whose outcome is not known -- the connection died, or a 200
  came back without a job handle in it -- leaves a marker and refuses to
  resubmit until a person has checked the provider;
* a definite refusal (bad key, invalid request) is fatal immediately: no
  retry could turn a 401 into a conversion.

Captions and chart digitization stay off. Both have been measured producing
confident wrong content, and a translation would then carry it.
"""

import base64
import binascii
import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from .bundle import (
    EXTRACTION_JOB,
    SUBMIT_UNKNOWN,
    contained_path,
    sha256_file,
    zero_based_pages,
)
from .errors import PipelineError
from .importer import guard_source_replacement, import_markdown
from .messages import (
    DATALAB_KEY_MISSING,
    SOURCE_REASON_EXTRACT,
    SUBMISSION_UNKNOWN,
)
from .preflight import HTML_IMAGE, REFERENCE_IMAGE

STAGE = "extract"
PARSER = "datalab"
KEY_VARIABLE = "DATALAB_API_KEY"

API_URL = "https://www.datalab.to/api/v1/convert"

# Balanced mode, Markdown with page markers and real images; no invented
# captions, no chart digitization, no second chunks request for crops.
REQUEST = {
    "output_format": "markdown",
    "mode": "balanced",
    "paginate": "true",
    "disable_image_extraction": "false",
    "disable_image_captions": "true",
}

RUNNING_STATUSES = {"processing", "queued", "pending", "running", None}
FAILED_STATUSES = {"failed", "error"}

IMAGE_LINK = re.compile(r"!\[[^\]\n]*\]\(([^)\n]+)\)")


def request_for(page_range=None):
    """The form fields one conversion is sent with: what a bundle is
    compared against on a rerun (`stages._verdict`), so a changed request
    is extracted again rather than reused."""
    data = dict(REQUEST)
    # Datalab counts pages from 0; the route counts from 1.
    converted = zero_based_pages(page_range)
    if converted:
        data["page_range"] = converted
    return data


def extract_pdf(
    bundle,
    pdf_path,
    *,
    pandoc,
    api_key=None,
    session=None,
    api_url=API_URL,
    page_range=None,
    reason=SOURCE_REASON_EXTRACT,
    poll_interval=5.0,
    wait_timeout=3600.0,
    sleep=time.sleep,
    monotonic=time.monotonic,
):
    """Convert one PDF to Markdown in `bundle`, resuming an existing job.

    The key is `api_key`, else `$DATALAB_API_KEY`, asked for only here --
    a bundle that is reused never needs one. An edited `source.md` is
    refused before anything is sent (`guard_source_replacement`), and asked
    again by the import once the conversion is back.
    """
    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise PipelineError(f"no PDF at {pdf}", stage=STAGE)
    api_key = api_key or os.environ.get(KEY_VARIABLE, "").strip()
    if not api_key:
        raise PipelineError(DATALAB_KEY_MISSING, stage=STAGE)
    guard_source_replacement(bundle, STAGE, reason)
    session = session if session is not None else _requests_session()

    bundle.create()
    unknown = bundle.work_file(SUBMIT_UNKNOWN)
    if unknown.is_file():
        raise PipelineError(SUBMISSION_UNKNOWN, stage=STAGE)

    fingerprint = sha256_file(pdf)
    data = request_for(page_range)
    job = _existing_job(bundle, fingerprint, data)
    if job is None:
        bundle.set_stage(STAGE, "running")
        job = _submit(bundle, pdf, fingerprint, session, api_key, api_url, data)
    else:
        print(f"Resuming Datalab job {job.get('request_id') or job['check_url']}")

    try:
        payload = _poll(
            session,
            api_key,
            job,
            poll_interval=poll_interval,
            wait_timeout=wait_timeout,
            sleep=sleep,
            monotonic=monotonic,
        )
        payload = _with_result_body(session, payload)
        return _write_extraction(bundle, pdf, payload, job, pandoc, page_range, reason)
    except PipelineError:
        # The job handle stays on disk -- a failure here is a reason to look
        # at the job, never a reason to buy another one.
        bundle.set_stage(STAGE, "failed")
        raise


# -- submission ---------------------------------------------------------
def _requests_session():
    try:
        import requests
    except ImportError as err:  # pragma: no cover - dependency is declared
        raise PipelineError(
            f"the requests package is required for Datalab extraction: {err}",
            stage=STAGE,
        )
    # A plain session on purpose. A transparent retry adapter would repeat
    # the POST for us, which is the one thing that must never happen here.
    return requests.Session()


def _existing_job(bundle, fingerprint, data):
    """The job this bundle already paid for, if it is the one being asked for.

    A handle is only reusable when it was bought for the same parser, the
    same PDF and the same request -- a changed page selection above all.
    Polling the old job and filing its pages under the new request would
    label one selection with another's provenance.
    """
    path = bundle.work_file(EXTRACTION_JOB)
    if not path.is_file():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise PipelineError(f"unreadable extraction job handle: {err}", stage=STAGE)
    if job.get("parser", PARSER) != PARSER:
        # docling's record of a local extraction it finished: no job is in
        # flight, and this run is switching engines.
        return None
    if not job.get("check_url"):
        raise PipelineError("extraction job handle has no check URL", stage=STAGE)
    same = (
        job.get("parser", PARSER) == PARSER
        and job.get("pdf_sha256") == fingerprint
        and job.get("request") == data
    )
    if job.get("status") == "complete" and not same:
        # A finished conversion of something else (another PDF, another
        # page selection, another request): nothing is in flight, and this
        # run is asking for a new one.
        return None
    if job.get("pdf_sha256") and job["pdf_sha256"] != fingerprint:
        raise PipelineError(
            "this bundle holds a Datalab job for a different PDF; "
            "use a new output directory",
            stage=STAGE,
        )
    if job.get("request") != data:
        raise PipelineError(
            "this bundle holds a Datalab job submitted with different "
            "extraction options; use a new output directory",
            stage=STAGE,
        )
    return job


def _submit(bundle, pdf, fingerprint, session, api_key, api_url, data):
    headers = {"X-API-Key": api_key}
    # Written before the request goes out and removed once the handle is on
    # disk. An interrupt, a crash or a killed process inside that window
    # leaves it behind, and the next run finds a submission it cannot
    # account for instead of buying the book again.
    _mark_unknown(bundle, pdf, fingerprint, "submission was in flight", failed=False)
    try:
        with pdf.open("rb") as handle:
            response = session.post(
                api_url,
                headers=headers,
                files={"file": (pdf.name, handle, "application/pdf")},
                data=data,
                timeout=180,
            )
    except Exception as err:
        # No answer came back, so the provider may or may not have taken
        # the job -- and may or may not have charged for it.
        _mark_unknown(bundle, pdf, fingerprint, f"{type(err).__name__}: {err}")
        raise PipelineError(SUBMISSION_UNKNOWN, stage=STAGE)

    status = getattr(response, "status_code", 0)
    # 4xx is the provider saying it refused this request: the key is wrong,
    # the options are wrong. Nothing was queued and nothing was charged, so
    # the marker comes off and the failure is final.
    if status in (401, 403):
        _clear_unknown(bundle)
        raise PipelineError(
            f"Datalab rejected the API key (HTTP {status}): {_body(response)}",
            stage=STAGE,
        )
    if 400 <= status < 500:
        _clear_unknown(bundle)
        raise PipelineError(
            f"Datalab rejected the request (HTTP {status}): {_body(response)}",
            stage=STAGE,
        )
    if status >= 500 or status < 200 or status >= 300:
        # A 5xx is not evidence the job was refused: a gateway can fail
        # after the conversion was accepted behind it. Treated as unknown,
        # which means a person checks the provider before anything is sent
        # a second time.
        _mark_unknown(bundle, pdf, fingerprint, f"HTTP {status}: {_body(response)}")
        raise PipelineError(SUBMISSION_UNKNOWN, stage=STAGE)

    try:
        accepted = response.json()
    except Exception as err:
        _mark_unknown(bundle, pdf, fingerprint, f"unparseable submission reply: {err}")
        raise PipelineError(SUBMISSION_UNKNOWN, stage=STAGE)
    check_url = (accepted or {}).get("request_check_url")
    if not check_url:
        if accepted and accepted.get("success") is False:
            # A refusal in so many words: nothing was queued.
            _clear_unknown(bundle)
            raise PipelineError(
                f"Datalab refused the conversion: {accepted.get('error') or accepted}",
                stage=STAGE,
            )
        _mark_unknown(
            bundle, pdf, fingerprint, "submission reply carried no request_check_url"
        )
        raise PipelineError(SUBMISSION_UNKNOWN, stage=STAGE)

    job = {
        "parser": "datalab",
        "check_url": check_url,
        "request_id": _request_id(check_url),
        "pdf": str(pdf),
        "pdf_sha256": fingerprint,
        "request": data,
        "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    # Written before the first poll: an interrupt one line later must still
    # leave something that can be queried instead of resubmitted.
    _write_job(bundle, job)
    _clear_unknown(bundle)
    bundle.set_stage(STAGE, "running", job=job["request_id"])
    print(f"Datalab job {job['request_id'] or check_url}")
    return job


def _mark_unknown(bundle, pdf, fingerprint, detail, *, failed=True):
    bundle.work.mkdir(parents=True, exist_ok=True)
    bundle.work_file(SUBMIT_UNKNOWN).write_text(
        json.dumps(
            {
                "pdf": str(pdf),
                "pdf_sha256": fingerprint,
                "detail": detail,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": SUBMISSION_UNKNOWN,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if failed:
        bundle.set_stage(STAGE, "failed", reason="submission outcome unknown")


def _clear_unknown(bundle):
    bundle.work_file(SUBMIT_UNKNOWN).unlink(missing_ok=True)


def _write_job(bundle, job):
    bundle.work.mkdir(parents=True, exist_ok=True)
    bundle.work_file(EXTRACTION_JOB).write_text(
        json.dumps(job, indent=2) + "\n", encoding="utf-8"
    )


def _request_id(check_url):
    path = urlparse(check_url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else None


def _body(response):
    try:
        return (response.text or "").strip()[:300]
    except Exception:
        return "<no body>"


# -- polling ------------------------------------------------------------
def _poll(session, api_key, job, *, poll_interval, wait_timeout, sleep, monotonic):
    headers = {"X-API-Key": api_key}
    deadline = monotonic() + wait_timeout
    last = None
    while monotonic() < deadline:
        try:
            response = session.get(job["check_url"], headers=headers, timeout=90)
        except Exception as err:
            # Reaching the provider failed; the job is unaffected, so this
            # waits rather than giving up or resubmitting.
            print(f"Warning: polling Datalab failed ({err}); waiting to retry")
            sleep(poll_interval)
            continue
        status_code = getattr(response, "status_code", 0)
        if status_code in (401, 403):
            raise PipelineError(
                f"Datalab rejected the API key while polling (HTTP {status_code})",
                stage=STAGE,
            )
        if 400 <= status_code < 500:
            raise PipelineError(
                f"Datalab job {job.get('request_id')} cannot be read "
                f"(HTTP {status_code}): {_body(response)}",
                stage=STAGE,
            )
        if status_code >= 500:
            print(f"Warning: Datalab returned HTTP {status_code}; waiting to retry")
            sleep(poll_interval)
            continue
        try:
            last = response.json()
        except Exception as err:
            raise PipelineError(f"unreadable Datalab poll reply: {err}", stage=STAGE)
        status = last.get("status")
        if status == "complete":
            if last.get("success") is False:
                raise PipelineError(
                    f"Datalab conversion failed: {last.get('error') or last}",
                    stage=STAGE,
                )
            return last
        if status in FAILED_STATUSES:
            raise PipelineError(
                f"Datalab conversion failed: {last.get('error') or last}", stage=STAGE
            )
        if status not in RUNNING_STATUSES:
            raise PipelineError(
                f"Datalab returned unknown status {status!r}", stage=STAGE
            )
        sleep(poll_interval)
    raise PipelineError(
        f"Datalab job {job.get('request_id') or job['check_url']} did not finish "
        f"within {wait_timeout:g}s. The job handle is kept; run extract again to "
        f"resume polling it. Do not resubmit.",
        stage=STAGE,
    )


def _with_result_body(session, payload):
    """Follow `result_url` when the poll reply carried no document."""
    if payload.get("markdown"):
        return payload
    result_url = payload.get("result_url")
    if not result_url:
        raise PipelineError("Datalab completed without markdown content", stage=STAGE)
    try:
        response = session.get(result_url, timeout=180)
        body = response.json()
    except Exception as err:
        raise PipelineError(f"could not read the Datalab result: {err}", stage=STAGE)
    if not isinstance(body, dict):
        raise PipelineError("Datalab result URL returned a non-object", stage=STAGE)
    merged = dict(payload)
    merged.update(body)
    if not merged.get("markdown"):
        raise PipelineError("Datalab completed without markdown content", stage=STAGE)
    return merged


# -- writing ------------------------------------------------------------
def _write_extraction(bundle, pdf, payload, job, pandoc, page_range, reason):
    markdown = payload.get("markdown") or ""
    if not markdown.strip():
        raise PipelineError("Datalab returned empty Markdown", stage=STAGE)

    # The whole staging directory goes: a docling extraction's records
    # (`figures.json`, `docling.json`) left there would be read as this
    # bundle's on the next step.
    staging = bundle.work_file("extraction")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    md_path = staging / f"{pdf.stem}.md"
    md_path.write_text(markdown, encoding="utf-8")

    images = payload.get("images") or {}
    written = _write_images(images, staging)
    _verify_links(markdown, staging)

    report = import_markdown(
        bundle,
        md_path,
        pandoc=pandoc,
        origin=pdf,
        stage=STAGE,
        kind="pdf",
        reason=reason,
    )

    job = dict(job)
    job["status"] = "complete"
    _write_job(bundle, job)
    # Replaced outright, not merged: a docling extraction's settings and
    # structure fields describe a different engine's run.
    manifest = bundle.read_manifest()
    manifest["extraction"] = {}
    bundle.write_manifest(manifest)
    bundle.update_manifest(
        extraction={
            "provider": PARSER,
            "request": job.get("request"),
            "request_id": job.get("request_id"),
            "page_range": page_range,
            "page_range_sent": (job.get("request") or {}).get("page_range"),
            "page_numbering": "1-based input, 0-based request",
            "pdf": pdf.name,
            "pdf_sha256": job.get("pdf_sha256"),
            "images_written": written,
            "reported_page_count": payload.get("page_count"),
            "parse_quality_score": payload.get("parse_quality_score"),
            # What the provider said it charged, not an estimate of ours.
            "cost_cents": (payload.get("cost_breakdown") or {}).get("final_cost_cents"),
        }
    )
    bundle.add_limitations(
        [
            "Extraction reading order, headings and diacritics are not verified "
            "by this pipeline; inspect source.md before translating."
        ]
    )
    return report


def _write_images(images, directory):
    written = 0
    for name, data in (images or {}).items():
        destination = contained_path(directory, unquote(str(name)), what="image")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_decode(name, data))
        written += 1
    return written


def _decode(name, data):
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if not isinstance(data, str):
        raise PipelineError(
            f"image {name} came back as {type(data).__name__}, not base64",
            stage=STAGE,
        )
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as err:
        raise PipelineError(f"image {name} is not valid base64: {err}", stage=STAGE)


def _verify_links(markdown, directory):
    """Every local image the Markdown names must be a file we just wrote."""
    for pattern, kind in (
        (REFERENCE_IMAGE, "reference-style image"),
        (HTML_IMAGE, "HTML image"),
    ):
        if pattern.search(markdown):
            raise PipelineError(
                f"Datalab returned a {kind}, which this pipeline does not carry",
                stage=STAGE,
            )
    missing = []
    for match in IMAGE_LINK.finditer(markdown):
        target = match.group(1).strip()
        if target.startswith("<") and target.endswith(">"):
            target = target[1:-1]
        target = target.split(None, 1)[0] if target else ""
        if not target or re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", target):
            continue
        path = contained_path(directory, unquote(target.split("#", 1)[0]), what="image")
        if not path.is_file():
            missing.append(target)
    if missing:
        raise PipelineError(
            "Datalab payload lacks linked images: " + ", ".join(sorted(set(missing))),
            stage=STAGE,
        )
