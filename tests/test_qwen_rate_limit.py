from book_maker.translator.qwen_translator import QwenTranslator


def test_qwen_rate_limit_detection():
    assert QwenTranslator._is_rate_limit_error(Exception("Error code: 429"))
    assert QwenTranslator._is_rate_limit_error(Exception("limit_requests"))
    assert not QwenTranslator._is_rate_limit_error(Exception("network timeout"))


if __name__ == "__main__":
    test_qwen_rate_limit_detection()
