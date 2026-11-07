"""A failure with a stable code that the API and the UI understand."""

from __future__ import annotations


class AttemptFailure(RuntimeError):
    def __init__(self, code: str, detail: str, retryable: bool = False) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable
