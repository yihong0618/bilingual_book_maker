import pytest


from make import ChatGPT

def test_translation():
    key = "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"

    # Create an instance of the ChatGPT class
    chatbot = ChatGPT(key)

    # Test the translation function
    result = chatbot.translate("Hello, how are you?")
    assert result == "你好，你好吗？"



if __name__ == "__main__":
    test_translation()
