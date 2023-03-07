from abc import abstractmethod


class Base:
    def __init__(self, key, language, api_base=None):
        self.key = key
        self.language = language
        self.current_key_index = 0

    def get_key(self, key_str):
        keys = key_str.split(",")
        key = keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(keys)
        return key

    @abstractmethod
    def translate(self, text):
        pass
