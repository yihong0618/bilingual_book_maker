from .base_translator import Base


class DeepL(Base):
    def __init__(self, session, key, api_base=None):
        super().__init__(session, key, api_base=api_base)

    def translate(self, text):
        return super().translate(text)
