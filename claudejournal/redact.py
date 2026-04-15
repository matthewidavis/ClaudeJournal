import re


class Redactor:
    def __init__(self, patterns: list[str]):
        self.regexes = [re.compile(p) for p in patterns]

    def scrub(self, text: str) -> str:
        if not text:
            return text
        for rx in self.regexes:
            text = rx.sub("[REDACTED]", text)
        return text
