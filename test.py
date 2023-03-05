import pytest
from make import GPT3



# Replace with your OpenAI API key
API_KEY = "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"
gpt3 = GPT3(API_KEY)

def test_translate():
    # Test case 1: Basic translation
    text = "Hello, how are you?"
    expected = "你好，你怎么样？"
    assert gpt3.translate(text) == expected

    # Test case 2: Translation of a longer text
    text = "The quick brown fox jumps over the lazy dog."
    expected = "敏捷的棕色狐狸跳过了懒狗。"
    assert gpt3.translate(text) == expected

    # Test case 3: Translation of non-English text
    text = "Je ne parle pas français."
    expected = "请帮我将`Je ne parle pas français.`翻译成中文"
    assert gpt3.translate(text) == expected

    # Test case 4: Handling of errors
    text = ""
    expected = ""
    assert gpt3.translate(text) == expected

    print("All test cases pass")

# Run the test function
test_translate()
