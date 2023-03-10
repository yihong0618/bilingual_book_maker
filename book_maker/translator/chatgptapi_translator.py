import time

import openai

from .base_translator import Base

from .terminology_translator import build_terminology, terminology_prompt

class ChatGPTAPI(Base):
    def __init__(self, key, language, terminology_filename, Professional_field="medical", api_base=None):
        super().__init__(key, language, terminology_filename,Professional_field)
        self.key_len = len(key.split(","))
        if api_base:
            openai.api_base = api_base
        self.terminology=build_terminology(self.terminology_filename)

    def rotate_key(self):
        openai.api_key = next(self.keys)

    def translate(self, text):
        print(text)
        self.rotate_key()
        # Professional_field="medical"
        Professional_prompt=""
        if self.Professional_field !="":
            Professional_prompt= f"It is {self.Professional_field} contents, and when translating, attention should be paid to using commonly used {self.Professional_field} professional terms expressions. "
        nagative_prompt="Please do not translate numbers and abbreviations, such as '123' '4.00' 'ACD' or 'IOL'."
        positive_prompt="Keep the meaning same, but make them more literary and easier to understand. "
       
        try:
            term_prompt=terminology_prompt(text, self.terminology)
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        # english prompt here to save tokens
                        "content": f"I want you to act as a translator, Please help me to translate to {self.language}, {positive_prompt} {nagative_prompt} {term_prompt} {Professional_prompt} Please return only translated content not include the origin text. The content that needs to be translated is \n\n `{text}` ",
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
                        "content": f"I want you to act as a translator, Please help me to translate to {self.language}, {positive_prompt} {nagative_prompt} {term_prompt} {Professional_prompt} Please return only translated content not include the origin text. The content that needs to be translated is \n\n `{text}` ",
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
