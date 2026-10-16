"""Cliente de teste do servidor: conecta, conversa e mostra o que chega (T-080).

    python scripts/smoke_server.py --token-file ~/token "oi, tudo bem?"
    python scripts/smoke_server.py --token "$TOKEN" --resume "e você, lembra do que eu disse?"
    python scripts/smoke_server.py --health

Serve para ver, de fora do contêiner, a arquitetura real funcionando: handshake, streaming,
retomada de conversa e o `/health`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
import uuid
from pathlib import Path

from websockets.asyncio.client import connect


def _health(url: str) -> int:
    http = url.replace("ws://", "http://").replace("wss://", "https://").split("/v1/ws")[0]
    with urllib.request.urlopen(f"{http}/health", timeout=5) as response:
        print(response.read().decode())
        return 0 if response.status == 200 else 1


async def _chat(url: str, token: str, text: str, resume: bool, device: str) -> int:
    async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}) as ws:
        hello = {
            "type": "hello",
            "protocol_version": 1,
            "client": {"kind": "cli", "version": "smoke", "device_id": device},
        }
        if resume:
            hello["resume"] = {"latest": True}
        await ws.send(json.dumps(hello))
        welcome = json.loads(await ws.recv())
        print(f"[welcome] {json.dumps(welcome['session'])}", file=sys.stderr)
        await ws.send(json.dumps({"type": "chat.send", "id": "smoke-1", "text": text}))
        while True:
            message = json.loads(await ws.recv())
            if message["type"] == "ping":
                await ws.send(json.dumps({"type": "pong"}))
            elif message["type"] == "chat.token":
                print(message["text"], end="", flush=True)
            elif message["type"] == "error":
                print(f"\n[erro {message['code']}] {message['message']}", file=sys.stderr)
            elif message["type"] == "chat.end":
                print(f"\n[fim: {message['finish']}]", file=sys.stderr)
                return 0 if message["finish"] == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", nargs="?", default="oi")
    parser.add_argument("--url", default="ws://127.0.0.1:8765/v1/ws")
    parser.add_argument("--token")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--resume", action="store_true", help="retoma a conversa mais recente")
    parser.add_argument("--device", default=f"smoke-{uuid.uuid4().hex[:6]}")
    parser.add_argument("--health", action="store_true", help="só consulta o /health")
    args = parser.parse_args()
    if args.health:
        return _health(args.url)
    token = args.token or (args.token_file.read_text().strip() if args.token_file else "")
    if not token:
        parser.error("informe --token ou --token-file")
    return asyncio.run(_chat(args.url, token, args.message, args.resume, args.device))


if __name__ == "__main__":
    raise SystemExit(main())
