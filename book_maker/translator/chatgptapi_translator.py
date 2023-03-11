import time

import openai
from os import environ

from .base_translator import Base


class ChatGPTAPI(Base):
    def __init__(self, key, language, api_base=None):
        super().__init__(key, language)
        self.key_len = len(key.split(","))
        if api_base:
            openai.api_base = api_base

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def get_translation(self, text):
        self.rotate_key()
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": environ.get("OPENAI_API_SYS_MSG") or "",
                },
                {
                    "role": "user",
                    "content": f"Please help me to translate,`{text}` to {self.language}, please return only translated content not include the origin text",
                },
            ],
        )
        t_text = (
            completion["choices"][0]
            .get("message")
            .get("content")
            .encode("utf8")
            .decode()
        )
        return t_text

    def translate(self, text):
        # todo: Determine whether to print according to the cli option
        print(text)

        try:
            t_text = self.get_translation(text)
        except Exception as e:
            # todo: better sleep time? why sleep alawys about key_len
            # 1. openai server error or own network interruption, sleep for a fixed time
            # 2. an apikey has no money or reach limit, don’t sleep, just replace it with another apikey
            # 3. all apikey reach limit, then use current sleep
            sleep_time = int(60 / self.key_len)
            print(e, f"will sleep {sleep_time} seconds")
            time.sleep(sleep_time)

            t_text = self.get_translation(text)

        # todo: Determine whether to print according to the cli option
        print(t_text)
        return t_text

    def translate_list(self, plist):
        sep = "\n\n\n\n\n"
        new_str = sep.join([item.text for item in plist])
        resultStr = self.translate(new_str)

        lines = resultStr.split("\n")
        lines = [line.strip() for line in lines if line.strip() != ""]

        return lines
