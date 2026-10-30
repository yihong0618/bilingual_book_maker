"""The Datalab engine's protocol behaviour, against a stand-in transport.

Restored 261026 with the adapter (`--pdf-parser datalab`, the hosted
alternative to docling). No request leaves this process. What is asserted is the part that costs
money if it is wrong: one POST per book, a job handle on disk before the
first poll, polling that resumes instead of resubmitting, and an ambiguous
submission that refuses to try again on its own.
"""

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import PNG, pandoc_or_skip  # noqa: E402

from book_maker.pipeline import datalab  # noqa: E402
from book_maker.pipeline.bundle import (  # noqa: E402
    EXTRACTION_JOB,
    SUBMIT_UNKNOWN,
    Bundle,
)
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import SUBMISSION_UNKNOWN  # noqa: E402

CHECK_URL = "https://www.datalab.to/api/v1/convert/job-abc123"

MARKDOWN = """# A Converted Chapter

{0}------------------------------------------------

Prose that came out of the converter.

![](_page_1_Figure_2.jpeg)

More prose after the figure.
"""


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", raises=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._raises = raises

    def json(self):
        if self._raises:
            raise self._raises
        return self._payload


class FakeSession:
    """Scripted replies, and a count of everything that was sent."""

    def __init__(self, post=None, get=None):
        self._post = list(post or [])
        self._get = list(get or [])
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        reply = self._post.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        reply = self._get.pop(0) if self._get else self._get_default()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _get_default(self):
        raise AssertionError("an unscripted GET was made")


def accepted():
    return FakeResponse(payload={"success": True, "request_check_url": CHECK_URL})


def completed(markdown=MARKDOWN, images=None, **extra):
    payload = {
        "status": "complete",
        "success": True,
        "markdown": markdown,
        "images": (
            images
            if images is not None
            else {"_page_1_Figure_2.jpeg": base64.b64encode(PNG).decode("ascii")}
        ),
        "page_count": 1,
        "cost_breakdown": {"final_cost_cents": 4.5},
    }
    payload.update(extra)
    return FakeResponse(payload=payload)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


@pytest.fixture
def bundle(tmp_path):
    return Bundle(tmp_path / "bundle").create()


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


def extract(bundle, pdf, session, pandoc, **kwargs):
    kwargs.setdefault("sleep", lambda seconds: None)
    return datalab.extract_pdf(
        bundle,
        pdf,
        api_key="dl-test-key",
        pandoc=pandoc,
        session=session,
        poll_interval=0,
        **kwargs,
    )


# --------------------------------------------------------------------------
def test_conversion_writes_the_bundle_and_records_what_was_charged(bundle, pdf, pandoc):
    session = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, session, pandoc)

    assert len(session.post_calls) == 1
    assert bundle.source.is_file()
    assert bundle.raw_source.read_text(encoding="utf-8").startswith(
        "# A Converted Chapter"
    )
    assert (bundle.assets / "_page_1_Figure_2.jpeg").read_bytes() == PNG
    assert "assets/_page_1_Figure_2.jpeg" in bundle.source.read_text(encoding="utf-8")

    manifest = bundle.read_manifest()
    assert manifest["stages"]["extract"]["status"] == "completed"
    assert manifest["source"]["kind"] == "pdf"
    extraction = manifest["extraction"]
    assert extraction["provider"] == "datalab"
    assert extraction["request_id"] == "job-abc123"
    assert extraction["cost_cents"] == 4.5
    assert extraction["request"]["mode"] == "balanced"
    # Neither invented captions nor chart digitization.
    assert extraction["request"]["disable_image_captions"] == "true"
    assert "extras" not in extraction["request"]
    assert "dl-test-key" not in bundle.manifest_path.read_text(encoding="utf-8")


def test_a_polling_timeout_keeps_the_job_and_resumes_without_a_second_post(
    bundle, pdf, pandoc
):
    session = FakeSession(post=[accepted()])
    with pytest.raises(PipelineError) as timed_out:
        extract(bundle, pdf, session, pandoc, wait_timeout=0)
    assert "Do not resubmit" in timed_out.value.detail
    assert len(session.post_calls) == 1
    assert session.get_calls == []

    job = json.loads(bundle.work_file(EXTRACTION_JOB).read_text(encoding="utf-8"))
    assert job["check_url"] == CHECK_URL
    assert job["request_id"] == "job-abc123"

    # A second run polls the handle it already has.
    session._get = [
        FakeResponse(payload={"status": "processing"}),
        completed(),
    ]
    extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert len(session.post_calls) == 1, "a resumed run resubmitted the book"
    assert len(session.get_calls) == 2
    assert bundle.source.is_file()


