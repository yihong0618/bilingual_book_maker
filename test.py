import pytest
from make import GPT3

class TestGPT3:
    @pytest.fixture
    def gpt3(self):
        return GPT3()
    
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
