import pytest


from make import ChatGPT

def test_translation():
    # Set up the API key
    key = "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"

    # Create an instance of the ChatGPT class
    chatbot = ChatGPT(key)

    # Test the translation function
    result = chatbot.translate("Hello, how are you?")
    assert result == "你好，你好吗？"

    print("Translation test passed!")

if __name__ == "__main__":
    test_translation()
