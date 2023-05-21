import re
import requests
from rich import print

from .base_translator import Base


class Claude(Base):
    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        temperature=1.0,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.api_url = (
            f"{api_base}v1/complete"
            if api_base
            else "https://api.anthropic.com/v1/complete"
        )
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
        }
        self.data = {
            "prompt": "",
            "model": "claude-v1.3",
            "max_tokens_to_sample": 1024,
            "temperature": temperature,
            "stop_sequences": ["\n\nHuman:"],
        }
        self.session = requests.session()
        self.language = language
        self.prompt_template = (
            prompt_template
            or "\n\nHuman: Help me translate the text within triple backticks into {language} and provide only the translated result.\n```{text}```\n\nAssistant: "
        )

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        self.rotate_key()
        self.data["prompt"] = self.prompt_template.format(
            text=text,
            language=self.language,
        )
        r = self.session.post(self.api_url, headers=self.headers, json=self.data)
        if not r.ok:
            return text
        t_text = r.json().get("completion").strip()

        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        return t_text
