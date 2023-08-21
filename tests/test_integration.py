import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture()
def test_book_dir() -> str:
    """Return test book dir"""
    # TODO: Can move this to conftest.py if there will be more unittests
    return str(Path(__file__).parent.parent / "test_books")


def test_google_translate_epub(test_book_dir, tmpdir):
    """Test google translate epub"""
    shutil.copyfile(
        os.path.join(test_book_dir, "Liber_Esther.epub"),
        os.path.join(tmpdir, "Liber_Esther.epub"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "Liber_Esther.epub"),
            "--test",
            "--test_num",
            "20",
            "--model",
            "google",
        ],
        env=os.environ.copy(),
    )

    assert os.path.isfile(os.path.join(tmpdir, "Liber_Esther_bilingual.epub"))
    assert os.path.getsize(os.path.join(tmpdir, "Liber_Esther_bilingual.epub")) != 0


def test_deepl_free_translate_epub(test_book_dir, tmpdir):
    """Test deepl free translate epub"""
    shutil.copyfile(
        os.path.join(test_book_dir, "Liber_Esther.epub"),
        os.path.join(tmpdir, "Liber_Esther.epub"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "Liber_Esther.epub"),
            "--test",
            "--test_num",
            "20",
            "--model",
            "deeplfree",
        ],
        env=os.environ.copy(),
    )

    assert os.path.isfile(os.path.join(tmpdir, "Liber_Esther_bilingual.epub"))
    assert os.path.getsize(os.path.join(tmpdir, "Liber_Esther_bilingual.epub")) != 0


def test_google_translate_epub_cli():
    pass


def test_google_translate_txt(test_book_dir, tmpdir):
    """Test google translate txt"""
    shutil.copyfile(
        os.path.join(test_book_dir, "the_little_prince.txt"),
        os.path.join(tmpdir, "the_little_prince.txt"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "the_little_prince.txt"),
            "--test",
            "--test_num",
            "20",
            "--model",
            "google",
        ],
        env=os.environ.copy(),
    )
    assert os.path.isfile(os.path.join(tmpdir, "the_little_prince_bilingual.txt"))
    assert os.path.getsize(os.path.join(tmpdir, "the_little_prince_bilingual.txt")) != 0


def test_google_translate_txt_batch_size(test_book_dir, tmpdir):
    """Test google translate txt with batch_size"""
    shutil.copyfile(
        os.path.join(test_book_dir, "the_little_prince.txt"),
        os.path.join(tmpdir, "the_little_prince.txt"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "the_little_prince.txt"),
            "--test",
            "--batch_size",
            "30",
            "--test_num",
            "20",
            "--model",
            "google",
        ],
        env=os.environ.copy(),
    )

    assert os.path.isfile(os.path.join(tmpdir, "the_little_prince_bilingual.txt"))
    assert os.path.getsize(os.path.join(tmpdir, "the_little_prince_bilingual.txt")) != 0


@pytest.mark.skipif(
    not os.environ.get("BBM_CAIYUN_API_KEY"),
    reason="No BBM_CAIYUN_API_KEY in environment variable.",
)
def test_caiyun_translate_txt(test_book_dir, tmpdir):
    """Test caiyun translate txt"""
    shutil.copyfile(
        os.path.join(test_book_dir, "the_little_prince.txt"),
        os.path.join(tmpdir, "the_little_prince.txt"),
    )
    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "the_little_prince.txt"),
            "--test",
            "--batch_size",
            "10",
            "--test_num",
            "100",
            "--model",
            "caiyun",
        ],
        env=os.environ.copy(),
    )

    assert os.path.isfile(os.path.join(tmpdir, "the_little_prince_bilingual.txt"))
    assert os.path.getsize(os.path.join(tmpdir, "the_little_prince_bilingual.txt")) != 0


@pytest.mark.skipif(
    not os.environ.get("BBM_DEEPL_API_KEY"),
    reason="No BBM_DEEPL_API_KEY in environment variable.",
)
def test_deepl_translate_txt(test_book_dir, tmpdir):
    shutil.copyfile(
        os.path.join(test_book_dir, "the_little_prince.txt"),
        os.path.join(tmpdir, "the_little_prince.txt"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "the_little_prince.txt"),
            "--test",
            "--batch_size",
            "30",
            "--test_num",
            "20",
            "--model",
            "deepl",
        ],
        env=os.environ.copy(),
    )

    assert os.path.isfile(os.path.join(tmpdir, "the_little_prince_bilingual.txt"))
    assert os.path.getsize(os.path.join(tmpdir, "the_little_prince_bilingual.txt")) != 0


