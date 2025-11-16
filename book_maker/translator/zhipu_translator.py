import re
import time
from openai import OpenAI, APIError
from rich import print
from .chatgptapi_translator import ChatGPTAPI


GLM_MODEL_LIST = [
    "GLM-4-Flash",
    "GLM-4-Air",
    "GLM-4-AirX",
    "GLM-4-Plus",
    "GLM-4-0520",
]


class ZhipuTranslator(ChatGPTAPI):
    """
    Zhipu AI (GLM) translator using OpenAI-compatible API
    Supports GLM-4 series models including free tier GLM-4-Flash
    API documentation: https://open.bigmodel.cn/dev/api
    """

    def __init__(self, key, language, api_base=None, **kwargs) -> None:
        super().__init__(key, language, **kwargs)
        self.model_list = [GLM_MODEL_LIST[0]]  # Default to GLM-4-Flash
        self.model = GLM_MODEL_LIST[0]  # Set default model
        self.api_url = str(api_base) if api_base else "https://open.bigmodel.cn/api/paas/v4/"
        self.openai_client = OpenAI(api_key=next(self.keys), base_url=self.api_url)

    def rotate_key(self):
        """Rotate API key for load balancing"""
        try:
            new_key = next(self.keys)
            self.openai_client = OpenAI(api_key=new_key, base_url=self.api_url)
        except StopIteration:
            pass

    def rotate_model(self):
        """Set the current model from model list"""
        self.model = self.model_list[0]

    def translate(self, text, needprint=True):
        """
        Override translate method to handle Zhipu-specific content filter errors

        When content is filtered (error code 1301), return original text and continue
        instead of crashing the entire translation process.
        """
        start_time = time.time()

        if needprint:
            print(re.sub(r"\n{3,}", "\n\n", text))

        attempt_count = 0
        max_attempts = 3
        t_text = ""

        while attempt_count < max_attempts:
            try:
                t_text = self.get_translation(text)
                break
            except APIError as e:
                # Check if this is a content filter error (Zhipu error code 1301)
                error_message = str(e)
                if "'code': '1301'" in error_message or "敏感内容" in error_message:
                    print(
                        f"[yellow]⚠ Content filter triggered - skipping this paragraph[/yellow]"
                    )
                    print(
                        f"[dim]Zhipu AI detected potentially sensitive content. Using original text.[/dim]"
                    )
                    t_text = text  # Return original text
                    break
                else:
                    # For other API errors, use default handling
                    print(f"[red]API Error: {error_message}[/red]")
                    attempt_count += 1
                    if attempt_count >= max_attempts:
                        print(
                            f"[red]Translation failed after {max_attempts} attempts. Using original text.[/red]"
                        )
                        t_text = text
                        break
                    time.sleep(1)
            except Exception as e:
                print(f"[yellow]Translation error: {str(e)}[/yellow]")
                print(f"[dim]Using original text for this paragraph.[/dim]")
                t_text = text  # Return original text instead of None
                break

        if needprint and t_text and t_text != text:
            print("[bold green]" + re.sub(r"\n{3,}", "\n\n", t_text) + "[/bold green]")

        elapsed_time = time.time() - start_time
        if needprint:
            print(f"[dim]Translation time: {elapsed_time:.2f}s[/dim]")

        return t_text

    def set_glm_model(self, model_name):
        """
        Set specific GLM model

        Args:
            model_name: Model identifier (e.g., 'glm-4-flash', 'glm', etc.)
        """
        # Map model identifiers to actual model names
        model_mapping = {
            "glm": "GLM-4-Flash",
            "glm-4-flash": "GLM-4-Flash",
            "glm-4-air": "GLM-4-Air",
            "glm-4-airx": "GLM-4-AirX",
            "glm-4-plus": "GLM-4-Plus",
            "glm-4-0520": "GLM-4-0520",
        }

        actual_model = model_mapping.get(model_name.lower())

        if actual_model and actual_model in GLM_MODEL_LIST:
            self.model_list = [actual_model]
            self.model = actual_model
            print(f"[blue]GLM model set to: {actual_model}[/blue]")
        else:
            # Fallback to default
            self.model_list = [GLM_MODEL_LIST[0]]
            self.model = GLM_MODEL_LIST[0]
            print(
                f"[yellow]Invalid GLM model: {model_name}. Using default: {self.model}[/yellow]"
            )

        return self.model
