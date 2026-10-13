"""Typed LLM errors. The router uses these to decide on failover."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for all LLM provider errors."""


class LLMTimeoutError(LLMError):
    """The provider did not answer in time."""


class LLMAuthError(LLMError):
    """Missing or invalid credentials (401/403)."""


class LLMRateLimitError(LLMError):
    """Rate limit hit (429)."""


class LLMUnavailableError(LLMError):
    """Provider unreachable or failing (connection refused, 5xx)."""
