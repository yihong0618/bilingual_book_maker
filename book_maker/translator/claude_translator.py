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
        prompt_sys_msg=None,
        temperature=1.0,
        context_flag=False,
        context_paragraph_limit=5,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.api_url = api_base or "https://api.anthropic.com"
        self.client = Anthropic(base_url=api_base, api_key=key, timeout=20)
        self.model = "claude-3-5-sonnet-20241022"  # default it for now
        self.language = language
        self.prompt_template = (
            prompt_template
            or "Help me translate the text within triple backticks into {language} and provide only the translated result.\n```{text}```"
        )
        self.prompt_sys_msg = prompt_sys_msg or ""
        self.temperature = temperature
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        self.context_paragraph_limit = context_paragraph_limit

    def rotate_key(self):
        pass

    def set_claude_model(self, model_name):
        self.model = model_name

    def create_messages(self, text, intermediate_messages=None):
        """Create messages for the current translation request"""
        current_msg = {
            "role": "user",
            "content": self.prompt_template.format(
                text=text,
                language=self.language,
            ),
        }

        messages = []
        if intermediate_messages:
            messages.extend(intermediate_messages)
        messages.append(current_msg)

        return messages

    def create_context_messages(self):
        """Create a message pair containing all context paragraphs"""
        if not self.context_flag or not self.context_list:
            return []

        # Create a single message pair for all previous context
        return [
            {
                "role": "user",
                "content": self.prompt_template.format(
                    text="\n\n".join(self.context_list),
                    language=self.language,
                ),
            },
            {"role": "assistant", "content": "\n\n".join(self.context_translated_list)},
        ]

    def save_context(self, text, t_text):
        """Save the current translation pair to context"""
        if not self.context_flag:
            return

        self.context_list.append(text)
        self.context_translated_list.append(t_text)

        # Keep only the most recent paragraphs within the limit
        if len(self.context_list) > self.context_paragraph_limit:
            self.context_list.pop(0)
            self.context_translated_list.pop(0)

    def translate(self, text):
        print(text)
        self.rotate_key()

        # Create messages with context
        messages = self.create_messages(text, self.create_context_messages())

        r = self.client.messages.create(
            max_tokens=4096,
            messages=messages,
            system=self.prompt_sys_msg,
            temperature=self.temperature,
            model=self.model,
        )
        t_text = r.content[0].text

        if self.context_flag:
            self.save_context(text, t_text)

        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        return t_text
