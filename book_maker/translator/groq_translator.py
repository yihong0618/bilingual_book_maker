from groq import Groq
from .chatgptapi_translator import ChatGPTAPI
from os import linesep
from itertools import cycle

GROQ_MODEL_LIST = [
    "llama3-8b-8192",
    "llama3-70b-8192",
    "mixtral-8x7b-32768",
    "gemma-7b-it",
]


class GroqClient(ChatGPTAPI):
    # Requests go through self.groq_client, not self.openai_client, so probing
    # would send the capability request to OpenAI with a Groq key.
    SUPPORTS_STRUCTURED_OUTPUTS = False

    def _chat_completion(self, prompt, model=None):
        """Classification through Groq's own client.

        Inheriting ChatGPTAPI's would have posted a Groq key to
        api.openai.com. No probe runs here (see above), so the ladder enters
        at the prompt rung, which is the only one this override serves.
        """
        completion = Groq(api_key=next(self.keys)).chat.completions.create(
            model=model or self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content

    def set_model_list(self, model_list):
        """Accept explicit Groq model IDs without OpenAI endpoint validation.

        ChatGPTAPI's implementation lists models through its OpenAI client.
        This subclass sends translation requests through the Groq SDK, so
        inheriting that validation would query api.openai.com with a Groq key.
        Groq will validate each ID on the first real request instead.
        """
        models = list(dict.fromkeys(m.strip() for m in model_list if m.strip()))
        if not models:
            raise ValueError("--model_list is empty")
        print(f"Using model list {models}")
        self.model_list = cycle(models)
        self.model = models[0]

    def rotate_model(self):
        if not self.model_list:
            model_list = list(set(GROQ_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)
        self.model = next(self.model_list)

    def create_chat_completion(self, text):
        self.groq_client = Groq(api_key=next(self.keys))

        content = f"{self.prompt_template.format(text=text, language=self.language, crlf=linesep)}"
        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")

        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": content},
        ]

        if self.deployment_id:
            return self.groq_client.chat.completions.create(
                engine=self.deployment_id,
                messages=messages,
                temperature=self.temperature,
                azure=True,
            )
        return self.groq_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