def test_an_ambiguous_submission_never_resubmits_on_its_own(bundle, pdf, pandoc):
    session = FakeSession(post=[OSError("connection reset")])
    with pytest.raises(PipelineError) as unknown:
        extract(bundle, pdf, session, pandoc)
    assert unknown.value.detail == SUBMISSION_UNKNOWN
    assert len(session.post_calls) == 1
    assert bundle.work_file(SUBMIT_UNKNOWN).is_file()
    assert bundle.stage_status("extract") == "failed"

    again = FakeSession(post=[accepted()], get=[completed()])
    with pytest.raises(PipelineError) as still_unknown:
        extract(bundle, pdf, again, pandoc)
    assert still_unknown.value.detail == SUBMISSION_UNKNOWN
    assert again.post_calls == [], "an unknown submission was repeated"


def test_a_two_hundred_without_a_job_handle_is_ambiguous(bundle, pdf, pandoc):
    session = FakeSession(post=[FakeResponse(payload={"success": True})])
    with pytest.raises(PipelineError) as unknown:
        extract(bundle, pdf, session, pandoc)
    assert unknown.value.detail == SUBMISSION_UNKNOWN
    assert bundle.work_file(SUBMIT_UNKNOWN).is_file()


@pytest.mark.parametrize("status", [401, 403])
def test_a_rejected_key_is_fatal_and_leaves_no_job(bundle, pdf, pandoc, status):
    session = FakeSession(
        post=[FakeResponse(status_code=status, payload={}, text="forbidden")]
    )
    with pytest.raises(PipelineError) as refused:
        extract(bundle, pdf, session, pandoc)
    assert "rejected the API key" in refused.value.detail
    assert not bundle.work_file(EXTRACTION_JOB).exists()
    assert not bundle.work_file(SUBMIT_UNKNOWN).exists()


def test_an_invalid_request_is_fatal(bundle, pdf, pandoc):
    session = FakeSession(
        post=[FakeResponse(status_code=422, payload={}, text="bad page_range")]
    )
    with pytest.raises(PipelineError) as refused:
        extract(bundle, pdf, session, pandoc, page_range="900-901")
    assert "rejected the request" in refused.value.detail
    assert not bundle.work_file(EXTRACTION_JOB).exists()


def test_a_refusal_in_a_two_hundred_reply_clears_the_marker(bundle, pdf, pandoc):
    refusal = FakeResponse(payload={"success": False, "error": "too many pages"})
    with pytest.raises(PipelineError) as refused:
        extract(bundle, pdf, FakeSession(post=[refusal]), pandoc)
    assert "too many pages" in refused.value.detail
    assert not bundle.work_file(SUBMIT_UNKNOWN).exists()

    # Nothing was queued, so the next run submits after the fix.
    again = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, again, pandoc)
    assert len(again.post_calls) == 1


def test_a_docling_extraction_record_does_not_block_a_switch(bundle, pdf, pandoc):
    bundle.work.mkdir(parents=True, exist_ok=True)
    bundle.work_file(EXTRACTION_JOB).write_text(
        json.dumps({"parser": "docling", "pdf": str(pdf), "device": "cpu"}),
        encoding="utf-8",
    )
    session = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, session, pandoc)
    assert len(session.post_calls) == 1
    assert bundle.read_manifest()["extraction"]["provider"] == "datalab"


def test_a_terminal_job_failure_is_reported(bundle, pdf, pandoc):
    session = FakeSession(
        post=[accepted()],
        get=[FakeResponse(payload={"status": "failed", "error": "page 3 is broken"})],
    )
    with pytest.raises(PipelineError) as failed:
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert "page 3 is broken" in failed.value.detail
    assert not bundle.source.exists()


def test_an_unknown_status_is_reported_rather_than_waited_out(bundle, pdf, pandoc):
    session = FakeSession(
        post=[accepted()], get=[FakeResponse(payload={"status": "on-fire"})]
    )
    with pytest.raises(PipelineError) as failed:
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert "unknown status" in failed.value.detail


