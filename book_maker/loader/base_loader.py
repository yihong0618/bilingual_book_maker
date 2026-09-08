import json
import os
from abc import ABC, abstractmethod


class BaseBookLoader(ABC):
    # Raised to True on the instance by `announce_saved_book`. A class
    # attribute so no loader needs an __init__ of its own to own the flag.
    _announced_saved_book = False

    # What `save_file` joins its content lines with. The text writers all
    # join with a newline; srt separates its blocks with a blank line.
    SAVE_FILE_SEPARATOR = "\n"

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace()

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write(self.SAVE_FILE_SEPARATOR.join(content))
        except Exception as e:
            raise Exception("can not save file") from e

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

    def load_state(self):
        """The resume file as a list of already-translated pieces.

        JSON, with a plain-lines fallback for the files earlier versions
        wrote. Overridden where a loader keeps another shape on disk.
        """
        try:
            with open(self.bin_path, encoding="utf-8") as f:
                content = f.read()
                try:
                    state = json.loads(content)
                except json.JSONDecodeError:
                    state = content.splitlines()
                if not isinstance(state, list):
                    raise ValueError("resume file must contain a list")
                self.p_to_save = state
        except Exception as e:
            raise Exception("can not load resume file") from e

    @abstractmethod
    def _save_temp_book(self):
        pass

    def _save_progress(self):
        """Write the resume file `load_state` reads back."""
        try:
            with open(self.bin_path, "w", encoding="utf-8") as f:
                json.dump(self.p_to_save, f, ensure_ascii=False)
        except Exception as e:
            raise Exception("can not save resume file") from e
