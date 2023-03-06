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
