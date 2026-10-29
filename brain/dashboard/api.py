"""The read-only JSON API of the dashboard. Routes are registered by path.

Each route is ``async def (query) -> JSON-able``, where ``query`` maps a parameter to its list of
values. A route can only read; it signals a problem with ``ApiError`` (a typed answer, never an
exception that reaches the connection).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import unquote

from brain.dashboard.serialize import sanitize

Query = Mapping[str, list[str]]
Route = Callable[[Query], Awaitable[Any]]
FileRoute = Callable[[Query], Awaitable[tuple[str, str, bytes]]]
"""A download: ``(file name, content type, bytes)``. Read-only like every route."""


class ApiError(Exception):
    """A request the API will not answer (the message is fit for the user)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class Api:
    """Exact routes (``/memories``) and prefix routes (``/memories/``), where the rest of the
    path reaches the handler as ``query["_id"]``. Everything returned goes through ``sanitize``."""

    def __init__(self) -> None:
        self._routes: dict[str, Route] = {}
        self._prefixes: dict[str, Route] = {}
        self._files: dict[str, FileRoute] = {}

    def route(self, path: str, handler: Route) -> None:
        (self._prefixes if path.endswith("/") else self._routes)[path] = handler

    def file_route(self, path: str, handler: FileRoute) -> None:
        self._files[path] = handler

    def paths(self) -> list[str]:
        return sorted([*self._routes, *self._prefixes, *self._files])

    def is_file(self, path: str) -> bool:
        return path in self._files

    async def handle_file(
        self, path: str, query: Query
    ) -> tuple[int, dict[str, Any] | tuple[str, str, bytes]]:
        """A download, or ``(status, {"error": ...})`` when the route cannot answer."""
        handler = self._files.get(path)
        if handler is None:
            return 404, {"error": "não existe", "path": path}
        try:
            return 200, await handler(query)
        except ApiError as exc:
            return exc.status, {"error": exc.message}

    async def handle(self, path: str, query: Query) -> tuple[int, Any]:
        handler = self._routes.get(path)
        params = query
        if handler is None:
            for prefix in sorted(self._prefixes, key=len, reverse=True):
                rest = path.removeprefix(prefix)
                if path.startswith(prefix) and rest and "/" not in rest:
                    handler, params = self._prefixes[prefix], {**query, "_id": [unquote(rest)]}
                    break
        if handler is None:
            return 404, {"error": "não existe", "path": path}
        try:
            return 200, sanitize(await handler(params))
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