@pytest.mark.skipif(
    not os.environ.get("BBM_DEEPL_API_KEY"),
    reason="No BBM_DEEPL_API_KEY in environment variable.",
)
def test_deepl_translate_srt(test_book_dir, tmpdir):
    shutil.copyfile(
        os.path.join(test_book_dir, "Lex_Fridman_episode_322.srt"),
        os.path.join(tmpdir, "Lex_Fridman_episode_322.srt"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "Lex_Fridman_episode_322.srt"),
            "--test",
            "--batch_size",
            "30",
            "--test_num",
            "2",
            "--model",
            "deepl",
        ],
        env=os.environ.copy(),
    )

    assert os.path.isfile(os.path.join(tmpdir, "Lex_Fridman_episode_322_bilingual.srt"))
    assert (
        os.path.getsize(os.path.join(tmpdir, "Lex_Fridman_episode_322_bilingual.srt"))
        != 0
    )


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="No OPENAI_API_KEY in environment variable.",
)
def test_openai_translate_epub_zh_hans(test_book_dir, tmpdir):
    shutil.copyfile(
        os.path.join(test_book_dir, "lemo.epub"),
        os.path.join(tmpdir, "lemo.epub"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "lemo.epub"),
            "--test",
            "--test_num",
            "5",
            "--language",
            "zh-hans",
        ],
        env=os.environ.copy(),
    )
    assert os.path.isfile(os.path.join(tmpdir, "lemo_bilingual.epub"))
    assert os.path.getsize(os.path.join(tmpdir, "lemo_bilingual.epub")) != 0


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="No OPENAI_API_KEY in environment variable.",
)
def test_openai_translate_epub_ja_prompt_txt(test_book_dir, tmpdir):
    shutil.copyfile(
        os.path.join(test_book_dir, "animal_farm.epub"),
        os.path.join(tmpdir, "animal_farm.epub"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "animal_farm.epub"),
            "--test",
            "--test_num",
            "5",
            "--language",
            "ja",
            "--model",
            "gpt3",
            "--prompt",
            "prompt_template_sample.txt",
        ],
        env=os.environ.copy(),
    )
    assert os.path.isfile(os.path.join(tmpdir, "animal_farm_bilingual.epub"))
    assert os.path.getsize(os.path.join(tmpdir, "animal_farm_bilingual.epub")) != 0


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="No OPENAI_API_KEY in environment variable.",
)
def test_openai_translate_epub_ja_prompt_json(test_book_dir, tmpdir):
    shutil.copyfile(
        os.path.join(test_book_dir, "animal_farm.epub"),
        os.path.join(tmpdir, "animal_farm.epub"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "animal_farm.epub"),
            "--test",
            "--test_num",
            "5",
            "--language",
            "ja",
            "--prompt",
            "prompt_template_sample.json",
        ],
        env=os.environ.copy(),
    )
    assert os.path.isfile(os.path.join(tmpdir, "animal_farm_bilingual.epub"))
    assert os.path.getsize(os.path.join(tmpdir, "animal_farm_bilingual.epub")) != 0


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="No OPENAI_API_KEY in environment variable.",
)
def test_openai_translate_srt(test_book_dir, tmpdir):
    shutil.copyfile(
        os.path.join(test_book_dir, "Lex_Fridman_episode_322.srt"),
        os.path.join(tmpdir, "Lex_Fridman_episode_322.srt"),
    )

    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            os.path.join(tmpdir, "Lex_Fridman_episode_322.srt"),
            "--test",
            "--test_num",
            "20",
        ],
        env=os.environ.copy(),
    )
    assert os.path.isfile(os.path.join(tmpdir, "Lex_Fridman_episode_322_bilingual.srt"))
    assert (
        os.path.getsize(os.path.join(tmpdir, "Lex_Fridman_episode_322_bilingual.srt"))
        != 0
    )
