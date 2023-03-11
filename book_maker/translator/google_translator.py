import requests

from .base_translator import Base


class Google(Base):
    """
    google translate
    """

    def __init__(self, key, language, **kwargs):
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
        r = self.session.post(
            self.api_url,
            headers=self.headers,
            data="q={text}".format(text=requests.utils.quote(text)),
        )
        if not r.ok:
            return text
        t_text = "".join(
            [sentence.get("trans", "") for sentence in r.json()["sentences"]]
        )
        print(t_text)
        return t_text
