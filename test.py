import pytest
from make import GPT3

class TestGPT3:
    @pytest.fixture
    def gpt3(self):
        key = "<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>"
        return GPT3(key)
    
    def test_translate(self, gpt3):
        text = "Hello"
        expected_output = "你好"
        assert gpt3.translate(text) == expected_output
        
        text = "Goodbye"
        expected_output = "再见"
        assert gpt3.translate(text) == expected_output
        
        text = ""
        expected_output = ""
        assert gpt3.translate(text) == expected_output
        
        text = "This is a test sentence."
        expected_output = "这是一个测试句子。"
        assert gpt3.translate(text) == expected_output
