import re
import requests
from rich import print


from .base_translator import Base


class Google(Base):
    """
    google translate
    """

    def __init__(self, key, language, **kwargs) -> None:
        super().__init__(key, language)
        self.api_url = "https://translate.google.com/translate_a/single?client=it&dt=qca&dt=t&dt=rmt&dt=bd&dt=rms&dt=sos&dt=md&dt=gt&dt=ld&dt=ss&dt=ex&otf=2&dj=1&hl=en&ie=UTF-8&oe=UTF-8&sl=auto&tl=zh-CN"
        self.headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "GoogleTranslate/6.29.59279 (iPhone; iOS 15.4; en; iPhone14,2)",
        }
        # TODO support more models here
        self.session = requests.session()
        self.language = language

    def rotate_key(self):
        pass

    def translate(self, text):
        print(text)
        """r = self.session.post(
            self.api_url,
            headers=self.headers,
            data=f"q={requests.utils.quote(text)}",
        )
        if not r.ok:
            return text
        t_text = "".join(
            [sentence.get("trans", "") for sentence in r.json()["sentences"]],
        )"""
        t_text = self._retry_translate(text)
        print("[bold green]" + re.sub("\n{3,}", "\n\n", t_text) + "[/bold green]")
        return t_text

    def _retry_translate(self, text, timeout=3):
        time = 0
        while time <= timeout:
            time += 1
            r = self.session.post(
                self.api_url,
                headers=self.headers,
                data=f"q={requests.utils.quote(text)}",
                timeout=3,
            )
            if r.ok:
                t_text = "".join(
                    [sentence.get("trans", "") for sentence in r.json()["sentences"]],
                )
                return t_text
        return text
