import itertools
from abc import ABC, abstractmethod

# Special delimiter for batch translation - UUID-based token unlikely to appear in any text
BATCH_DELIMITER = "\n\n@@\n\n"


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
        Translate a list of texts. Default implementation translates one by one.
        Subclasses can override for batch efficiency.
        """
        return [self.translate(t) for t in text_list]
