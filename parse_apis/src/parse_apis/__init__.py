"""Parse typed APIs.

Generated, typed clients for your Parse APIs appear here as submodules after
`parse sync`:

    from parse_apis.<slug> import <Root>

Runtime errors are re-exported from `parse_sdk` so callers can
`from parse_apis import RateLimitError` without importing the engine directly.
This file is the committed scaffold — `parse sync` only writes the generated
payload around it.
"""
from __future__ import annotations

from parse_sdk import (
    ParseError,
    AuthError,
    ForbiddenError,
    NotFoundError,
    BadRequestError,
    QuotaExceededError,
    RateLimitError,
    UpstreamError,
    PaginationError,
    PaginationLimitError,
    RequestMeta,
)

__all__ = [
    "ParseError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "BadRequestError",
    "QuotaExceededError",
    "RateLimitError",
    "UpstreamError",
    "PaginationError",
    "PaginationLimitError",
    "RequestMeta",
]


def __getattr__(name: str):
    # Submodule imports (`from parse_apis.<slug> import ...`) are handled by the
    # import system; this only fires for attribute access on the package itself.
    raise AttributeError(
        f"module 'parse_apis' has no attribute {name!r}. "
        f"If {name!r} is one of your Parse APIs, run `uv run parse sync` to generate it."
    )
