import time

import openai
from ..utils import num_tokens_from_messages
from os import environ

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
        ]
        count_tokens = num_tokens_from_messages(message_log)
        consumed_tokens = 0
        t_text = ""
        if count_tokens > 4000:
            print("too long!")

            splits = count_tokens // 4000 + 1

            text_list = text.split(".")
            sub_text = ""
            t_sub_text = ""
            for n in range(splits):
                text_segment = text_list[n * splits : (n + 1) * splits]
                sub_text = ".".join(text_segment)
                print(sub_text)

        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
        )
        t_text = (
            completion["choices"][0]
            .get("message")
            .get("content")
            .encode("utf8")
            .decode()
        )
        return t_text
                consumed_tokens += completion["usage"]["prompt_tokens"]

    def translate(self, text):
        # todo: Determine whether to print according to the cli option
        print(text)

            else:
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
                    consumed_tokens += completion["usage"]["prompt_tokens"]

                except Exception as e:
                    # TIME LIMIT for open api please pay
                    key_len = self.key.count(",") + 1
                    sleep_time = int(60 / key_len)
                    time.sleep(sleep_time)
                    print(e, f"will sleep  {sleep_time} seconds")
                    self.rotate_key()
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
                    consumed_tokens += completion["usage"]["prompt_tokens"]

        print(t_text)
        print(f"{consumed_tokens} prompt tokens used.")
        return t_text
