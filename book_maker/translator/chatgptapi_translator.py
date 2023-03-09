import time

import openai

from .base_translator import Base

from .terminology_translator import build_terminology, terminology_prompt

class ChatGPTAPI(Base):
    def __init__(self, key, language, terminology_filename, api_base=None):
        super().__init__(key, language, terminology_filename)
        self.key_len = len(key.split(","))
        if api_base:
            openai.api_base = api_base
        self.terminology=build_terminology(self.terminology_filename)

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def translate(self, text):
        print(text)
        self.rotate_key()
        try:
            term_prompt=terminology_prompt(text, self.terminology)
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        # english prompt here to save tokens
                        "content": f"Please help me to translate,`{text}` to {self.language}, {term_prompt}, Please do not translate numbers and abbreviations. please return only translated content not include the origin text",
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
            self.rotate_key()
            term_prompt=terminology_prompt(text, self.terminology)
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": f"Please help me to translate,`{text}` to {self.language}, {term_prompt}, Please do not translate numbers and abbreviations.  please return only translated content not include the origin text",
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
