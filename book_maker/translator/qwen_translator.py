import re
import time
from os import environ
from rich import print
from openai import OpenAI

from .base_translator import Base


class QwenTranslator(Base):
    """
    Qwen-MT translator using Alibaba Cloud's DashScope API
    Specialized translation model supporting 92 languages with advanced features
    Official documentation: https://help.aliyun.com/document_detail/2860790.html

    Todo: support more languages, terminology, and domain hints
    """

    # Language mapping from bilingual_book_maker format to Qwen language codes
    LANGUAGE_MAP = {
        # Common languages
        "english": "English",
        "chinese": "Chinese",
        "simplified chinese": "Chinese",
        "traditional chinese": "Traditional Chinese",
        "japanese": "Japanese",
        "korean": "Korean",
        "spanish": "Spanish",
        "french": "French",
        "german": "German",
        "portuguese": "Portuguese",
        "italian": "Italian",
        "russian": "Russian",
        "arabic": "Arabic",
        "hindi": "Hindi",
        "thai": "Thai",
        "vietnamese": "Vietnamese",
        "indonesian": "Indonesian",
        "malay": "Malay",
        "dutch": "Dutch",
        "turkish": "Turkish",
        "polish": "Polish",
        "czech": "Czech",
        "hungarian": "Hungarian",
        "romanian": "Romanian",
        "greek": "Greek",
        "hebrew": "Hebrew",
        "finnish": "Finnish",
        "danish": "Danish",
        "swedish": "Swedish",
        "norwegian": "Norwegian Bokmål",
        "ukrainian": "Ukrainian",
        "bulgarian": "Bulgarian",
        "serbian": "Serbian",
        "croatian": "Croatian",
        "slovenian": "Slovenian",
        "slovak": "Slovak",
        "lithuanian": "Lithuanian",
        "latvian": "Latvian",
        "estonian": "Estonian",
        # Add more mappings as needed
    }

    def __init__(
        self,
        key,
        language,
        model="qwen-mt-turbo",
        source_lang="auto",
        api_base=None,
        prompt_template=None,  # Not used for translation models
        prompt_sys_msg=None,  # Not used for translation models
        temperature=None,  # Not used for translation models
        context_flag=False,
        context_paragraph_limit=5,
        terminology=None,
        domain_hint=None,
        **kwargs,
    ) -> None:
        super().__init__(key, language)

        # API configuration
        self.api_base = api_base or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        self.client = OpenAI(
            api_key=next(self.keys), base_url=self.api_base, timeout=60
        )

        # Model configuration
        self.model = self.set_qwen_model(model)
        self.source_lang = source_lang
        self.target_lang = self._map_language(language)

        # Advanced features
        self.terminology = self.set_terminology(terminology)
        self.domain_hint = self.set_domain_hint(domain_hint)

        # Context/Translation memory support
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        self.context_paragraph_limit = context_paragraph_limit

        print(f"[bold blue]Qwen Translator initialized:[/bold blue]")
        print(f"  Model: {self.model}")
        print(f"  Source Language: {self.source_lang}")
        print(f"  Target Language: {self.target_lang}")
        if self.domain_hint:
            print(f"  Domain Hint: {self.domain_hint}")

    def rotate_key(self):
        """Rotate API key for load balancing"""
        try:
            self.client.api_key = next(self.keys)
        except StopIteration:
            pass

    def _map_language(self, language):
        """Map language name to Qwen language format"""
        language_lower = language.lower().strip()

        # Direct mapping
        if language_lower in self.LANGUAGE_MAP:
            return self.LANGUAGE_MAP[language_lower]

        # Try partial matching for common variations
        for key, value in self.LANGUAGE_MAP.items():
            if language_lower in key or key in language_lower:
                return value

        # Fallback to original language name with proper capitalization
        return language.title()

    def _create_translation_options(self):
        """Create translation options for the API request"""
        options = {"source_lang": self.source_lang, "target_lang": self.target_lang}

        # Add terminology if provided
        if self.terminology and len(self.terminology) > 0:
            options["terms"] = self.terminology

        # Add domain hint if provided (must be in English)
        if self.domain_hint and len(self.domain_hint) > 0:
            options["domains"] = self.domain_hint

        # Add translation memory if context is enabled
        if self.context_flag and self.context_list:
            tm_list = []
            for src, tgt in zip(self.context_list, self.context_translated_list):
                tm_list.append({"source": src, "target": tgt})
            if tm_list:
                options["tm_list"] = tm_list

        return options

    def save_context(self, text, t_text):
        """Save the current translation pair to context for translation memory"""
        if not self.context_flag:
            return

        self.context_list.append(text)
        self.context_translated_list.append(t_text)

        # Keep only the most recent paragraphs within the limit
        if len(self.context_list) > self.context_paragraph_limit:
            self.context_list.pop(0)
            self.context_translated_list.pop(0)

    def translate(self, text, needprint=True):
        """Main translation method"""
        start_time = time.time()

        if needprint:
            print(re.sub(r"\n{3,}", "\n\n", text))

        attempt_count = 0
        max_attempts = 3
        t_text = ""

        while attempt_count < max_attempts:
            try:
                self.rotate_key()

                # Prepare messages
                messages = [{"role": "user", "content": text}]

                # Create translation options
                translation_options = self._create_translation_options()

                # Make API request
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    extra_body={"translation_options": translation_options},
                )

                # Extract translated text
                if completion.choices[0].message.content:
                    t_text = completion.choices[0].message.content.strip()
                else:
                    t_text = ""

                # Save to context for translation memory
                if self.context_flag and t_text:
                    self.save_context(text, t_text)

                break

            except Exception as e:
                attempt_count += 1
                print(
                    f"[red]Translation attempt {attempt_count} failed: {str(e)}[/red]"
                )

                if attempt_count >= max_attempts:
                    print(
                        f"[red]Translation failed after {max_attempts} attempts[/red]"
                    )
                    t_text = text  # Fallback to original text
                else:
                    time.sleep(1)  # Wait before retry

        if needprint:
            print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")

        end_time = time.time()
        print(f"[dim]Translation time: {end_time - start_time:.2f}s[/dim]")

        return t_text

    def set_terminology(self, terminology):
        """Set custom terminology for translation

        Args:
            terminology: List of dict with 'source' and 'target' keys
                        e.g., [{"source": "API", "target": "应用程序接口"}]
        """
        self.terminology = terminology or []
        print(f"[blue]Terminology updated: {len(self.terminology)} terms[/blue]")

    def set_domain_hint(self, domain_hint):
        """Set domain hint for specialized translation

        Args:
            domain_hint: String describing the domain in English
                        e.g., "Technical documentation for software development"
        """
        self.domain_hint = domain_hint or ""
        print(f"[blue]Domain hint set: {self.domain_hint}[/blue]")

    def set_qwen_model(self, model_name):
        """Set Qwen model type

        Args:
            model_name: Either "qwen-mt-turbo" or "qwen-mt-plus"
        """
        if model_name in ["qwen-mt-turbo", "qwen-mt-plus"]:
            self.model = model_name
            print(f"[blue]Qwen model set to: {self.model}[/blue]")
        else:
            self.model = "qwen-mt-turbo"
            print(
                f"[red]Invalid Qwen model: {model_name}. Using default: {self.model}[/red]"
            )
