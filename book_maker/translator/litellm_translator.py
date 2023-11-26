from os import linesep

from litellm import completion

from book_maker.translator.chatgptapi_translator import ChatGPTAPI

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}


class liteLLM(ChatGPTAPI):
    def create_chat_completion(self, text):
        # content = self.prompt_template.format(
        #     text=text, language=self.language, crlf="\n"
        # )

        content = f"{self.context if self.context_flag else ''} {self.prompt_template.format(text=text, language=self.language, crlf=linesep)}"

        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")

        context_sys_str = "For each passage given, you may be provided a summary of the story up until this point (wrapped in tags '<summary>' and '</summary>') for context within the query, to provide background context of the story up until this point. If it's provided, use the context summary to aid you in translation with deeper comprehension, and write a new summary above the returned translation, wrapped in '<summary>' HTML-like tags, including important details (if relevant) from the new passage, retaining the most important key details from the existing summary, and dropping out less important details. If the summary is blank, assume it is the start of the story and write a summary from scratch. Do not make the summary longer than a paragraph, and smaller details can be replaced based on the relative importance of new details. The summary should be formatted in straightforward, inornate text, briefly summarising the entire story (from the start, including information before the given passage, leading up to the given passage) to act as an instructional payload for a Large-Language AI Model to fully understand the context of the passage."

        sys_content = f"{self.system_content or self.prompt_sys_msg.format(crlf=linesep)} {context_sys_str if self.context_flag else ''} "

        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": content},
        ]

        if self.deployment_id:
            return completion(
                engine=self.deployment_id,
                messages=messages,
                temperature=self.temperature,
                azure=True,
            )

        return completion(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=self.temperature,
        )
