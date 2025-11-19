import re
import time
import os
import shutil
from copy import copy
from os import environ
from itertools import cycle
import json
from threading import Lock

from openai import AzureOpenAI, OpenAI, RateLimitError
from rich import print

from .base_translator import Base
from ..config import config

CHATGPT_CONFIG = config["translator"]["chatgptapi"]

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}

GPT35_MODEL_LIST = [
    "gpt-3.5-turbo",
    "gpt-3.5-turbo-1106",
    "gpt-3.5-turbo-16k",
    "gpt-3.5-turbo-0613",
    "gpt-3.5-turbo-16k-0613",
    "gpt-3.5-turbo-0301",
    "gpt-3.5-turbo-0125",
]
GPT4_MODEL_LIST = [
    "gpt-4-1106-preview",
    "gpt-4",
    "gpt-4-32k",
    "gpt-4o-2024-05-13",
    "gpt-4-0613",
    "gpt-4-32k-0613",
]

GPT4oMINI_MODEL_LIST = [
    "gpt-4o-mini",
    "gpt-4o-mini-2024-07-18",
]
GPT4o_MODEL_LIST = [
    "gpt-4o",
    "gpt-4o-2024-05-13",
    "gpt-4o-2024-08-06",
    "chatgpt-4o-latest",
]
O1PREVIEW_MODEL_LIST = [
    "o1-preview",
    "o1-preview-2024-09-12",
]
O1_MODEL_LIST = [
    "o1",
    "o1-2024-12-17",
]
O1MINI_MODEL_LIST = [
    "o1-mini",
    "o1-mini-2024-09-12",
]
O3MINI_MODEL_LIST = [
    "o3-mini",
]


