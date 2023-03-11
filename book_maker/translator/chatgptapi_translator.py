import time
import re

import openai
from os import environ

from .base_translator import Base


class ChatGPTAPI(Base):
    def __init__(self, key, language, api_base=None, prompt_template=None):
        super().__init__(key, language)
        self.key_len = len(key.split(","))
        if api_base:
            openai.api_base = api_base
        self.prompt_template = (
            prompt_template
            or "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"
        )

    max_num_token = -1

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def get_translation(self, text):
        self.rotate_key()
        content = self.prompt_template.format(text=text, language=self.language)
        completion = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": environ.get("OPENAI_API_SYS_MSG") or "",
                },
                {
                    "role": "user",
                    "content": content,
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
        print("=================================================")
        print(f'Total tokens used this time: {completion["usage"]["total_tokens"]}')
        self.max_num_token = max(
            self.max_num_token, int(completion["usage"]["total_tokens"])
        )
        print(f"The maximum number of tokens used at one time: {self.max_num_token}")
        return t_text

    def translate(self, text, needprint=True):
        # todo: Determine whether to print according to the cli option
        if needprint:
            print(re.sub("\n{3,}", "\n\n", text))

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
        if needprint:
            print(re.sub("\n{3,}", "\n\n", t_text))
        return t_text

    def translate_and_split_lines(self, text):
        result_str = self.translate(text, False)
        lines = result_str.split("\n")
        lines = [line.strip() for line in lines if line.strip() != ""]
        return lines

    def translate_list(self, plist):
        sep = "\n\n\n\n\n"
        new_str = sep.join([item.text for item in plist])

        retry_count = 0
        plist_len = len(plist)

        # supplement_prompt = f"Translated result should have {plist_len} paragraphs"
        # supplement_prompt = "Each paragraph in the source text should be translated into a separate and complete paragraph, and each paragraph should be separated"
        supplement_prompt = "Each paragraph in the source text should be translated into a separate and complete paragraph, and each translated paragraph should be separated by a blank line"

        self.prompt_template = (
            "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text. "
            + supplement_prompt
        )

        lines = self.translate_and_split_lines(new_str)

        while len(lines) != plist_len and retry_count < 15:
            print(
                f"bug: {plist_len} paragraphs of text translated into {len(lines)} paragraphs"
            )
            num = 6
            print(f"sleep for {num}s and try again")
            time.sleep(num)
            print(f"retry {retry_count+1} ...")
            lines = self.translate_and_split_lines(new_str)
            retry_count += 1
            if len(lines) == plist_len:
                print("retry success")

        if len(lines) != plist_len:
            for i in range(0, plist_len):
                print(plist[i].text)
                print()
                if i < len(lines):
                    print(lines[i])
                    print()

            print(
                f"bug: {plist_len} paragraphs of text translated into {len(lines)} paragraphs"
            )
            print("continue")

        return lines
