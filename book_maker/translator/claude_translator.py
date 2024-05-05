import re
import time
from rich import print
from anthropic import Anthropic

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
        self.api_url = f"{api_base}" if api_base else "https://api.anthropic.com"
        self.client = Anthropic(base_url=api_base, api_key=key, timeout=20)

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
        prompt = self.prompt_template.format(
            text=text,
            language=self.language,
        )
        message = [{"role": "user", "content": prompt}]
        r = self.client.messages.create(
            max_tokens=4096,
            messages=message,
            model="claude-3-haiku-20240307",  # default it for now
        )
        t_text = r.content[0].text
        # api limit rate and spider rule
        time.sleep(1)

        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        return t_text
