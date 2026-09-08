"""The subtitle loader: its own prompt, and `--prompt` on top of it.

The pair below traces to upstream PR #247, which borrowed a design from
jesselau76/srt-gpt-translator (MIT) and hard-coded the prompt that came with
it. Hard-coded is the part this fixes: the loader took `prompt_config` and
threw it away, so `--prompt` was accepted on an srt book and did nothing.

The two strings are pinned here because they are what a default run sends,
and because the timeline clause is the whole reason this loader has a prompt
of its own — a general translation prompt rewrites the block number and the
timestamps along with the line, and the file then does not parse as srt.
"""

import pytest

from book_maker.loader.srt_loader import DEFAULT_PROMPT_CONFIG, SRTBookLoader

SRT = """1
00:00:01,000 --> 00:00:02,000
Hello there.

2
00:00:03,000 --> 00:00:04,000
General Kenobi.
"""


class FakeTranslator:
    """Records what the loader built it with."""

    def __init__(self, key, language, **kwargs):
        self.key = key
        self.language = language
        self.kwargs = kwargs

    def translate(self, text, needprint=True):  # pragma: no cover - unused here
        return text


@pytest.fixture
def srt_file(tmp_path):
    path = tmp_path / "subs.srt"
    path.write_text(SRT, encoding="utf-8")
    return path


def _loader(srt_file, prompt_config=None):
    return SRTBookLoader(
        str(srt_file),
        FakeTranslator,
        "k",
        False,
        "simplified chinese",
        prompt_config=prompt_config,
    )


class TestTheDefaultPrompt:
    def test_the_two_strings_are_what_a_default_run_sends(self, srt_file):
        kwargs = _loader(srt_file).translate_model.kwargs
        assert kwargs["prompt_sys_msg"] == "You are a srt subtitle file translator."
        assert kwargs["prompt_template"] == (
            "Translate the following subtitle text into {language}, but keep "
            "the subtitle number and timeline and newlines unchanged: \n{text}"
        )

    def test_the_default_is_a_usable_template(self, srt_file):
        # `{text}` and `{language}` and nothing else: the same contract the
        # CLI holds an operator's own template to
        rendered = DEFAULT_PROMPT_CONFIG["user"].format(text="x", language="zh")
        assert rendered.endswith("\nx")


class TestPromptReachesTheTranslator:
    def test_a_user_template_replaces_the_loader_default(self, srt_file):
        loader = _loader(srt_file, {"user": "Render {text} into {language}."})
        assert (
            loader.translate_model.kwargs["prompt_template"]
            == "Render {text} into {language}."
        )

    def test_a_section_the_prompt_omits_keeps_the_loader_default(self, srt_file):
        # a `--prompt` carrying only a user template must not silently drop
        # the subtitle system message with it
        loader = _loader(srt_file, {"user": "Render {text}."})
        assert (
            loader.translate_model.kwargs["prompt_sys_msg"]
            == DEFAULT_PROMPT_CONFIG["system"]
        )

    def test_a_style_section_travels_too(self, srt_file):
        loader = _loader(srt_file, {"user": "Render {text}.", "style": "Terse."})
        assert loader.translate_model.kwargs["style_note"] == "Terse."


class TestParsing:
    def test_blocks_keep_their_number_and_timeline(self, srt_file):
        loader = _loader(srt_file)
        blocks = loader._parse_srt(srt_file.read_text(encoding="utf-8"))
        assert [b["number"] for b in blocks] == ["1", "2"]
        assert blocks[0]["time"] == "00:00:01,000 --> 00:00:02,000"
        assert blocks[1]["text"] == "General Kenobi."

    def test_a_block_is_rebuilt_in_the_order_srt_wants(self, srt_file):
        loader = _loader(srt_file)
        block = loader._parse_srt(srt_file.read_text(encoding="utf-8"))[0]
        assert loader._get_block_text(block) == (
            "1\n00:00:01,000 --> 00:00:02,000\nHello there."
        )
