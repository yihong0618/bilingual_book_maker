import time
import re
from copy import copy
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
            or ""
        )
        self.system_content = environ.get("OPENAI_API_SYS_MSG") or ""

    max_num_token = -1

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def create_chat_completion(self, text):
        content = self.prompt_template.format(text=text, language=self.language)
        sys_content = self.prompt_sys_msg
        if self.system_content:
            sys_content = self.system_content
        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": content},
        ]

        return openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
        )

    def get_translation(self, text):
        self.rotate_key()

        completion = {}
        try:
            completion = self.create_chat_completion(text)
        except Exception:
            if (
                not "choices" in completion
                or not isinstance(completion["choices"], list)
                or len(completion["choices"]) == 0
            ):
                raise
            if completion["choices"][0]["finish_reason"] != "length":
                raise

        # work well or exception finish by length limit
        choice = completion["choices"][0]

        t_text = choice.get("message").get("content").encode("utf8").decode()

        if choice["finish_reason"] == "length":
            with open("log/long_text.txt", "a") as f:
                print(
                    f"""==================================================
The total token is too long and cannot be completely translated\n
{text}
""",
                    file=f,
                )

        # usage = completion["usage"]
        # print(f"total_token: {usage['total_tokens']}")
        # if int(usage["total_tokens"]) > self.max_num_token:
        #     self.max_num_token = int(usage["total_tokens"])
        #     print(
        #         f"{usage['total_tokens']} {usage['prompt_tokens']} {usage['completion_tokens']} {self.max_num_token} (total_token, prompt_token, completion_tokens, max_history_total_token)"
        #     )
        return t_text

    def translate(self, text, needprint=True):
        # print("=================================================")
        start_time = time.time()
        # todo: Determine whether to print according to the cli option
        if needprint:
            print(re.sub("\n{3,}", "\n\n", text))

        attempt_count = 0
        max_attempts = 3
        t_text = ""

        while attempt_count < max_attempts:
            try:
                t_text = self.get_translation(text)
                break
            except Exception as e:
                # todo: better sleep time? why sleep alawys about key_len
                # 1. openai server error or own network interruption, sleep for a fixed time
                # 2. an apikey has no money or reach limit, don’t sleep, just replace it with another apikey
                # 3. all apikey reach limit, then use current sleep
                sleep_time = int(60 / self.key_len)
                print(e, f"will sleep {sleep_time} seconds")
                time.sleep(sleep_time)
                attempt_count += 1
                if attempt_count == max_attempts:
                    print(f"Get {attempt_count} consecutive exceptions")
                    raise

        # todo: Determine whether to print according to the cli option
        if needprint:
            print(re.sub("\n{3,}", "\n\n", t_text))

        elapsed_time = time.time() - start_time
        # print(f"translation time: {elapsed_time:.1f}s")

        return t_text

    def translate_and_split_lines(self, text):
        result_str = self.translate(text, False)
        lines = result_str.split("\n")
        lines = [line.strip() for line in lines if line.strip() != ""]
        return lines

    def get_best_result_list(
        self, plist_len, new_str, sleep_dur, result_list, max_retries=15
    ):
        if len(result_list) == plist_len:
            return result_list, 0

        best_result_list = result_list
        retry_count = 0

        while retry_count < max_retries and len(result_list) != plist_len:
            print(
                f"bug: {plist_len} -> {len(result_list)} : Number of paragraphs before and after translation"
            )
            print(f"sleep for {sleep_dur}s and retry {retry_count+1} ...")
            time.sleep(sleep_dur)
            retry_count += 1
            result_list = self.translate_and_split_lines(new_str)
            if (
                len(result_list) == plist_len
                or len(best_result_list) < len(result_list) <= plist_len
                or (
                    len(result_list) < len(best_result_list)
                    and len(best_result_list) > plist_len
                )
            ):
                best_result_list = result_list

        return best_result_list, retry_count

    def log_retry(self, state, retry_count, elapsed_time, log_path="log/buglog.txt"):
        if retry_count == 0:
            return
        print(f"retry {state}")
        with open(log_path, "a") as f:
            print(
                f"retry {state}, count = {retry_count}, time = {elapsed_time:.1f}s",
                file=f,
            )

    def log_translation_mismatch(
        self, plist_len, result_list, new_str, sep, log_path="log/buglog.txt"
    ):
        if len(result_list) == plist_len:
            return
        newlist = new_str.split(sep)
        with open(log_path, "a") as f:
            print(f"problem size: {plist_len - len(result_list)}", file=f)
            for i in range(len(newlist)):
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

    def translate_list(self, plist):
        sep = "\n\n\n\n\n"
        # new_str = sep.join([item.text for item in plist])

        new_str = ""
        i = 1
        for p in plist:
            temp_p = copy(p)
            for sup in temp_p.find_all("sup"):
                sup.extract()
            new_str += f"({i}) " + temp_p.get_text().strip() + sep
            i = i + 1

        if new_str.endswith(sep):
            new_str = new_str[: -len(sep)]

        plist_len = len(plist)

        print(f"plist len = {len(plist)}")

        result_list = self.translate_and_split_lines(new_str)

        start_time = time.time()

        result_list, retry_count = self.get_best_result_list(
            plist_len, new_str, 6, result_list
        )

        end_time = time.time()

        state = "fail" if len(result_list) != plist_len else "success"
        log_path = "log/buglog.txt"

        self.log_retry(state, retry_count, end_time - start_time, log_path)
        self.log_translation_mismatch(plist_len, result_list, new_str, sep, log_path)

        # del (num), num. sometime (num) will translated to num.
        result_list = [re.sub(r"^(\(\d+\)|\d+\.|（\d+）)\s*", "", s) for s in result_list]
        return result_list
