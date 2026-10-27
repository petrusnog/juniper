"""Contrato do protocolo cliente ↔ brain (ADR-012, T-074)."""

from __future__ import annotations

import base64
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
    (
        "server",
        {
            "type": "trace",
            "layer": "Servidor",
            "text": "Recebi.",
            "at": "2026-10-16T01:12:03-03:00",
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
    # --- voz (ADR-023) ---
    (
        "client",
        {
            "type": "voice.start",
            "id": "v-1",
            "format": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            "local_stt": True,
            "local_tts": True,
        },
    ),
    (
        "server",
        {
            "type": "voice.ready",
            "reply_to": "v-1",
            "input": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
            "output": {"encoding": "pcm_s16le", "sample_rate": 24000, "channels": 1},
            "stt": "cloud",
            "tts": "cloud",
        },
    ),
    ("client", {"type": "voice.audio", "seq": 7, "data": "AAECAwQF"}),
    (
        "server",
        {"type": "voice.audio", "seq": 0, "data": "AAECAwQF", "turn_id": "t-9", "last": True},
    ),
    ("server", {"type": "voice.state", "state": "speaking", "turn_id": "t-9"}),
    ("server", {"type": "voice.transcript", "text": "toca Djavan", "final": True}),
    ("server", {"type": "voice.say", "turn_id": "t-9", "text": "Tocando Oceano.", "last": True}),
    (
        "server",
        {"type": "voice.fallback", "component": "stt", "mode": "local", "reason": "quota"},
    ),
    ("client", {"type": "voice.text", "id": "v-2", "text": "toca Djavan"}),
    ("client", {"type": "voice.interrupt"}),
    ("client", {"type": "voice.end", "reason": "client"}),
    ("server", {"type": "voice.end", "reason": "silence"}),
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


CLIENT = {"kind": "cli", "version": "0.1.0", "device_id": "d-1"}


@pytest.mark.parametrize(
    "resume",
    [{}, {"session_id": "a", "latest": True}, {"latest": False}, {"session_id": ""}],
)
def test_resume_needs_exactly_one_target(resume: dict[str, object]) -> None:
    hello = {"type": "hello", "protocol_version": 1, "client": CLIENT, "resume": resume}
    with pytest.raises(p.InvalidMessageError):
        p.parse_message(source="client", raw=hello)
    with pytest.raises(p.InvalidMessageError):
        p.parse_message(source="client", raw={"type": "session.resume", "id": "s-1", **resume})


def test_resume_accepts_either_target() -> None:
    for resume in ({"session_id": "abc"}, {"latest": True}):
        hello = p.parse_message(
            source="client",
            raw={"type": "hello", "protocol_version": 1, "client": CLIENT, "resume": resume},
        )
        assert isinstance(hello, p.Hello) and hello.resume is not None


# --- voz (ADR-023) -------------------------------------------------------------------------


def test_voice_audio_travels_both_ways_and_decodes_to_pcm() -> None:
    pcm = bytes(range(10))
    frame = {"type": "voice.audio", "seq": 3, "data": base64.b64encode(pcm).decode()}
    for source in ("client", "server"):
        message = p.parse_message(json.dumps(frame), source=source)  # type: ignore[arg-type]
        assert (
            isinstance(message, p.VoiceAudio) and message.pcm() == pcm and message.turn_id is None
        )


def test_voice_end_travels_both_ways_and_defaults_to_the_client_reason() -> None:
    assert p.parse_message('{"type":"voice.end"}', source="client") == p.VoiceEnd(reason="client")
    assert p.parse_message(
        '{"type":"voice.end","reason":"command"}', source="server"
    ) == p.VoiceEnd(reason="command")


@pytest.mark.parametrize(
    ("raw", "source"),
    [
        (
            '{"type":"voice.ready","reply_to":"v","input":{"sample_rate":16000},"output":{"sample_rate":24000},"stt":"cloud","tts":"cloud"}',
            "client",
        ),
        ('{"type":"voice.state","state":"idle"}', "client"),
        ('{"type":"voice.transcript","text":"x"}', "client"),
        ('{"type":"voice.say","turn_id":"t","text":"x"}', "client"),
        ('{"type":"voice.fallback","component":"stt","mode":"local","reason":"quota"}', "client"),
        ('{"type":"voice.start","id":"v","format":{"sample_rate":16000}}', "server"),
        ('{"type":"voice.text","id":"v","text":"x"}', "server"),
        ('{"type":"voice.interrupt"}', "server"),
    ],
)
def test_voice_messages_are_only_accepted_in_their_direction(raw: str, source: str) -> None:
    with pytest.raises(p.InvalidMessageError, match="não é aceito nesta direção"):
        p.parse_message(raw, source=source)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "voice.audio", "seq": 0, "data": "isto não é base64!"},
        {"type": "voice.audio", "seq": 0, "data": "AAE"},  # bad padding
        {"type": "voice.audio", "seq": -1, "data": "AAEC"},
        {"type": "voice.audio", "seq": 0, "data": "A" * 200_004},  # over the frame limit
        {"type": "voice.audio", "data": "AAEC"},  # no seq
        {"type": "voice.start", "id": "v", "format": {"sample_rate": 100}},
        {"type": "voice.start", "id": "v", "format": {"sample_rate": 16000, "channels": 2}},
        {"type": "voice.start", "id": "v", "format": {"sample_rate": 16000, "encoding": "mp3"}},
        {"type": "voice.start", "format": {"sample_rate": 16000}},  # no id
        {"type": "voice.text", "id": "v", "text": "x" * 32_001},
        {"type": "voice.end", "reason": "porque sim"},
    ],
)
def test_bad_voice_frames_are_refused_with_a_typed_error(frame: dict[str, Any]) -> None:
    with pytest.raises(p.InvalidMessageError):
        p.parse_message(frame, source="client")


def test_a_voice_frame_stays_inside_the_message_limit() -> None:
    biggest = {"type": "voice.audio", "seq": 1, "data": "A" * 200_000}
    message = p.parse_message(biggest, source="client")
    assert len(p.serialize_message(message).encode()) < p.MAX_MESSAGE_BYTES


def test_voice_error_codes_are_not_retryable_and_part_of_the_schema() -> None:
    for code in ("voice_unavailable", "voice_busy"):
        assert p.RETRYABLE[code] is False
        assert p.Error.from_code(code, "x").retryable is False  # type: ignore[arg-type]
    assert {"voice_unavailable", "voice_busy"} <= set(p.json_schema()["x-error-codes"])


def test_protocol_version_did_not_change_with_voice() -> None:
    assert p.PROTOCOL_VERSION == 1  # compatible extension (ADR-012, section 12)
    hello = {
        "type": "hello",
        "protocol_version": 1,
        "client": {"kind": "mobile", "version": "0.1.0", "device_id": "d"},
    }
    assert p.parse_message(hello, source="client").client.kind == "mobile"  # type: ignore[union-attr]


def test_an_old_server_view_still_ignores_unknown_voice_fields() -> None:
    message = p.parse_message('{"type":"voice.interrupt","novo":1}', source="client")
    assert isinstance(message, p.VoiceInterrupt)
