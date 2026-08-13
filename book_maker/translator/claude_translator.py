import re
from rich import print
from anthropic import Anthropic, BadRequestError, UnprocessableEntityError

from .base_translator import Base
from ..structured import RungRejected


def _sdk_base_url(api_base):
    """Trim a trailing `/v1` the SDK is going to add back.

    `Anthropic(base_url=...)` appends `/v1/messages` itself, so an api_base
    copied from an OpenAI-shaped gateway (`https://host/v1`) produces
    `/v1/v1/messages` and a 403 whose text — "HTTP node only allows access to
    inference API paths" — points nowhere near the cause.
    """
    if not api_base:
        return None
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
        print(f"[dim]using anthropic base_url {base} (the SDK adds /v1)[/dim]")
    return base


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
        base_url = _sdk_base_url(api_base)
        self.api_url = base_url or "https://api.anthropic.com"
        self.client = Anthropic(base_url=base_url, api_key=key, timeout=20)
        self.model = "claude-haiku-4-5-20251001"  # default it for now
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

    def set_model_list(self, model_list):
        """The `--model_list` surface, so `--provider` can reach this class.

        `--model` is limited to MODEL_DICT keys, so a gateway's own id
        (`claude-haiku-4.5`) could not otherwise reach the anthropic shape at
        all; cli.py calls this for every `--provider`, and its absence here
        was an AttributeError. Claude has no model rotation, so the first
        entry wins — announced, not silently.
        """
        models = [m.strip() for m in model_list if m and m.strip()]
        if not models:
            raise ValueError("--model_list is empty")
        if len(models) > 1:
            print(
                f"[yellow]ℹ claude uses one model per run; taking "
                f"'{models[0]}' and ignoring {len(models) - 1} more[/yellow]"
            )
        self.model = models[0]

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

    def _chat_completion(self, prompt, model=None):
        """One question, one answer — the channel plan classification needs.

        No native structured-output rung on purpose: nobody here runs the
        official Anthropic API, so its schema feature cannot be tested, and
        the gateways that serve the anthropic shape drop schema fields
        anyway. Measured 260807 on api.b.ai: Claude ignores `response_format`
        entirely, and still answers 12 of 12 signatures with legal verdicts
        through this rung. The lint is what establishes that, not the request.

        Deliberately outside the translation flow: no context pairs, no
        prompt template, no saved history.
        """
        try:
            r = self.client.messages.create(
                max_tokens=4096,
                model=model or self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        except (BadRequestError, UnprocessableEntityError) as e:
            raise RungRejected(e) from e
        return "".join(
            block.text for block in r.content if getattr(block, "type", "") == "text"
        )

    def translate(self, text):
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

        return t_text
