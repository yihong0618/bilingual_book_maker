import itertools
from abc import ABC, abstractmethod


class Base(ABC):
    def __init__(self, key, language):
        self.keys = itertools.cycle(key.split(","))
        self.language = language

    def get_key(self):
        return next(self.keys)

    @abstractmethod
    def translate(self, text):
        pass
