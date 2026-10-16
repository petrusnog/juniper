"""Contrato do protocolo cliente ↔ brain (ADR-012, T-074)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from brain.memory.errors import MemoryUnavailableError

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "docs" / "protocol" / "schema.json"

# Um exemplo por mensagem do ADR-012: (origem, quadro).
EXAMPLES: list[tuple[str, dict[str, Any]]] = [
    (
        "client",
        {
            "type": "hello",
            "protocol_version": 1,
            "client": {
                "kind": "desktop",
                "version": "0.1.0",
                "device_id": "a1b2c3",
                "device_name": "PC do Petrus",
            },
            "resume": {"session_id": "20261015-101500-ab12cd"},
        },
    ),
    (
        "server",
        {
            "type": "welcome",
            "protocol_version": 1,
            "server_version": "4.0.0",
            "heartbeat_seconds": 30,
            "session": {"id": "20261015-101500-ab12cd", "resumed": True, "turns": 6},
        },
    ),
    ("server", {"type": "ping"}),
    ("client", {"type": "pong"}),
    (
        "server",
        {
            "type": "error",
            "reply_to": "c-1",
            "code": "turn_in_progress",
            "message": "Um momento.",
            "retryable": True,
        },
    ),
    ("client", {"type": "chat.send", "id": "c-7f3a", "text": "o que tenho amanhã?"}),
    (
        "server",
        {"type": "chat.started", "reply_to": "c-7f3a", "turn_id": "t-12", "session_id": "s"},
    ),
    ("server", {"type": "chat.token", "turn_id": "t-12", "text": "Amanhã você tem "}),
    (
        "server",
        {
            "type": "chat.end",
            "turn_id": "t-12",
            "finish": "ok",
            "usage": {"total_tokens": 1840},
        },
    ),
    ("client", {"type": "chat.cancel", "turn_id": "t-12"}),
    ("client", {"type": "session.new", "id": "s-1"}),
    ("client", {"type": "session.resume", "id": "s-2", "latest": True}),
    ("client", {"type": "session.list", "id": "s-3", "limit": 10}),
    (
        "server",
        {
            "type": "session.state",
            "reply_to": "s-2",
            "session_id": "s",
            "turns": 2,
            "history": [{"role": "user", "text": "oi"}, {"role": "assistant", "text": "olá"}],
            "note": "Conversa retomada.",
        },
    ),
    (
        "server",
        {
            "type": "session.listed",
            "reply_to": "s-3",
            "sessions": [
                {"id": "s", "updated_at": "2026-10-15T10:15:00Z", "turns": 6, "preview": "oi"}
            ],
        },
    ),
    ("server", {"type": "session.detached", "session_id": "s", "reason": "taken"}),
    (
        "client",
        {
            "type": "memory.request",
            "id": "m-1",
            "op": "search",
            "args": {"query": "framework php", "limit": 5, "threshold": 0.8},
        },
    ),
    ("client", {"type": "memory.request", "id": "m-2", "op": "list", "args": {"all": True}}),
    ("client", {"type": "memory.request", "id": "m-3", "op": "forget", "args": {"id": "mem_1"}}),
    ("client", {"type": "memory.request", "id": "m-4", "op": "export", "args": {"vectors": True}}),
    ("client", {"type": "memory.request", "id": "m-5", "op": "profile.get", "args": {}}),
    (
        "client",
        {"type": "memory.request", "id": "m-6", "op": "profile.set", "args": {"text": "Sou o P."}},
    ),
    ("client", {"type": "memory.request", "id": "m-7", "op": "profile.forget", "args": {}}),
    (
        "server",
        {
            "type": "memory.result",
            "reply_to": "m-1",
            "ok": True,
            "data": {"results": [{"id": "mem_1", "text": "Laravel 11.", "score": 0.86}]},
        },
    ),
    (
        "client",
        {
            "type": "register",
            "tools": [
                {
                    "name": "open_url",
                    "description": "Abre uma URL.",
                    "parameters": {"type": "object"},
                    "tier": "safe",
                }
            ],
        },
    ),
    (
        "server",
        {
            "type": "registered",
            "tools": [
                {"name": "open_url", "description": "Abre.", "parameters": {}, "tier": "safe"}
            ],
        },
    ),
    ("client", {"type": "tools.request", "id": "t-1"}),
    (
        "server",
        {
            "type": "tools.listed",
            "reply_to": "t-1",
            "tools": [
                {"name": "recall", "description": "Busca.", "tier": "read", "locality": "cloud"}
            ],
        },
    ),
    (
        "server",
        {
            "type": "invoke",
            "call_id": "k-9",
            "tool_name": "open_url",
            "arguments": {"url": "https://www.youtube.com"},
            "timeout_ms": 10000,
        },
    ),
    (
        "client",
        {
            "type": "result",
            "call_id": "k-9",
            "success": True,
            "output": "Abri https://www.youtube.com no navegador.",
        },
    ),
]


@pytest.mark.parametrize(
    ("source", "frame"), EXAMPLES, ids=[f["type"] + str(i) for i, (_, f) in enumerate(EXAMPLES)]
)
def test_round_trip(source: str, frame: dict[str, Any]) -> None:
    message = p.parse_message(json.dumps(frame), source=source)  # type: ignore[arg-type]
    assert json.loads(p.serialize_message(message)) == frame
    assert p.parse_message(p.serialize_message(message), source=source) == message  # type: ignore[arg-type]


def test_every_message_type_has_an_example() -> None:
    covered = {frame["type"] for _, frame in EXAMPLES}
    assert covered == p._CLIENT_TYPES | p._SERVER_TYPES


def test_unknown_fields_are_ignored() -> None:
    message = p.parse_message('{"type":"chat.cancel","turn_id":"t","novo":1}')
    assert isinstance(message, p.ChatCancel)


@pytest.mark.parametrize(
    "raw",
    [
        "não é json",
        "[1, 2]",
        '{"sem_tipo": 1}',
        '{"type": 7}',
        '{"type": "coisa.nova"}',
        '{"type": "chat.send", "id": "c"}',  # falta text
        '{"type": "chat.end", "turn_id": "t", "finish": "talvez"}',
        '{"type": "memory.request", "id": "m", "op": "reindex", "args": {}}',
        '{"type": "invoke", "call_id": "k", "tool_name": "x", "timeout_ms": 0}',
    ],
)
def test_malformed_messages_raise_typed_error(raw: str) -> None:
    with pytest.raises(p.InvalidMessageError) as info:
        p.parse_message(raw)
    assert info.value.code == "bad_request"


def test_text_and_frame_limits() -> None:
    too_long = {"type": "chat.send", "id": "c", "text": "a" * (p.MAX_CHAT_TEXT_CHARS + 1)}
    with pytest.raises(p.InvalidMessageError):
        p.parse_message(too_long)
    with pytest.raises(p.InvalidMessageError):
        p.parse_message(b" " * (p.MAX_MESSAGE_BYTES + 1))


def test_direction_is_enforced() -> None:
    with pytest.raises(p.InvalidMessageError):
        p.parse_message({"type": "chat.token", "turn_id": "t", "text": "x"}, source="client")
    with pytest.raises(p.InvalidMessageError):
        p.parse_message({"type": "chat.send", "id": "c", "text": "x"}, source="server")


@pytest.mark.parametrize("version", [0, 2, "1", None, True])
def test_incompatible_version_raises_typed_error(version: object) -> None:
    hello = {
        "type": "hello",
        "protocol_version": version,
        "client": {"kind": "cli", "version": "1", "device_id": "d"},
    }
    with pytest.raises(p.UnsupportedVersionError) as info:
        p.parse_message(hello)
    assert info.value.code == "unsupported_version"
    welcome = {
        "type": "welcome",
        "protocol_version": version,
        "server_version": "4",
        "heartbeat_seconds": 30,
        "session": {"id": "s", "resumed": False, "turns": 0},
    }
    with pytest.raises(p.UnsupportedVersionError):
        p.parse_message(welcome)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (LLMAuthError(), "llm_auth"),
        (LLMRateLimitError(), "llm_rate_limit"),
        (LLMUnavailableError(), "llm_unavailable"),
        (LLMTimeoutError(), "llm_timeout"),
        (LLMError(), "llm_error"),
        (MemoryUnavailableError("fora"), "memory_unavailable"),
        (p.InvalidMessageError("x"), "bad_request"),
        (p.UnsupportedVersionError(9), "unsupported_version"),
        (RuntimeError("boom"), "internal"),
    ],
)
def test_error_codes_follow_exception_hierarchy(exc: Exception, code: str) -> None:
    assert p.error_code_for(exc) == code


def test_error_from_exception_sets_retryable() -> None:
    err = p.Error.from_exception(LLMRateLimitError(), "Muitas requisições.", reply_to="c-1")
    assert (err.code, err.retryable, err.reply_to) == ("llm_rate_limit", True, "c-1")
    assert p.Error.from_exception(LLMAuthError(), "x").retryable is False


def test_every_error_code_is_mapped() -> None:
    assert set(p.RETRYABLE) == set(p.ErrorCode.__args__)  # type: ignore[attr-defined]


def test_schema_is_versioned_and_covers_all_messages() -> None:
    schema = p.json_schema()
    assert schema["x-protocol-version"] == p.PROTOCOL_VERSION
    defs = schema["$defs"]
    for name in ("Hello", "Welcome", "ChatSend", "Invoke", "Result", "MemorySearchRequest"):
        assert name in defs


def test_schema_file_is_up_to_date() -> None:
    assert SCHEMA_PATH.exists(), "rode scripts/export_protocol_schema.py"
    assert SCHEMA_PATH.read_text(encoding="utf-8") == p.schema_text(), (
        "docs/protocol/schema.json desatualizado: rode scripts/export_protocol_schema.py"
    )


def test_export_script_check_mode() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_protocol_schema.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout


def test_core_does_not_import_clients() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "brain").rglob("*.py")
        if "clients" not in path.parts
        and path.name != "__main__.py"
        and any(
            line.strip().startswith(("from brain.clients", "import brain.clients"))
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    ]
    assert offenders == []