class ChatGPTAPI(Base):
    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        temperature=1.0,
        context_flag=False,
        context_paragraph_limit=0,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.key_len = len(key.split(","))
        self.openai_client = OpenAI(api_key=next(self.keys), base_url=api_base)
        self.api_base = api_base

        self.prompt_template = (
            prompt_template
            or environ.get(PROMPT_ENV_MAP["user"])
            or self.DEFAULT_PROMPT
        )
        self.prompt_sys_msg = (
            prompt_sys_msg
            or environ.get(
                "OPENAI_API_SYS_MSG",
            )  # XXX: for backward compatibility, deprecate soon
            or environ.get(PROMPT_ENV_MAP["system"])
            or ""
        )
        self.system_content = environ.get("OPENAI_API_SYS_MSG") or ""
        self.deployment_id = None
        self.temperature = temperature
        self.model_list = None
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        if context_paragraph_limit > 0:
            # not set by user, use default
            self.context_paragraph_limit = context_paragraph_limit
        else:
            # set by user, use user's value
            self.context_paragraph_limit = CHATGPT_CONFIG["context_paragraph_limit"]
        self.batch_text_list = []
        self.batch_info_cache = None
        self.result_content_cache = {}
        self._api_lock = Lock()

    def rotate_key(self):
        with self._api_lock:
            self.openai_client.api_key = next(self.keys)

    def rotate_model(self):
        with self._api_lock:
            if self.model_list:
                self.model = next(self.model_list)

    def create_messages(self, text, intermediate_messages=None):
        content = self.prompt_template.format(
            text=text, language=self.language, crlf="\n"
        ) if "{language}" in self.prompt_template else self.prompt_template.format(text=text, crlf="\n")

        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")
        messages = [
            {"role": "system", "content": sys_content},
        ]

        if intermediate_messages:
            messages.extend(intermediate_messages)

        messages.append({"role": "user", "content": content})
        return messages

    def create_context_messages(self):
        messages = []
        if self.context_flag:
            messages.append({"role": "user", "content": "\n".join(self.context_list)})
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(self.context_translated_list),
                }
            )
        return messages

    def create_chat_completion(self, messages):
        
        completion = self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            stream=False,
        )
        return completion

    def get_translation(self, text, needprint=True):
        self.rotate_key()
        self.rotate_model()  # rotate all the model to avoid the limit
        
        messages = self.create_messages(text, self.create_context_messages())
        completion = self.create_chat_completion(messages)

        # TODO work well or exception finish by length limit
        # Check if content is not None before encoding
        t_text = ""
        max_len_retry= 2
        for len_retry in range(max_len_retry):
            cur_content = completion.choices[0].message.content
            cur_content = re.sub(r'<think>.*?</think>','',cur_content,flags=re.S) #r1-like things
            if cur_content is not None:
                t_text += cur_content.encode("utf8").decode() or ""
            else:
                break
            if completion.choices[0].finish_reason != "length":
                break
            if needprint:
                _comp_len_info = f"completion_tokens: {completion.usage.completion_tokens}" if completion.usage.completion_tokens else f"len(completion): {len(cur_content)}"
                print(f"[bold red]Imcompleted translation due to length at Attempt {len_retry+1}; {_comp_len_info}[/bold red]")
            messages+=[{"role": "assistant","content": cur_content},{"role": "user", "content": "继续"}]
            completion = self.create_chat_completion(messages)

        if self.context_flag:
            self.save_context(text, t_text)

        return t_text

    def save_context(self, text, t_text):
        if self.context_paragraph_limit > 0:
            self.context_list.append(text)
            self.context_translated_list.append(t_text)
            # Remove the oldest context
            if len(self.context_list) > self.context_paragraph_limit:
                self.context_list.pop(0)
                self.context_translated_list.pop(0)

    def translate(self, text, needprint=True):
        start_time = time.time()
        # todo: Determine whether to print according to the cli option
        if needprint:
            print(re.sub("\n{3,}", "\n\n", text).replace('[/','').replace(r'[\\',''))

        attempt_count = 0
        max_attempts = 30
        t_text = ""

        while attempt_count < max_attempts:
            try:
                t_text = self.get_translation(text)
                break
            except RateLimitError as e:
                # todo: better sleep time? why sleep alawys about key_len
                # 1. openai server error or own network interruption, sleep for a fixed time
                # 2. an apikey has no money or reach limit, don`t sleep, just replace it with another apikey
                # 3. all apikey reach limit, then use current sleep
                sleep_time = int(10 / self.key_len)
                print(e, f"will sleep {sleep_time} seconds")
                time.sleep(sleep_time)
                attempt_count += 1
                if attempt_count == max_attempts:
                    print(f"Get {attempt_count} consecutive exceptions")
                    raise
            except Exception as e:
                sleep_time = 5+5*attempt_count
                print(str(e), f"will sleep {sleep_time} seconds")
                time.sleep(sleep_time)
                attempt_count += 1
                if attempt_count == max_attempts:
                    print(f"Get {attempt_count} consecutive exceptions")
                    return


        # todo: Determine whether to print according to the cli option
        if needprint:
            print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text).replace('[/','').replace(r'[\\','') + "[/bold green]")

        time.time() - start_time
        # print(f"translation time: {elapsed_time:.1f}s")

        return t_text

    def translate_and_split_lines(self, text):
        result_str = self.translate(text, False)
        lines = result_str.splitlines()
        lines = [line.strip() for line in lines if line.strip() != ""]
        return lines

    def log_retry(self, state, retry_count, elapsed_time, log_path="log/buglog.txt"):
        if retry_count == 0:
            return
        print(f"retry {state}")
        with open(log_path, "a", encoding="utf-8") as f:
            print(
                f"retry {state}, count = {retry_count}, time = {elapsed_time:.1f}s",
                file=f,
            )

    def log_translation_mismatch(
        self,
        plist_len,
        result_list,
        new_str,
        sep,
        log_path="log/buglog.txt",
    ):
        if len(result_list) == plist_len:
            return
        newlist = new_str.split(sep)
        with open(log_path, "a", encoding="utf-8") as f:
            print(f"problem size: {plist_len - len(result_list)}", file=f)
            for i in range(len(newlist)):
                print(newlist[i], file=f)
                print(file=f)
                if i < len(result_list):
                    print("............................................", file=f)
                    print(result_list[i], file=f)
                    print(file=f)
                print("=============================", file=f)

        print(
            f"bug: {plist_len} paragraphs of text translated into {len(result_list)} paragraphs",
        )
        print("continue")

    def join_lines(self, text):
        lines = text.splitlines()
        new_lines = []
        temp_line = []

        # join
        for line in lines:
            if line.strip():
                temp_line.append(line.strip())
            else:
                if temp_line:
                    new_lines.append(" ".join(temp_line))
                    temp_line = []
                new_lines.append(line)

        if temp_line:
            new_lines.append(" ".join(temp_line))

        text = "\n".join(new_lines)
        # try to fix #372
        if not text:
            return ""

        # del ^M
        text = text.replace("^M", "\r")
        lines = text.splitlines()
        filtered_lines = [line for line in lines if line.strip() != "\r"]
        new_text = "\n".join(filtered_lines)

        return new_text

    def translate_list(self, plist):
        plist_len = len(plist)

        # Create a list of original texts and add clear numbering markers to each paragraph
        formatted_text = ""
        for i, p in enumerate(plist, 1):
            temp_p = copy(p)
            for sup in temp_p.find_all("sup"):
                sup.extract()
            para_text = temp_p.get_text().strip()
            # Using special delimiters and clear numbering
            formatted_text += f"PARAGRAPH {i}:\n{para_text}\n\n"

        print(f"plist len = {plist_len}")

        original_prompt_template = self.prompt_template

        structured_prompt = (
            f"Translate the following {plist_len} paragraphs to {{language}}. "
            f"CRUCIAL INSTRUCTION: Format your response using EXACTLY this structure:\n\n"
            f"TRANSLATION OF PARAGRAPH 1:\n[Your translation of paragraph 1 here]\n\n"
            f"TRANSLATION OF PARAGRAPH 2:\n[Your translation of paragraph 2 here]\n\n"
            f"... and so on for all {plist_len} paragraphs.\n\n"
            f"You MUST provide EXACTLY {plist_len} translated paragraphs. "
            f"Do not merge, split, or rearrange paragraphs. "
            f"Translate each paragraph independently but consistently. "
            f"Keep all numbers and special formatting in your translation. "
            f"Each original paragraph must correspond to exactly one translated paragraph."
        )

        self.prompt_template = structured_prompt + " ```{text}```"

        translated_text = self.translate(formatted_text, False)

        # Extract translations from structured output
        translated_paragraphs = []
        for i in range(1, plist_len + 1):
            pattern = (
                r"TRANSLATION OF PARAGRAPH "
                + str(i)
                + r":(.*?)(?=TRANSLATION OF PARAGRAPH \d+:|\Z)"
            )
            matches = re.findall(pattern, translated_text, re.DOTALL)

            if matches:
                translated_paragraph = matches[0].strip()
                translated_paragraphs.append(translated_paragraph)
            else:
                print(f"Warning: Could not find translation for paragraph {i}")
                loose_pattern = (
                    r"(?:TRANSLATION|PARAGRAPH|PARA).*?"
                    + str(i)
                    + r".*?:(.*?)(?=(?:TRANSLATION|PARAGRAPH|PARA).*?\d+.*?:|\Z)"
                )
                loose_matches = re.findall(loose_pattern, translated_text, re.DOTALL)
                if loose_matches:
                    translated_paragraphs.append(loose_matches[0].strip())
                else:
                    translated_paragraphs.append("")

        self.prompt_template = original_prompt_template

        # If the number of extracted paragraphs is incorrect, try the alternative extraction method.
        if len(translated_paragraphs) != plist_len:
            print(
                f"Warning: Extracted {len(translated_paragraphs)}/{plist_len} paragraphs. Using fallback extraction."
            )

            all_para_pattern = r"(?:TRANSLATION|PARAGRAPH|PARA).*?(\d+).*?:(.*?)(?=(?:TRANSLATION|PARAGRAPH|PARA).*?\d+.*?:|\Z)"
            all_matches = re.findall(all_para_pattern, translated_text, re.DOTALL)

            if all_matches:
                # Create a dictionary to map translation content based on paragraph numbers
                para_dict = {}
                for num_str, content in all_matches:
                    try:
                        num = int(num_str)
                        if 1 <= num <= plist_len:
                            para_dict[num] = content.strip()
                    except ValueError:
                        continue

                # Rebuild the translation list in the original order
                new_translated_paragraphs = []
                for i in range(1, plist_len + 1):
                    if i in para_dict:
                        new_translated_paragraphs.append(para_dict[i])
                    else:
                        new_translated_paragraphs.append("")

                if len(new_translated_paragraphs) == plist_len:
                    translated_paragraphs = new_translated_paragraphs

        if len(translated_paragraphs) < plist_len:
            translated_paragraphs.extend(
                [""] * (plist_len - len(translated_paragraphs))
            )
        elif len(translated_paragraphs) > plist_len:
            translated_paragraphs = translated_paragraphs[:plist_len]

        return translated_paragraphs

    def extract_paragraphs(self, text, paragraph_count):
        """Extract paragraphs from translated text, ensuring paragraph count is preserved."""
        # First try to extract by paragraph numbers (1), (2), etc.
        result_list = []
        for i in range(1, paragraph_count + 1):
            pattern = rf"\({i}\)\s*(.*?)(?=\s*\({i + 1}\)|\Z)"
            match = re.search(pattern, text, re.DOTALL)
            if match:
                result_list.append(match.group(1).strip())

        # If exact pattern matching failed, try another approach
        if len(result_list) != paragraph_count:
            pattern = r"\((\d+)\)\s*(.*?)(?=\s*\(\d+\)|\Z)"
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                # Sort by paragraph number
                matches.sort(key=lambda x: int(x[0]))
                result_list = [match[1].strip() for match in matches]

        # Fallback to original line-splitting approach
        if len(result_list) != paragraph_count:
            lines = text.splitlines()
            result_list = [line.strip() for line in lines if line.strip() != ""]

        return result_list

    def set_deployment_id(self, deployment_id):
        self.deployment_id = deployment_id
        self.openai_client = AzureOpenAI(
            api_key=next(self.keys),
            azure_endpoint=self.api_base,
            api_version="2023-07-01-preview",
            azure_deployment=self.deployment_id,
        )

    def set_gpt35_models(self, ollama_model=""):
        if ollama_model:
            self.model_list = cycle([ollama_model])
            return
        # gpt3 all models for save the limit
        if self.deployment_id:
            self.model_list = cycle(["gpt-35-turbo"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(GPT35_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_gpt4_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["gpt-4"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(GPT4_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_gpt4omini_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["gpt-4o-mini"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(GPT4oMINI_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_gpt4o_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["gpt-4o"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(GPT4o_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_o1preview_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["o1-preview"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(O1PREVIEW_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_o1_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["o1"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(O1_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_o1mini_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["o1-mini"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(O1MINI_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_o3mini_models(self):
        # for issue #375 azure can not use model list
        if self.deployment_id:
            self.model_list = cycle(["o3-mini"])
        else:
            my_model_list = [
                i["id"] for i in self.openai_client.models.list().model_dump()["data"]
            ]
            model_list = list(set(my_model_list) & set(O3MINI_MODEL_LIST))
            print(f"Using model list {model_list}")
            self.model_list = cycle(model_list)

    def set_model_list(self, model_list):
        model_list = list(set(model_list))
        print(f"Using model list {model_list}")
        self.model_list = cycle(model_list)

    def batch_init(self, book_name):
        self.book_name = self.sanitize_book_name(book_name)

    def add_to_batch_translate_queue(self, book_index, text):
        self.batch_text_list.append({"book_index": book_index, "text": text})

    def sanitize_book_name(self, book_name):
        # Replace any characters that are not alphanumeric, underscore, hyphen, or dot with an underscore
        sanitized_book_name = re.sub(r"[^\w\-_\.]", "_", book_name)
        # Remove leading and trailing underscores and dots
        sanitized_book_name = sanitized_book_name.strip("._")
        return sanitized_book_name

    def batch_metadata_file_path(self):
        return os.path.join(os.getcwd(), "batch_files", f"{self.book_name}_info.json")

    def batch_dir(self):
        return os.path.join(os.getcwd(), "batch_files", self.book_name)

    def custom_id(self, book_index):
        return f"{self.book_name}-{book_index}"

    def is_completed_batch(self):
        batch_metadata_file_path = self.batch_metadata_file_path()

        if not os.path.exists(batch_metadata_file_path):
            print("Batch result file does not exist")
            raise Exception("Batch result file does not exist")

        with open(batch_metadata_file_path, "r", encoding="utf-8") as f:
            batch_info = json.load(f)

        for batch_file in batch_info["batch_files"]:
            batch_status = self.check_batch_status(batch_file["batch_id"])
            if batch_status.status != "completed":
                return False

        return True

    def batch_translate(self, book_index):
        if self.batch_info_cache is None:
            batch_metadata_file_path = self.batch_metadata_file_path()
            with open(batch_metadata_file_path, "r", encoding="utf-8") as f:
                self.batch_info_cache = json.load(f)

        batch_info = self.batch_info_cache
        target_batch = None
        for batch in batch_info["batch_files"]:
            if batch["start_index"] <= book_index < batch["end_index"]:
                target_batch = batch
                break

        if not target_batch:
            raise ValueError(f"No batch found for book_index {book_index}")

        if target_batch["batch_id"] in self.result_content_cache:
            result_content = self.result_content_cache[target_batch["batch_id"]]
        else:
            batch_status = self.check_batch_status(target_batch["batch_id"])
            if batch_status.output_file_id is None:
                raise ValueError(f"Batch {target_batch['batch_id']} is not completed")
            result_content = self.get_batch_result(batch_status.output_file_id)
            self.result_content_cache[target_batch["batch_id"]] = result_content

        result_lines = result_content.text.split("\n")
        custom_id = self.custom_id(book_index)
        for line in result_lines:
            if line.strip():
                result = json.loads(line)
                if result["custom_id"] == custom_id:
                    return result["response"]["body"]["choices"][0]["message"][
                        "content"
                    ]

        raise ValueError(f"No result found for custom_id {custom_id}")

    def create_batch_context_messages(self, index):
        messages = []
        if self.context_flag:
            if index % CHATGPT_CONFIG[
                "batch_context_update_interval"
            ] == 0 or not hasattr(self, "cached_context_messages"):
                context_messages = []
                for i in range(index - 1, -1, -1):
                    item = self.batch_text_list[i]
                    if len(item["text"].split()) >= 100:
                        context_messages.append(item["text"])
                        if len(context_messages) == self.context_paragraph_limit:
                            break

                if len(context_messages) == self.context_paragraph_limit:
                    print("Creating cached context messages")
                    self.cached_context_messages = [
                        {"role": "user", "content": "\n".join(context_messages)},
                        {
                            "role": "assistant",
                            "content": self.get_translation(
                                "\n".join(context_messages)
                            ),
                        },
                    ]

            if hasattr(self, "cached_context_messages"):
                messages.extend(self.cached_context_messages)

        return messages

    def make_batch_request(self, book_index, text):
        messages = self.create_messages(
            text, self.create_batch_context_messages(book_index)
        )
        return {
            "custom_id": self.custom_id(book_index),
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                # model shuould not be rotate
                "model": self.batch_model,
                "messages": messages,
                "temperature": self.temperature,
            },
        }

    def create_batch_files(self, dest_file_path):
        file_paths = []
        # max request 50,000 and max size 100MB
        lines_per_file = 40000
        current_file = 0

        for i in range(0, len(self.batch_text_list), lines_per_file):
            current_file += 1
            file_path = os.path.join(dest_file_path, f"{current_file}.jsonl")
            start_index = i
            end_index = i + lines_per_file

            # TODO: Split the file if it exceeds 100MB
            with open(file_path, "w", encoding="utf-8") as f:
                for text in self.batch_text_list[i : i + lines_per_file]:
                    batch_req = self.make_batch_request(
                        text["book_index"], text["text"]
                    )
                    json.dump(batch_req, f, ensure_ascii=False)
                    f.write("\n")
            file_paths.append(
                {
                    "file_path": file_path,
                    "start_index": start_index,
                    "end_index": end_index,
                }
            )

        return file_paths

    def batch(self):
        self.rotate_model()
        self.batch_model = self.model
        # current working directory
        batch_dir = self.batch_dir()
        batch_metadata_file_path = self.batch_metadata_file_path()
        # cleanup batch dir and result file
        if os.path.exists(batch_dir):
            shutil.rmtree(batch_dir)
        if os.path.exists(batch_metadata_file_path):
            os.remove(batch_metadata_file_path)
        os.makedirs(batch_dir, exist_ok=True)
        # batch execute
        batch_files = self.create_batch_files(batch_dir)
        batch_info = []
        for batch_file in batch_files:
            file_id = self.upload_batch_file(batch_file["file_path"])
            batch = self.batch_execute(file_id)
            batch_info.append(
                self.create_batch_info(
                    file_id, batch, batch_file["start_index"], batch_file["end_index"]
                )
            )
        # save batch info
        batch_info_json = {
            "book_id": self.book_name,
            "batch_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch_files": batch_info,
        }
        with open(batch_metadata_file_path, "w", encoding="utf-8") as f:
            json.dump(batch_info_json, f, ensure_ascii=False, indent=2)

    def create_batch_info(self, file_id, batch, start_index, end_index):
        return {
            "input_file_id": file_id,
            "batch_id": batch.id,
            "start_index": start_index,
            "end_index": end_index,
            "prefix": self.book_name,
        }

    def upload_batch_file(self, file_path):
        batch_input_file = self.openai_client.files.create(
            file=open(file_path, "rb"), purpose="batch"
        )
        return batch_input_file.id

    def batch_execute(self, file_id):
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        res = self.openai_client.batches.create(
            input_file_id=file_id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={
                "description": f"Batch job for {self.book_name} at {current_time}"
            },
        )
        if res.errors:
            print(res.errors)
            raise Exception(f"Batch execution failed: {res.errors}")
        return res

    def check_batch_status(self, batch_id):
        return self.openai_client.batches.retrieve(batch_id)

    def get_batch_result(self, output_file_id):
        return self.openai_client.files.content(output_file_id)
