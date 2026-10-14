"""Errors of the memory layer that the clients show to the user as they are."""

from __future__ import annotations


class MemoryUnavailableError(Exception):
    """The memory database cannot be used (down, refused the login, driver missing).

    The message is written for the user, in Portuguese, and says what to do about it, so a
    client prints it instead of a stacktrace.
    """
