import time
import re
from copy import copy

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
        self.system_content = environ.get("OPENAI_API_SYS_MSG") or ""

    max_num_token = -1

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def get_translation(self, text):
        self.rotate_key()
        content = self.prompt_template.format(text=text, language=self.language)

        completion = {}
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system",
                        "content": self.system_content,
                    },
                    {
                        "role": "user",
                        "content": content,
                    },
                ],
            )
        except Exception:
            if completion["choices"][0]["finish_reason"] != "length":
                raise

        choice = completion["choices"][0]

        t_text = choice.get("message").get("content").encode("utf8").decode()

        if choice["finish_reason"] == "length":
            with open("long_text.txt", "a") as f:
                print(
                    f"""==================================================
The total token is too long and cannot be completely translated\n
{text}
""",
                    file=f,
                )

        usage = completion["usage"]
        if int(usage["total_tokens"]) > self.max_num_token:
            self.max_num_token = int(usage["total_tokens"])
            print("=================================================")
            print(
                f"{usage['total_tokens']} {usage['prompt_tokens']} {usage['completion_tokens']} {self.max_num_token} (total_token, prompt_token, completion_tokens, max_history_total_token)"
            )
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
        # new_str = sep.join([item.text for item in plist])

        new_str = ""
        for p in plist:
            temp_p = copy(p)
            for sup in temp_p.find_all("sup"):
                sup.extract()
            new_str += temp_p.get_text().strip() + sep

        if new_str.endswith(sep):
            new_str = new_str[: -len(sep)]

        plist_len = len(plist)
        always_trans = """[If there are any links, images, figure, listing or other content that cannot be translated, please leave them in the original language. If you cannot translate the content, please include it in brackets like this]:
[Insert Original Content Here]
        """

        self.system_content += f"""Please translate the following paragraphs individually while preserving their original structure(This time it should be exactly {plist_len} paragraphs, no more or less). {always_trans}. Only translate the paragraphs provided below:

[Insert first paragraph here]

[Insert second paragraph here]

[Insert third paragraph here]
"""

        retry_count = 0
        sleep_dur = 6
        result_list = self.translate_and_split_lines(new_str)

        while len(result_list) != plist_len and retry_count < 5:
            print(
                f"bug: {plist_len} -> {len(result_list)} : Number of paragraphs before and after translation"
            )
            print(f"sleep for {sleep_dur}s and retry {retry_count+1} ...")
            time.sleep(sleep_dur)
            result_list = self.translate_and_split_lines(new_str)
            retry_count += 1

        state = "fail" if len(result_list) != plist_len else "success"

        if retry_count > 0:
            print(f"retry {state}")
            with open("buglog.txt", "a") as f:
                print(
                    f"retry {state}, count = {retry_count}",
                    file=f,
                )

        if len(result_list) != plist_len:
            # todo: select best
            newlist = new_str.split(sep)
            with open("buglog.txt", "a") as f:
                print(f"problem size: {plist_len - len(result_list)}", file=f)
                for i in range(0, len(newlist)):
                    print(newlist[i], file=f)
                    print(file=f)
                    if i < len(result_list):
                        print(result_list[i], file=f)
                        print(file=f)
                    print("=============================", file=f)
            print(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")

            print(
                f"bug: {plist_len} paragraphs of text translated into {len(result_list)} paragraphs"
            )
            print("continue")

        return result_list
