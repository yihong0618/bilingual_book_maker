
from abc import ABC, abstractmethod


class TranslateEngineBase(ABC):
    @abstractmethod
    def __init__(self, key: str, lang: str, *args, **kwargs):
        pass

    @abstractmethod
    def translate(self, text) -> str:
        pass


class FileEngineBase(ABC):
    @abstractmethod
    def __init__(self, engine: TranslateEngineBase, *args, **kwargs):
        pass

    @abstractmethod
    def make_bilingual_book(self):
        pass
