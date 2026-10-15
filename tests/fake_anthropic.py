"""Anthropic SDK errors and the free token-count call, for tests that never reach the API."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic

KEY = "sk-ant-test-0123456789"

WORKSPACE = (
    "This API key is not scoped to a workspace, so this request must include the "
    "anthropic-workspace-id header with the ID of the workspace to use."
)
NO_CREDIT = (
    "Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits."
)


def api_error(
    status: int, text: str, kind: str = "invalid_request_error"
) -> anthropic.APIStatusError:
    response = SimpleNamespace(request=None, status_code=status, headers={})
    body = {"type": "error", "error": {"type": kind, "message": text}}
    cls = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
    }.get(status, anthropic.APIStatusError)
    return cls(f"Error code: {status}", response=response, body=body)  # type: ignore[arg-type]


class TokenCounter:
    """``sdk.with_options(...).messages.count_tokens(...)``, the free call ``check_key`` makes."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.messages = self
        self.models: list[str] = []
        self.options: dict[str, Any] = {}

    def with_options(self, **options: Any) -> TokenCounter:
        self.options = options
        return self

    async def count_tokens(self, *, model: str, messages: Any, timeout: float) -> Any:
        self.models.append(model)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(input_tokens=8)

    async def close(self) -> None:
        pass