def test_an_image_that_is_not_base64_is_an_error(bundle, pdf, pandoc):
    session = FakeSession(
        post=[accepted()],
        get=[completed(images={"_page_1_Figure_2.jpeg": "not base64 at all!!"})],
    )
    with pytest.raises(PipelineError) as failed:
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert "not valid base64" in failed.value.detail
    assert not bundle.source.exists()


def test_a_referenced_image_that_never_arrived_is_an_error(bundle, pdf, pandoc):
    session = FakeSession(post=[accepted()], get=[completed(images={})])
    with pytest.raises(PipelineError) as failed:
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert "lacks linked images" in failed.value.detail
    assert not bundle.source.exists()


def test_an_empty_conversion_is_an_error(bundle, pdf, pandoc):
    session = FakeSession(
        post=[accepted()], get=[completed(markdown="   \n", images={})]
    )
    with pytest.raises(PipelineError) as failed:
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert "empty Markdown" in failed.value.detail


def test_an_image_path_that_escapes_the_bundle_is_refused(bundle, pdf, pandoc):
    session = FakeSession(
        post=[accepted()],
        get=[
            completed(
                markdown="# T\n\n![](../../escape.jpeg)\n",
                images={"../../escape.jpeg": base64.b64encode(PNG).decode("ascii")},
            )
        ],
    )
    with pytest.raises(PipelineError) as failed:
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert "escapes the bundle" in failed.value.detail


def test_a_finished_job_for_another_pdf_is_replaced_by_a_new_one(
    bundle, pdf, pandoc, tmp_path
):
    """Nothing is in flight: a new PDF in the same bundle is a new conversion."""
    session = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, session, pandoc, wait_timeout=60)

    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.7\n%different\n")
    again = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, other, again, pandoc, wait_timeout=60)
    assert len(again.post_calls) == 1
    job = json.loads(bundle.work_file(EXTRACTION_JOB).read_text(encoding="utf-8"))
    assert job["pdf"] == str(other)


def test_an_unfinished_job_for_another_pdf_is_refused(bundle, pdf, pandoc, tmp_path):
    session = FakeSession(post=[accepted()])
    with pytest.raises(PipelineError):
        extract(bundle, pdf, session, pandoc, wait_timeout=0)

    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.7\n%different\n")
    with pytest.raises(PipelineError) as refused:
        extract(bundle, other, FakeSession(), pandoc, wait_timeout=60)
    assert "different PDF" in refused.value.detail


def test_a_missing_key_fails_before_anything_is_sent(bundle, pdf, pandoc, monkeypatch):
    monkeypatch.delenv("DATALAB_API_KEY", raising=False)
    session = FakeSession()
    with pytest.raises(PipelineError) as refused:
        datalab.extract_pdf(bundle, pdf, api_key="", pandoc=pandoc, session=session)
    assert "DATALAB_API_KEY" in refused.value.detail
    assert session.post_calls == []


def test_a_failure_while_polling_marks_the_stage_failed(bundle, pdf, pandoc):
    session = FakeSession(
        post=[accepted()],
        get=[FakeResponse(payload={"status": "failed", "error": "broken"})],
    )
    with pytest.raises(PipelineError):
        extract(bundle, pdf, session, pandoc, wait_timeout=60)
    assert bundle.stage_status("extract") == "failed"
    # The handle is still there so the job can be looked at, not rebought.
    assert bundle.work_file(EXTRACTION_JOB).is_file()


def test_the_page_selection_is_converted_to_datalab_page_indices(bundle, pdf, pandoc):
    """The harness counts pages from 1; Datalab counts them from 0."""
    session = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, session, pandoc, wait_timeout=60, page_range="1-20,25")

    _, kwargs = session.post_calls[0]
    assert kwargs["data"]["page_range"] == "0-19,24"
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["page_range"] == "1-20,25"
    assert extraction["page_range_sent"] == "0-19,24"


@pytest.mark.parametrize("bad", ["0-3", "5-2", "abc", "-4"])
def test_an_impossible_page_selection_is_refused_before_anything_is_sent(
    bundle, pdf, pandoc, bad
):
    session = FakeSession()
    with pytest.raises(PipelineError):
        extract(bundle, pdf, session, pandoc, page_range=bad)
    assert session.post_calls == []


