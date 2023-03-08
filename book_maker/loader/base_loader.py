from abc import abstractmethod


class BaseBookLoader:
    def __init__(
        self,
        epub_name,
        model,
        key,
        resume,
        language,
        model_api_base=None,
        is_test=False,
        test_num=5,
    ):
        pass

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
