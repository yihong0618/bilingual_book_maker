import os
from abc import ABC, abstractmethod


class BaseBookLoader(ABC):
    # Raised to True on the instance by `announce_saved_book`. A class
    # attribute so no loader needs an __init__ of its own to own the flag.
    _announced_saved_book = False

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace()

    def announce_saved_book(self, path):
        """The last line of a finished run: the file it produced, in full.

        Every writer below names its output relative to the source book, so
        a caller that started somewhere else — a script, an agent, a shell in
        another directory — had to reconstruct where the file landed, and a
        run that wrote nothing looked exactly like one that did. Called at
        the point the file exists, on the success path only, and at most once
        per run: several writers may write the same book (a batched epub run
        writes it twice), and one run produces one announcement.

        Builtin `print`, deliberately, not `rich.print`: rich wraps at the 80
        columns it assumes for a pipe, and a path broken across two lines is
        the one thing this line exists to hand a caller intact.
        """
        if self._announced_saved_book:
            return
        self._announced_saved_book = True
        print(f"Bilingual book saved: {os.path.abspath(path)}")

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
