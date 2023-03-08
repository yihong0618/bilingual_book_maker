from abc import ABC, abstractmethod


class BaseBookLoader(ABC):
    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace()

    @abstractmethod
    def _make_new_book(self, book):
        pass

    @abstractmethod
    def make_bilingual_book(self):
        pass

    @abstractmethod
    def load_state(self):
        pass

    @abstractmethod
    def _save_temp_book(self):
        pass

    @abstractmethod
    def _save_progress(self):
        pass
