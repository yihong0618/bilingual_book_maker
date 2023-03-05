import pytest
from make import MyClass

class TestMyClass:
    @pytest.fixture
    def my_class(self):
        return MyClass()
    
    def test_translate(self, my_class):
        text = "Hello"
        expected_output = "你好"
        assert my_class.translate(text) == expected_output
        
        text = "Goodbye"
        expected_output = "再见"
        assert my_class.translate(text) == expected_output
        
        text = ""
        expected_output = ""
        assert my_class.translate(text) == expected_output
