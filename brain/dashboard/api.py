"""The read-only JSON API of the dashboard. Routes are registered by path.

Each route is ``async def (query) -> JSON-able``, where ``query`` maps a parameter to its list of
values. A route can only read; it signals a problem with ``ApiError`` (a typed answer, never an
exception that reaches the connection).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

Query = Mapping[str, list[str]]
Route = Callable[[Query], Awaitable[Any]]


class ApiError(Exception):
    """A request the API will not answer (the message is fit for the user)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Api:
    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}

    def route(self, path: str, handler: Route) -> None:
        self._routes[path] = handler

    def paths(self) -> list[str]:
        return sorted(self._routes)

    async def handle(self, path: str, query: Query) -> tuple[int, Any]:
        handler = self._routes.get(path)
        if handler is None:
            return 404, {"error": "não existe", "path": path}
        try:
            return 200, await handler(query)
        except ApiError as exc:
            return exc.status, {"error": exc.message}


def first(query: Query, name: str, default: str | None = None) -> str | None:
    values = query.get(name)
    return values[0] if values else default


def default_api() -> Api:
    """The API with its routes. Later tasks add the memory routes here."""
    api = Api()

    async def session(query: Query) -> Any:
        return {"ok": True}

    api.route("/session", session)
    return api
