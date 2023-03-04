
import time
import openai
import requests

from abstract import TranslateEngineBase


class GPT3(TranslateEngineBase):
    def __init__(self, key: str, lang: str, *args, **kwargs):
        super().__init__(key, lang)
        self.lang = lang
        self.api_key = key
        self.api_url = "https://api.openai.com/v1/completions"
        self.headers = {
            "Content-Type": "application/json",
        }
        # TODO support more models here
        self.data = {
            "prompt": "",
            "model": "text-davinci-003",
            "max_tokens": 1024,
            "temperature": 1,
            "top_p": 1,
        }
        self.session = requests.session()

    def translate(self, text: str) -> str:
        print(text)
        self.data["prompt"] = f"Please help me to translate the following text to {self.lang}: \n\n{text}"
        r = self.session.post(
            self.api_url,
            headers=self.headers,
            json=self.data
        )
        if not r.ok:
            return text
        t_text = r.json().get("choices")[0].get("text", "").strip()
        print(t_text)
        return t_text


class DeepL(TranslateEngineBase):
    def __init__(self, key: str, session, *args, **kwargs):
        super().__init__(key, session)

    def translate(self, text: str):
        return super().translate(text)


class ChatGPT(TranslateEngineBase):
    def __init__(self, key: str, lang: str, not_limit: bool = False, *args, **kwargs):
        super().__init__(key, lang)
        self.key = key
        self.lang = lang
        self.not_limit = not_limit
        self.current_key_index = 0

    def get_key(self, key_str):
        keys = key_str.split(",")
        key = keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(keys)
        return key

    def translate(self, text: str) -> str:
        print(text)
        openai.api_key = self.get_key(self.key)
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        # english prompt here to save tokens
                        "content": f"Please help me to translate the following text to {self.lang}. Please return only translated content not include the origin text. Here is the text: \n\n{text}",
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
            if not self.not_limit:
                # for time limit
                time.sleep(3)
        except Exception as e:
            # TIME LIMIT for open api please pay
            key_len = self.key.count(",") + 1
            sleep_time = int(60 / key_len)
            time.sleep(sleep_time)
            print(str(e), "will sleep  " + str(sleep_time) + " seconds")
            openai.api_key = self.get_key(self.key)
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": f"Please help me to translate the following text to {self.lang}. Please return only translated content not include the origin text. Here is the text: \n\n{text}",
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
