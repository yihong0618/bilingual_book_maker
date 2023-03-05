import unittest
from unittest.mock import Mock
from make import MyClass

class TestTranslate(unittest.TestCase):
    def setUp(self):
        self.my_class = MyClass()
        self.my_class.session = Mock()
        self.my_class.api_url = "https://example.com"
        self.my_class.headers = {"Content-Type": "application/json"}
        self.my_class.data = {"model": "text-to-text", "prompt": "", "temperature": 0.8}

    def test_translate_successful(self):
        # Mock the response from the server
        self.my_class.session.post.return_value.ok = True
        self.my_class.session.post.return_value.json.return_value = {
            "choices": [{"text": "你好"}]
        }

        # Call the function with some text to translate
        result = self.my_class.translate("hello")

        # Check that the result is correct
        self.assertEqual(result, "你好")
        self.assertEqual(
            self.my_class.data["prompt"], "Please help me to translate，`hello` to Chinese"
        )

    def test_translate_failure(self):
        # Mock a failure response from the server
        self.my_class.session.post.return_value.ok = False

        # Call the function with some text to translate
        result = self.my_class.translate("hello")

        # Check that the result is the original text (no translation)
        self.assertEqual(result, "hello")
        self.assertEqual(
            self.my_class.data["prompt"], "Please help me to translate，`hello` to Chinese"
        )
