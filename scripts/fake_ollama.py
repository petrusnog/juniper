"""Ollama de mentira para testar o servidor sem chave de LLM (smoke test do Docker, T-080).

    python scripts/fake_ollama.py --port 11999

Responde a `POST /api/chat` em stream (NDJSON) repetindo a última mensagem do usuário, e a
`GET /api/tags` (health check). Aponte o brain para ele com
`JUNIPER_LLM__PRIORITY='["ollama"]'` e `JUNIPER_LLM__OLLAMA__URL=http://<host>:11999`.
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_DATE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # silencioso
        return

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"models": []}')

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        users = [m["content"] for m in body.get("messages", []) if m.get("role") == "user"]
        last = _DATE_PREFIX.sub("", users[-1]) if users else ""
        reply = f"Eco ({len(users)}): {last}"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for piece in re.findall(r"\S+\s*", reply):
            line = {"message": {"role": "assistant", "content": piece}, "done": False}
            self.wfile.write((json.dumps(line) + "\n").encode())
        done = {
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
        }
        self.wfile.write((json.dumps(done) + "\n").encode())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11999)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
