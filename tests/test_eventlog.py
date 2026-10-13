"""Tests for the structured event log (T-031) and its hooks in agent, dispatcher and router."""

from __future__ import annotations

import json
import stat
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import SecretStr

from brain.config import settings
from brain.eventlog import REDACTED, EventLog, event_log, redact
from brain.llm.errors import LLMUnavailableError
from brain.llm.router import LLMRouter
from brain.llm.types import Message
from tests.test_agent import call, make_agent, no, say, yes
from tests.test_llm_router import ScriptedProvider


def read_events(directory: Path) -> list[dict[str, Any]]:
    lines: list[str] = []
    for file in sorted(directory.glob("*.jsonl")):
        lines += file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_redact_masks_secret_keys_and_values() -> None:
    data = {
        "api_key": "abc",
        "nested": {"Authorization": "Bearer abcdefgh12345", "ok": "texto"},
        "text": "chave gsk_ABCDEFGHIJKLMNOP1234 e Bearer xyzxyzxyz123 vazou",
        "list": [{"password": "x"}, "gsk_ABCDEFGHIJKLMNOP1234"],
    }
    out = json.dumps(redact(data))
    for leaked in ('abc"', "abcdefgh12345", "gsk_ABCDEF", "xyzxyzxyz123"):
        assert leaked not in out
    assert "texto" in out and REDACTED in out


def test_redact_masks_the_configured_groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_api_key", SecretStr("minha-chave-secreta-9"))
    assert "minha-chave" not in json.dumps(redact({"msg": "erro com minha-chave-secreta-9 ali"}))


def test_redact_cuts_long_strings_and_handles_odd_types() -> None:
    out = redact({"big": "x" * 5000, "t": (1, 2), "p": Path("/a"), "n": None})
    assert len(out["big"]) < 2100 and "chars" in out["big"]
    assert out["t"] == [1, 2] and out["p"] == "/a" and out["n"] is None


def test_log_writes_one_file_per_day_with_private_mode(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "logs")
    log.log("tool_call", name="x", args={"token": "s"})
    log.log("error", message="boom")
    today = datetime.now(ZoneInfo(settings.agent.timezone))
    path = log.path_for(today)
    assert path.exists() and path.name.endswith(".jsonl")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    events = read_events(tmp_path / "logs")
    assert [e["event"] for e in events] == ["tool_call", "error"]
    assert events[0]["args"] == {"token": REDACTED}
    assert events[0]["run"] == log.run_id and "ts" in events[0]


def test_log_never_raises_and_can_be_disabled(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("não é diretório")
    EventLog(blocker / "logs").log("error", message="x")  # must not raise
    off = EventLog(tmp_path / "off")
    off.enabled = False
    off.log("error")
    assert not (tmp_path / "off").exists()


async def test_agent_logs_llm_and_tool_events() -> None:
    agent, _, _ = make_agent([call("echo", text="oi"), say("pronto")])
    await agent.run("faz")
    events = read_events(event_log.directory)
    kinds = [e["event"] for e in events]
    assert kinds == ["llm_request", "llm_response", "tool_call", "llm_request", "llm_response"]
    response = events[1]
    assert response["tool_calls"] == ["echo"] and response["latency_ms"] >= 0
    tool = events[2]
    assert tool["name"] == "echo" and tool["tier"] == "read" and tool["decision"] == "auto"
    assert tool["success"] is True and tool["args"] == {"text": "oi"}


@pytest.mark.parametrize(
    ("tool", "confirm", "decision"),
    [
        ("write_thing", yes, "confirmed"),
        ("write_thing", no, "denied"),
        ("nuke", yes, "forbidden"),
        ("ghost", yes, "unknown"),
        ("echo", yes, "invalid"),  # missing required 'text'
        ("boom", yes, "crashed"),
    ],
)
async def test_tool_call_decisions_are_logged(tool: str, confirm: Any, decision: str) -> None:
    agent, _, _ = make_agent([call(tool), say("ok")], confirm=confirm)
    await agent.run("faz")
    events = [e for e in read_events(event_log.directory) if e["event"] == "tool_call"]
    assert events[0]["decision"] == decision


async def test_tool_crash_is_logged_as_error_without_leaking_to_llm() -> None:
    agent, _, _ = make_agent([call("boom"), say("ok")])
    await agent.run("faz")
    errors = [e for e in read_events(event_log.directory) if e["event"] == "error"]
    assert errors and errors[0]["type"] == "RuntimeError" and errors[0]["where"] == "tool.boom"


async def test_llm_failure_is_logged_as_error() -> None:
    agent, _, _ = make_agent([])
    with pytest.raises(LLMUnavailableError):
        await agent.run("oi")
    errors = [e for e in read_events(event_log.directory) if e["event"] == "error"]
    assert errors[0]["where"] == "agent.run" and errors[0]["type"] == "LLMUnavailableError"


async def test_router_failover_is_logged() -> None:
    router = LLMRouter(
        [ScriptedProvider("a", error=LLMUnavailableError("fora")), ScriptedProvider("b")]
    )
    await router.chat([Message("user", "oi")])
    events = [e for e in read_events(event_log.directory) if e["event"] == "failover"]
    assert events and events[0]["provider"] == "a" and events[0]["type"] == "LLMUnavailableError"


async def test_logged_args_never_contain_user_secrets() -> None:
    agent, _, _ = make_agent([call("echo", text="Bearer abcdefghijkl"), say("ok")])
    await agent.run("faz")
    assert "abcdefghijkl" not in (
        event_log.directory / next(p.name for p in event_log.directory.glob("*.jsonl"))
    ).read_text(encoding="utf-8")


def test_redact_keeps_token_counts_but_masks_tokens() -> None:
    out = redact({"prompt_tokens": 10, "total_tokens": 12, "access_token": "x", "token": "y"})
    assert out == {
        "prompt_tokens": 10,
        "total_tokens": 12,
        "access_token": REDACTED,
        "token": REDACTED,
    }
