import itertools
from abc import ABC, abstractmethod

# Special delimiter for batch translation - unlikely to appear in normal text
BATCH_DELIMITER = "%%"


class Base(ABC):
    def __init__(self, key, language) -> None:
        self.keys = itertools.cycle(key.split(","))
        self.language = language

    @abstractmethod
    def rotate_key(self):
        pass

    @abstractmethod
    def translate(self, text):
        pass

    def set_deployment_id(self, deployment_id):
        pass

    def translate_list(self, text_list):
        """
        Translate multiple texts in a single batch request.
        Uses special delimiters to preserve paragraph structure.
        Returns a list of translated texts.

        Default implementation falls back to individual translations.
        Subclasses should override for true batch support.
        """
        # Fallback: translate individually
        return [self.translate(text) for text in text_list]
