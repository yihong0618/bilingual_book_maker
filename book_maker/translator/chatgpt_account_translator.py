import requests
from revChatGPT.V1 import Chatbot
from rich import print

from .base_translator import Base


class ChatGPTAccount(Base):
    def __init__(self, key, language, api_base=None, email=None, password=None):
        super().__init__(key, language)
        self.session = requests.session()
        self.language = language
        self.email = email
        self.password = password

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        chatbot = Chatbot(
            config={
                "email": self.email,
                "password": self.password,
            }
        )
        prompt = f"Please help me to translate,`{text}` to {self.language}"
        response = ""

        for data in chatbot.ask(prompt):
            response = data["message"].encode("utf8").decode()

        print(response)
        return response
