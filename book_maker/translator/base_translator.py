import itertools
from abc import ABC, abstractmethod


class Base(ABC):
    def __init__(self, key, language,terminology_filename):
        self.keys = itertools.cycle(key.split(","))
        self.language = language
        self.terminology_filename=terminology_filename

    @abstractmethod
    def rotate_key(self):
        pass

    @abstractmethod
    def translate(self, text):
        pass
