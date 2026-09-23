from __future__ import annotations

import logging
import re

ACCOUNT_RE = re.compile(r"(?<!\d)(\d[\d -]{5,}\d)(?!\d)")


def redact_message(message: str, include_sensitive: bool = False) -> str:
    if include_sensitive:
        return message

    def repl(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(1))
        return "***" + digits[-4:] if len(digits) >= 4 else "***"

    return ACCOUNT_RE.sub(repl, message)


class RedactingFormatter(logging.Formatter):
    def __init__(self, *args, include_sensitive: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.include_sensitive = include_sensitive

    def format(self, record: logging.LogRecord) -> str:
        return redact_message(super().format(record), self.include_sensitive)


def configure_logging(verbose: bool = False, include_sensitive: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(levelname)s: %(message)s", include_sensitive=include_sensitive))
    root = logging.getLogger("pdfbank")
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
