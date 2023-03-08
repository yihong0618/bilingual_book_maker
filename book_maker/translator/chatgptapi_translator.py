import time

import openai

from .base_translator import Base


class ChatGPTAPI(Base):
    def __init__(self, key, language, api_base=None):
        super().__init__(key, language)
        self.ken_len = len(key.split(","))
        if api_base:
            openai.api_base = api_base

    def translate(self, text):
        print(text)
        openai.api_key = self.get_key()
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        # english prompt here to save tokens
                        "content": f"Please help me to translate,`{text}` to {self.language}, please return only translated content not include the origin text",
                    }
                ],
            )
            t_text = (
                completion["choices"][0]
                .get("message")
                .get("content")
                .encode("utf8")
                .decode()
            )
        except Exception as e:
            # TIME LIMIT for open api please pay
            sleep_time = int(60 / self.key_len)
            time.sleep(sleep_time)
            print(e, f"will sleep  {sleep_time} seconds")
            openai.api_key = self.get_key()
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": f"Please help me to translate,`{text}` to {self.language}, please return only translated content not include the origin text",
                    }
                ],
            )
            t_text = (
                completion["choices"][0]
                .get("message")
                .get("content")
                .encode("utf8")
                .decode()
            )
        print(t_text)
        return t_text
