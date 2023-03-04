
import time
import openai
import requests

from abstract import TranslateEngineBase


class GPT3(TranslateEngineBase):
    def __init__(self, key: str, lang: str, *args, **kwargs):
        self.lang = lang
        self.api_key = key
        self.api_url = "https://api.openai.com/v1/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
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

    def translate(self, text) -> str:
        print(text)
        self.data["prompt"] = f"Please help me to translate the following text to {self.lang}: \n\n{text}"
        r = self.session.post(
            self.api_url, headers=self.headers, json=self.data)
        if not r.ok:
            return text
        t_text = r.json().get("choices")[0].get("text", "").strip()
        print(t_text)
        return t_text


class DeepL(TranslateEngineBase):
    def __init__(self, key: str, session, *args, **kwargs):
        super().__init__(key, session)

    def translate(self, text):
        return super().translate(text)


class ChatGPT(TranslateEngineBase):
    def __init__(self, key: str, lang: str, not_limit: bool = False, *args, **kwargs):
        super().__init__(key, lang)
        self.key = key
        self.lang = lang
        self.not_limit = not_limit

    def translate(self, text: str) -> str:
        print(text)
        openai.api_key = self.key
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
            print(str(e), "will sleep 60 seconds")
            # TIME LIMIT for open api please pay
            time.sleep(60)
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
