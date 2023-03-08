import itertools
from abc import ABC, abstractmethod


class Base(ABC):
    def __init__(self, key, language):
        self.keys = itertools.cycle(key.split(","))
        self.language = language

    @abstractmethod
    def rotate_key(self):
        pass

    @abstractmethod
    def translate(self, text):
        pass