def test_a_job_for_a_different_page_selection_is_not_reused(bundle, pdf, pandoc):
    session = FakeSession(post=[accepted()])
    with pytest.raises(PipelineError):
        extract(bundle, pdf, session, pandoc, wait_timeout=0, page_range="1-5")

    again = FakeSession(post=[accepted()], get=[completed()])
    with pytest.raises(PipelineError) as refused:
        extract(bundle, pdf, again, pandoc, wait_timeout=60, page_range="6-10")
    assert "different extraction options" in refused.value.detail
    assert again.post_calls == [], "a second selection resubmitted the book"


def test_a_server_error_is_unknown_not_a_refusal(bundle, pdf, pandoc):
    """A 5xx can come from a gateway in front of an accepted conversion."""
    session = FakeSession(
        post=[FakeResponse(status_code=502, payload={}, text="bad gateway")]
    )
    with pytest.raises(PipelineError) as unknown:
        extract(bundle, pdf, session, pandoc)
    assert unknown.value.detail == SUBMISSION_UNKNOWN
    assert bundle.work_file(SUBMIT_UNKNOWN).is_file()

    again = FakeSession(post=[accepted()], get=[completed()])
    with pytest.raises(PipelineError):
        extract(bundle, pdf, again, pandoc, wait_timeout=60)
    assert again.post_calls == []


def test_an_interrupt_during_submission_is_unknown_too(bundle, pdf, pandoc):
    """The window between sending the POST and writing the handle.

    A process killed there leaves no job file, and 'no job file' must not
    read as 'nothing was submitted'.
    """

    class Interrupting(FakeSession):
        def post(self, url, **kwargs):
            self.post_calls.append((url, kwargs))
            raise KeyboardInterrupt("operator stopped the run")

    session = Interrupting()
    with pytest.raises(BaseException):
        extract(bundle, pdf, session, pandoc)
    assert bundle.work_file(SUBMIT_UNKNOWN).is_file()
    assert not bundle.work_file(EXTRACTION_JOB).exists()

    again = FakeSession(post=[accepted()], get=[completed()])
    with pytest.raises(PipelineError) as unknown:
        extract(bundle, pdf, again, pandoc, wait_timeout=60)
    assert unknown.value.detail == SUBMISSION_UNKNOWN
    assert again.post_calls == []


def test_the_key_is_read_from_the_environment(bundle, pdf, pandoc, monkeypatch):
    monkeypatch.setenv("DATALAB_API_KEY", "dl-env-key")
    session = FakeSession(post=[accepted()], get=[completed()])
    datalab.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        session=session,
        poll_interval=0,
        sleep=lambda seconds: None,
    )
    _, kwargs = session.post_calls[0]
    assert kwargs["headers"] == {"X-API-Key": "dl-env-key"}
    assert "dl-env-key" not in bundle.manifest_path.read_text(encoding="utf-8")


def test_the_conversion_is_normalized_and_the_raw_reply_kept(bundle, pdf, pandoc):
    """Datalab's page separator becomes the route's page marker in
    source.md; source.raw.md is Datalab's own text."""
    session = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, session, pandoc)
    source = bundle.source.read_text(encoding="utf-8")
    assert "<!-- page 1 -->" in source
    assert "------------" not in source
    assert "{0}------------------------------------------------" in (
        bundle.raw_source.read_text(encoding="utf-8")
    )
    assert bundle.read_manifest()["source"]["normalized"] == "datalab"


def test_a_docling_extraction_before_it_leaves_nothing_behind(bundle, pdf, pandoc):
    """Another engine's records would be read as this bundle's: the
    figure records under .work/extraction and docling's manifest fields."""
    staging = bundle.work_file("extraction")
    staging.mkdir(parents=True)
    (staging / "figures.json").write_text("[]", encoding="utf-8")
    bundle.update_manifest(extraction={"provider": "docling", "ocr": True})

    session = FakeSession(post=[accepted()], get=[completed()])
    extract(bundle, pdf, session, pandoc)
    assert not (staging / "figures.json").exists()
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["provider"] == "datalab"
    assert "ocr" not in extraction
