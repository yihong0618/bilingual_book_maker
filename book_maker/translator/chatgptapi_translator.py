import time
from os import environ

import openai

from .base_translator import Base

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}


class ChatGPTAPI(Base):
    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        **kwargs,
    ):
        super().__init__(key, language)
        self.key_len = len(key.split(","))
        if api_base:
            openai.api_base = api_base
        self.prompt_template = (
            prompt_template
            or environ.get(PROMPT_ENV_MAP["user"])
            or self.DEFAULT_PROMPT
        )
        self.prompt_sys_msg = (
            prompt_sys_msg
            or environ.get(
                "OPENAI_API_SYS_MSG"
            )  # XXX: for backward compatability, deprecate soon
            or environ.get(PROMPT_ENV_MAP["system"])
        )

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def get_translation(self, text):
        self.rotate_key()
        messages = []
        if self.prompt_sys_msg:
            messages.append(
                {"role": "system", "content": self.prompt_sys_msg},
            )
        messages.append(
            {
                "role": "user",
                "content": self.prompt_template.format(
                    text=text, language=self.language
                ),
            }
        )

        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
        )
        return (
            completion["choices"][0]
            .get("message")
            .get("content")
            .encode("utf8")
            .decode()
        )

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
        print(t_text.strip())
        return t_text
