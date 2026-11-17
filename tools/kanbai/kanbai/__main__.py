"""CLI: ``python -m kanbai migrate | serve | project | agent``.

Credentials come from the environment (KANBAI_DATABASE_URL or KANBAI_PG_*), never from code.
"""

from __future__ import annotations

import argparse
import sys

from kanbai.board import Board
from kanbai.domain import Project, Role
from kanbai.modelmap import ModelMap
from kanbai.pgstore import PgStore


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kanbai")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")  # in a container: 0.0.0.0 + published on 127.0.0.1
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--models", help="TOML file points -> model (default: the packaged one)")
    proj = sub.add_parser("project")
    proj.add_argument("id")
    proj.add_argument("--name", required=True)
    proj.add_argument("--repo", required=True, help="repository path as seen by the host worker")
    proj.add_argument("--base", default="master")
    proj.add_argument("--prefix", default="feature/")
    agent = sub.add_parser("agent")
    agent.add_argument("name")
    agent.add_argument("--role", required=True, choices=[r.value for r in Role if r != Role.HUMAN])
    args = ap.parse_args(argv)

    store = PgStore()
    store.migrate()
    from pathlib import Path

    board = Board(store, ModelMap.load(Path(args.models) if getattr(args, "models", None) else None))
    if args.cmd == "migrate":
        print("schema applied")
    elif args.cmd == "project":
        res = board.create_project(Project(args.id, args.name, args.repo, args.base, args.prefix))
        print("ok" if res.ok else res.error)
        return 0 if res.ok else 1
    elif args.cmd == "agent":
        made = board.register_agent(args.name, Role(args.role))
        token = board.issue_token(made.data.id).data  # type: ignore[attr-defined]
        print(f"agent {made.data.id} ({args.role}); token (shown once): {token}")  # type: ignore[attr-defined]
    elif args.cmd == "serve":
        import uvicorn

        from kanbai.mcp_server import build_app

        uvicorn.run(build_app(board), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
