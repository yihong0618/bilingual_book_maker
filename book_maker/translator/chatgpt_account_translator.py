import requests
from revChatGPT.V1 import Chatbot
from rich import print

from .base_translator import Base


class ChatGPTAccount(Base):
    def __init__(self, key, language, **kwargs):
        super().__init__(key, language)
        self.language = language
        self.chatgpt_account = kwargs.get("chatgptaccount")
        self.chatgpt_password = kwargs.get("chatgptpassword")

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        chatbot = Chatbot(
            config={
                "email": self.chatgpt_account,
                "password": self.chatgpt_password,
            }
        )
        prompt = f"Please help me to translate,`{text}` to {self.language}"
        response = ""

        for data in chatbot.ask(prompt):
            response = data["message"].encode("utf8").decode()

        print(response)
        return response
