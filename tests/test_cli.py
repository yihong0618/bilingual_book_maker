from book_maker.cli import get_book_type


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"


import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"


def _run(tmp_path, *args):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(src),
            "--model",
            "google",
            *args,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return proc, src.parent / (src.stem + "_plan.json")


def test_plan_classify_implies_plan_mode(tmp_path):
    # choosing how to classify is choosing plan mode; requiring
    # --translate-tags auto as well would be a papercut
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == 0
    assert plan.exists()
    assert "Paste the block below" in proc.stdout


def test_no_classify_flag_keeps_legacy_tag_mode(tmp_path):
    # the flag is opt-in: without it nothing about today's behavior changes
    proc, plan = _run(tmp_path, "--test", "--test_num", "1")
    assert proc.returncode == 0
    assert not plan.exists()


def test_explicit_tag_list_loses_to_the_classify_flag(tmp_path):
    proc, plan = _run(tmp_path, "--plan-classify", "agent", "--translate-tags", "p")
    assert proc.returncode == 0
    assert plan.exists()
    assert "ignoring --translate-tags p" in proc.stdout


def test_agent_mode_rejects_a_classifier_model(tmp_path):
    proc, _ = _run(
        tmp_path, "--plan-classify", "agent", "--plan-classify-model", "gpt-4o"
    )
    assert proc.returncode == 1
    assert "cannot be combined" in proc.stdout


def test_classify_model_flag_implies_model_mode(tmp_path):
    # naming a classifier is asking for model mode; it must not silently
    # sit in none mode doing nothing
    proc, plan = _run(
        tmp_path,
        "--plan-classify-model",
        "no-such-model",
        "--test",
        "--test_num",
        "1",
    )
    # google translator has no structured_json, and an explicitly chosen
    # classifier that cannot run must block rather than degrade
    assert proc.returncode == 1
    assert "--plan-classify-model" in proc.stdout
