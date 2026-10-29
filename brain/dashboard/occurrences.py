"""The occurrences of the dashboard (T-168): leaks the user reported and leaks the audit found.

Two sources, one list:

* **relato**: what the user marked with ``/vazou`` or "isso vazou" (``leak_reports``), with the last
  ten exchanges of the conversation;
* **automatico**: what the audit caught (``leak_audit``), with the model's raw text, the text that
  was delivered and the message that started it.

Everything here is a pure function over the stored documents; the routes read the stores and call
these, and the same rows feed the screen and the spreadsheet. Ids leave the dashboard shortened to
``ID_CHARS`` characters (the stores find a document by prefix): a full 32-character id looks like a
token to the secret filter that guards everything the dashboard sends.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

from brain.config import settings
from brain.dashboard.xlsx import Cell, Sheet

ID_CHARS: Final = 16
KINDS: Final = ("relato", "automatico")
USER: Final = "Usuário"
HER: Final = "Juniper"
HER_RAW: Final = "Juniper (texto bruto do modelo)"
PREVIEW_CHARS: Final = 140
RAW_NOTE: Final = "o modelo escreveu isto antes do guarda cortar"

_DATE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_SYSTEM_HINT = re.compile(r"\s*\[Sistema:[^\]]*\]\s*$")


def short_id(document: Mapping[str, Any]) -> str:
    return str(document["_id"])[:ID_CHARS]


def _plain(text: object) -> str:
    return " ".join(str(text or "").split())


def _preview(text: object) -> str:
    flat = _plain(text)
    return flat if len(flat) <= PREVIEW_CHARS else f"{flat[:PREVIEW_CHARS]}…"


def _user_text(text: object) -> str:
    """The user's message without the date stamp the system puts before it."""
    return _SYSTEM_HINT.sub("", _DATE_PREFIX.sub("", str(text or ""), count=1))


def local(moment: Any) -> str:
    """``29/10/2026 14:32:05`` in the agent's time zone (a string passes through unchanged)."""
    if not isinstance(moment, datetime):
        return str(moment or "")
    return moment.astimezone(ZoneInfo(settings.agent.timezone)).strftime("%d/%m/%Y %H:%M:%S")


# --- messages -------------------------------------------------------------------------------


def report_messages(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One row per message of the last exchanges: the user's, then hers (if she said anything)."""
    rows: list[dict[str, Any]] = []
    for exchange in report.get("exchanges") or []:
        rows.append({"author": USER, "text": exchange.get("user") or "", "at": exchange.get("at")})
        reply = exchange.get("assistant") or ""
        if reply or exchange.get("interrupted"):
            rows.append(
                {
                    "author": HER,
                    "text": reply,
                    "at": exchange.get("at"),
                    "note": "você cortou a resposta" if exchange.get("interrupted") else "",
                }
            )
    return rows


def audit_messages(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """What started the call, what was delivered and, if different, what the model wrote."""
    at = record.get("at")
    rows = [{"author": USER, "text": _user_text(record.get("user_message")), "at": at}]
    delivered, raw = record.get("delivered") or "", record.get("raw") or ""
    rows.append({"author": HER, "text": delivered, "at": at, "note": "entregue"})
    if raw and raw != delivered:
        rows.append({"author": HER_RAW, "text": raw, "at": at, "note": RAW_NOTE})
    return rows


def messages_of(kind: str, document: Mapping[str, Any]) -> list[dict[str, Any]]:
    return report_messages(document) if kind == "relato" else audit_messages(document)


# --- list items -----------------------------------------------------------------------------


def item(kind: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """The summary of one occurrence, for the list."""
    messages = messages_of(kind, document)
    hers = [m["text"] for m in messages if m["author"] == HER and m["text"]]
    base: dict[str, Any] = {
        "id": short_id(document),
        "kind": kind,
        "at": document.get("at"),
        "session": document.get("session"),
        "channel": document.get("channel"),
        "voice": bool(document.get("voice")),
        "messages": len(messages),
        "preview": _preview(hers[-1] if hers else ""),
    }
    if kind == "relato":
        return {
            **base,
            "note": document.get("note") or "",
            "audit": [str(i)[:ID_CHARS] for i in document.get("audit") or []],
        }
    return {
        **base,
        "note": "",
        "rules": list(document.get("rules") or []),
        "leaked": bool(document.get("leaked")),
        "quarantined": bool(document.get("quarantined")),
        "provider": document.get("provider"),
        "model": document.get("model"),
        "finish_reason": document.get("finish_reason"),
    }


def detail(kind: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """One occurrence, whole: the list item, its messages and (automatic) what fired."""
    out = item(kind, document)
    out["messages_list"] = messages_of(kind, document)
    out["commit"] = document.get("commit")
    out["version"] = document.get("version")
    if kind == "automatico":
        out["hits"] = list(document.get("hits") or [])
        out["blocks"] = list(document.get("blocks") or [])
        out["tool_calls"] = list(document.get("tool_calls") or [])
    return out


# --- spreadsheet ----------------------------------------------------------------------------


def workbook(occurrences: Sequence[tuple[str, Mapping[str, Any]]]) -> list[Sheet]:
    """The two sheets of the export: every message (one row each) and the occurrences themselves.

    ``occurrences`` are ``(kind, document)`` pairs, newest first.
    """
    messages: list[list[Cell]] = []
    summary: list[list[Cell]] = []
    for kind, document in occurrences:
        info = item(kind, document)
        label = "Relato (/vazou)" if kind == "relato" else "Detectado"
        common: list[Cell] = [
            info["id"],
            label,
            info["session"],
            info["channel"],
            "sim" if info["voice"] else "não",
            local(info["at"]),
        ]
        rows = messages_of(kind, document)
        for order, row in enumerate(rows, start=1):
            messages.append([*common, order, row["author"], row["text"], row.get("note") or ""])
        summary.append(
            [
                *common,
                info["note"],
                ", ".join(info.get("rules") or []),
                ("sim" if info.get("leaked") else "não") if kind == "automatico" else "",
                info.get("model") or "",
                document.get("commit") or "",
                len(rows),
            ]
        )
    return [
        Sheet(
            "Mensagens",
            [
                "Ocorrência",
                "Tipo",
                "ID da sessão",
                "Canal",
                "Voz",
                "Data da ocorrência",
                "Ordem",
                "Autor",
                "Mensagem",
                "Observação",
            ],
            messages,
            widths=[18, 16, 36, 12, 6, 20, 8, 28, 90, 30],
            wrap=frozenset({8, 9}),
        ),
        Sheet(
            "Ocorrências",
            [
                "Ocorrência",
                "Tipo",
                "ID da sessão",
                "Canal",
                "Voz",
                "Data da ocorrência",
                "Nota",
                "Regras",
                "O texto vazou até você",
                "Modelo",
                "Commit",
                "Mensagens",
            ],
            summary,
            widths=[18, 16, 36, 12, 6, 20, 40, 34, 14, 28, 14, 11],
            wrap=frozenset({6, 7}),
        ),
    ]
