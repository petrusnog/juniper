"""From a technical event to a friendly activity entry (T-221).

``translate(event, fields)`` returns the :class:`Entry` for an event the ``event_log`` wrote, or
None for the ones that are only noise for a person (timings, embeddings, raw HTTP). Every text is
marked with ``N_`` so ``scripts/i18n.py`` finds it and the ``.po`` catalogs can translate it.
Unknown events are not lost: they become a generic "recorded the event X" entry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from brain.activity.model import Entry
from brain.i18n import N_

Fields = Mapping[str, Any]

# Events that say nothing a person audits (they stay in the technical log).
SKIPPED: Final = frozenset(
    {
        "timing",
        "embedding",
        "embedding_model_load",
        "memory_warmup",
        "memory_request",
        "http_request",
        "http_response",
        "http_error",
        "llm_request",
    }
)


def _text(value: object, limit: int = 120) -> str:
    flat = " ".join(str(value or "").split())
    return flat if len(flat) <= limit else f"{flat[:limit]}…"


def _seconds(ms: object) -> float:
    return round(float(ms) / 1000, 1) if isinstance(ms, int | float) else 0.0


def _domain(url: object) -> str:
    text = str(url or "")
    host = text.split("//", 1)[-1].split("/", 1)[0]
    return host or text


def _tokens(usage: object) -> int | None:
    if isinstance(usage, Mapping):
        total = usage.get("total_tokens")
        return int(total) if isinstance(total, int | float) else None
    return None


# --- tools --------------------------------------------------------------------------------------

Tool = Callable[[Fields], tuple[str, dict[str, Any], str | None]]
"""``(msgid, params, target)`` for a tool that worked."""


def _music_search(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    query = _text(a.get("query"))
    return (
        N_("Searched {query} on YouTube Music"),
        {"query": query},
        query,
    )


def _play_music(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    query = _text(a.get("query"))
    return N_("Played {query} on YouTube Music"), {"query": query}, query


def _open_url(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    url = _text(a.get("url"), 200)
    return N_("Opened the link {url}"), {"url": url}, url


def _web_search(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    query = _text(a.get("query"))
    return N_("Searched the web for {query}"), {"query": query}, query


def _web_fetch(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    url = _text(a.get("url"), 200)
    return N_("Read the page {url}"), {"url": url}, url


def _remember(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    text = _text(a.get("text"))
    return N_("Saved to your memory: {text}"), {"text": text}, text


def _recall(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    query = _text(a.get("query"))
    return N_("Looked in your memory for {query}"), {"query": query}, query


def _forget(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    what = _text(a.get("query") or a.get("id"))
    return N_("Forgot from your memory: {what}"), {"what": what}, what


def _update_profile(_: Fields) -> tuple[str, dict[str, Any], str | None]:
    return N_("Updated your profile"), {}, None


def _create_reminder(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title") or a.get("text"))
    return N_("Created the reminder {title}"), {"title": title}, title


def _list_reminders(_: Fields) -> tuple[str, dict[str, Any], str | None]:
    return N_("Listed your reminders"), {}, None


def _update_reminder(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title") or a.get("reminder_id"))
    return N_("Changed the reminder {title}"), {"title": title}, title


def _delete_reminder(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("reminder_id"))
    return N_("Deleted the reminder {title}"), {"title": title}, title


def _list_events(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    start = _text(a.get("start_date"))
    return N_("Looked at your calendar from {start}"), {"start": start or "?"}, start or None


def _create_event(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title"))
    return (
        N_("Created the calendar event {title} for {start}"),
        {"title": title, "start": _text(a.get("start"))},
        title,
    )


def _update_event(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title") or a.get("event_id"))
    return N_("Changed the calendar event {title}"), {"title": title}, title


def _delete_event(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("event_id"))
    return N_("Deleted the calendar event {title}"), {"title": title}, title


def _path_tool(message: str) -> Tool:
    def build(a: Fields) -> tuple[str, dict[str, Any], str | None]:
        path = _text(a.get("path") or a.get("source"), 200)
        return message, {"path": path}, path

    return build


def _music_playlist_create(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title"))
    return N_("Created the playlist {title}"), {"title": title}, title


def _music_playlist_add(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    count = len(a.get("tracks") or a.get("queries") or []) or 1
    return (
        N_("Added {count} track to a playlist", "Added {count} tracks to a playlist"),
        {"count": count},
        None,
    )


def _music_feedback(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title"))
    return N_("Recorded your opinion about {title}"), {"title": title}, title


def _report_issue(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    title = _text(a.get("title"))
    return N_("Registered the occurrence {title}"), {"title": title}, title


def _learn_topic(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    topic = _text(a.get("topic") or a.get("query"))
    return N_("Studied the topic {topic}"), {"topic": topic}, topic


def _docs_search(a: Fields) -> tuple[str, dict[str, Any], str | None]:
    query = _text(a.get("query"))
    return N_("Searched the documentation for {query}"), {"query": query}, query


TOOLS: Final[dict[str, tuple[str, Tool | None]]] = {
    "music_search": ("music", _music_search),
    "play_music": ("music", _play_music),
    "music_playlists": ("music", lambda _: (N_("Listed your playlists"), {}, None)),
    "music_playlist_create": ("music", _music_playlist_create),
    "music_playlist_add": ("music", _music_playlist_add),
    "music_feedback": ("music", _music_feedback),
    "open_url": ("web", _open_url),
    "web_search": ("web", _web_search),
    "web_fetch": ("web", _web_fetch),
    "remember": ("memory", _remember),
    "recall": ("memory", _recall),
    "forget": ("memory", _forget),
    "update_profile": ("memory", _update_profile),
    "learn_topic": ("memory", _learn_topic),
    "create_reminder": ("reminder", _create_reminder),
    "list_reminders": ("reminder", _list_reminders),
    "update_reminder": ("reminder", _update_reminder),
    "delete_reminder": ("reminder", _delete_reminder),
    "list_events": ("calendar", _list_events),
    "create_event": ("calendar", _create_event),
    "update_event": ("calendar", _update_event),
    "delete_event": ("calendar", _delete_event),
    "fs_list": ("files", _path_tool(N_("Listed the folder {path}"))),
    "fs_search": ("files", _path_tool(N_("Searched for files in {path}"))),
    "fs_read": ("files", _path_tool(N_("Read the file {path}"))),
    "fs_write": ("files", _path_tool(N_("Wrote the file {path}"))),
    "fs_mkdir": ("files", _path_tool(N_("Created the folder {path}"))),
    "fs_move": ("files", _path_tool(N_("Moved {path}"))),
    "fs_copy": ("files", _path_tool(N_("Copied {path}"))),
    "fs_replace": ("files", _path_tool(N_("Replaced the file {path}"))),
    "fs_trash": ("files", _path_tool(N_("Sent {path} to the trash"))),
    "report_issue": ("system", _report_issue),
    "docs_search": ("system", _docs_search),
}

# What a tool call says when it did not simply work: asked first, denied, or failed.
_PENDING = N_("Wanted to use {tool} on {target}, but left it pending until you confirm")
_PENDING_BARE = N_("Wanted to use {tool}, but left it pending until you confirm")
_DENIED = N_("Did not use {tool} on {target}: you denied the confirmation")
_DENIED_BARE = N_("Did not use {tool}: you denied the confirmation")
_FAILED = N_("Tried to use {tool} on {target}, but it failed: {error}")
_FAILED_BARE = N_("Tried to use {tool}, but it failed: {error}")
_GENERIC = N_("Used the tool {tool}")


def tool_call(f: Fields) -> Entry:
    name = str(f.get("name") or "?")
    raw_args = f.get("args")
    args: Mapping[str, Any] = raw_args if isinstance(raw_args, Mapping) else {}
    category, builder = TOOLS.get(name, ("tool", None))
    key, params, target = builder(args) if builder else (_GENERIC, {"tool": name}, None)
    decision = str(f.get("decision") or "")
    success = f.get("success")
    facts = {
        "tool": name,
        "tier": f.get("tier"),
        "decision": decision or None,
        "duration_ms": f.get("duration_ms"),
        "target": target,
        "error_type": None,
    }
    outcome, severity = "ok", "info"
    if decision == "pending":
        outcome = "pending"
        key, params = (
            (_PENDING, {"tool": name, "target": target})
            if target
            else (
                _PENDING_BARE,
                {"tool": name},
            )
        )
    elif decision == "denied":
        outcome = "denied"
        key, params = (
            (_DENIED, {"tool": name, "target": target})
            if target
            else (
                _DENIED_BARE,
                {"tool": name},
            )
        )
    elif success is False:
        outcome, severity = "failed", "warn"
        error = _text(f.get("error") or "?", 160)
        key, params = (
            (_FAILED, {"tool": name, "target": target, "error": error})
            if target
            else (
                _FAILED_BARE,
                {"tool": name, "error": error},
            )
        )
    return Entry(category, f"{category}.{name}", key, params, outcome, severity, "juniper", facts)


# --- LLM ----------------------------------------------------------------------------------------


def llm_response(f: Fields) -> Entry:
    provider = str(f.get("provider") or "?")
    tokens = _tokens(f.get("usage"))
    params = {
        "provider": provider,
        "seconds": _seconds(f.get("latency_ms")),
        "chars": f.get("chars") or 0,
    }
    facts = {
        "provider": provider,
        "tokens": tokens,
        "duration_ms": f.get("latency_ms"),
        "finish_reason": f.get("finish_reason"),
    }
    calls = f.get("tool_calls") or []
    if f.get("leak_blocked"):
        key = N_("{provider} answered in {seconds} s, and I cut text that was not meant for you")
        return Entry("llm", "llm.call", key, params, "partial", "warn", "juniper", facts)
    if calls:
        key = N_("{provider} answered in {seconds} s and asked to use: {tools}")
        params["tools"] = ", ".join(map(str, calls))
    else:
        key = N_("{provider} answered in {seconds} s with {chars} characters")
    return Entry("llm", "llm.call", key, params, "ok", "info", "juniper", facts)


def failover(f: Fields) -> Entry:
    provider = str(f.get("provider") or "?")
    kind = str(f.get("type") or "?")
    key = N_("{provider} failed ({error}), so I moved to the next provider")
    return Entry(
        "llm",
        "llm.failover",
        key,
        {"provider": provider, "error": kind},
        "failed",
        "warn",
        "juniper",
        {"provider": provider, "error_type": kind},
    )


def web_failover(f: Fields) -> Entry:
    provider = str(f.get("provider") or "?")
    reason = str(f.get("reason") or "?")
    return Entry(
        "web",
        "web.failover",
        N_("The web search with {provider} failed ({reason}), so I used another one"),
        {"provider": provider, "reason": reason},
        "partial",
        "warn",
        "juniper",
        {"provider": provider, "error_type": reason},
    )


def llm_empty(_: Fields) -> Entry:
    return Entry(
        "llm",
        "llm.empty",
        N_("The model returned an empty answer"),
        outcome="failed",
        severity="warn",
    )


# --- conversation and memory --------------------------------------------------------------------


def turn_start(f: Fields) -> Entry:
    return Entry(
        "message",
        "message.received",
        N_("Received your message ({chars} characters)"),
        {"chars": f.get("input_chars") or 0},
        actor="user",
    )


def memory_recall(f: Fields) -> Entry:
    found = int(f.get("found") or 0)
    return Entry(
        "memory",
        "memory.recall",
        N_(
            "Looked in your memory and found {count} relevant memory",
            "Looked in your memory and found {count} relevant memories",
        ),
        {"count": found},
        facts={"duration_ms": (f.get("timings_ms") or {}).get("total")},
    )


def leak_quarantined(_: Fields) -> Entry:
    return Entry(
        "leak",
        "leak.quarantined",
        N_("A reply showed internal text, so I kept that turn out of my memory"),
        outcome="partial",
        severity="warn",
    )


def history_scrubbed(f: Fields) -> Entry:
    return Entry(
        "leak",
        "leak.scrubbed",
        N_(
            "Cleaned {count} message of the conversation that carried internal text",
            "Cleaned {count} messages of the conversation that carried internal text",
        ),
        {"count": int(f.get("messages") or 0)},
        severity="warn",
    )


def error(f: Fields) -> Entry:
    where = str(f.get("where") or "?")
    kind = str(f.get("type") or "?")
    return Entry(
        "error",
        "error.raised",
        N_("Something failed in {where} ({error})"),
        {"where": where, "error": kind},
        "failed",
        "error",
        "system",
        {"error_type": kind, "target": where},
    )


# --- voice, reminders, accounts, clients ---------------------------------------------------------


def voice(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    dropped = (N_("Dropped something you said before it was understood"), "failed", "warn")
    table: dict[str, tuple[str, str, str]] = {
        "start": (N_("Started a voice conversation"), "ok", "info"),
        "end": (N_("Ended the voice conversation ({reason})"), "ok", "info"),
        "stt": (N_("Transcribed what you said"), "ok", "info"),
        "stt_failed": (N_("Could not transcribe what you said"), "failed", "warn"),
        "tts": (N_("Spoke the reply ({chars} characters)"), "ok", "info"),
        "tts_failed": (N_("Could not speak the reply"), "failed", "warn"),
        "utterance_dropped": dropped,
        "asked_repeat": (
            N_("Asked you to repeat: I was not sure of what I heard"),
            "partial",
            "info",
        ),
        "correction": (N_("Corrected a word I had misheard"), "ok", "info"),
        "latency": (N_("Measured the voice latency"), "ok", "info"),
    }
    key, outcome, severity = (
        dropped
        if f.get("dropped")
        else table.get(action, (N_("Voice event: {action}"), "ok", "info"))
    )
    params = {k: f.get(k) for k in ("reason", "chars") if f.get(k) is not None}
    params["action"] = action
    return Entry(
        "voice",
        f"voice.{action or 'event'}",
        key,
        params,
        outcome,
        severity,
        "juniper",
        {"duration_ms": f.get("total_ms")},
    )


_REMINDERS: Final = {
    "telegram_sent": (N_("Sent you a reminder on Telegram"), "ok", "info"),
    "voice_reminded": (N_("Reminded you by voice"), "ok", "info"),
    "telegram_unconfigured": (
        N_("Could not send the reminder: Telegram is not set up"),
        "failed",
        "warn",
    ),
    "job_recovered": (N_("Recovered the reminders that were due while I was off"), "ok", "info"),
    "job_failed": (N_("The reminder job failed ({reason})"), "failed", "error"),
}


def reminder(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    key, outcome, severity = _REMINDERS.get(action, (N_("Reminder event: {action}"), "ok", "info"))
    return Entry(
        "reminder",
        f"reminder.{action}",
        key,
        {"action": action, "reason": f.get("reason") or "?"},
        outcome,
        severity,
        "scheduler",
    )


_AUTH: Final = {
    "login": (N_("A client logged in"), "ok", "info"),
    "failed": (N_("A login failed"), "failed", "warn"),
    "locked": (N_("A client was locked out after too many failed logins"), "denied", "warn"),
    "user_added": (N_("Added a user ({role})"), "ok", "info"),
    "rotated": (N_("Rotated a client's secret"), "ok", "info"),
    "revoked": (N_("Revoked a client"), "ok", "warn"),
    "user_enabled": (N_("Enabled a user"), "ok", "info"),
    "user_disabled": (N_("Disabled a user"), "ok", "warn"),
    "store_down": (N_("The accounts store is unavailable"), "failed", "error"),
}


def auth(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    key, outcome, severity = _AUTH.get(action, (N_("Account event: {action}"), "ok", "info"))
    return Entry(
        "auth",
        f"auth.{action}",
        key,
        {"action": action, "role": f.get("role") or "?"},
        outcome,
        severity,
        "system",
        {"target": f.get("client") or f.get("user_id")},
    )


_TELEGRAM: Final = {
    "start": (N_("The Telegram bot started"), "ok", "info"),
    "stop": (N_("The Telegram bot stopped"), "ok", "info"),
    "login": (N_("Logged in on Telegram"), "ok", "info"),
    "login_refused": (N_("Refused a login on Telegram"), "denied", "warn"),
    "login_expired": (N_("A Telegram login expired"), "failed", "info"),
    "unauthorized": (N_("Ignored a Telegram user that is not allowed"), "denied", "warn"),
    "rate_limited": (N_("Held back a Telegram message: too many in a row"), "denied", "warn"),
    "ignored": (N_("Ignored a Telegram message ({reason})"), "ok", "info"),
    "reattached": (N_("Reattached a Telegram conversation"), "ok", "info"),
    "error": (N_("A Telegram step failed in {where} ({error})"), "failed", "error"),
}


def telegram(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    key, outcome, severity = _TELEGRAM.get(action, (N_("Telegram event: {action}"), "ok", "info"))
    return Entry(
        "telegram",
        f"telegram.{action}",
        key,
        {
            "action": action,
            "reason": f.get("reason") or "?",
            "where": f.get("where") or "?",
            "error": f.get("type") or "?",
        },
        outcome,
        severity,
        "system",
        {"channel": "telegram"},
    )


def dashboard(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    if action == "admin":
        return Entry(
            "dashboard",
            "dashboard.admin",
            N_("An administrator did {op} on the dashboard"),
            {"op": f.get("op") or "?"},
            actor="user",
            facts={"target": f.get("target")},
        )
    if action == "job":
        done = f.get("op") == "end"
        key = N_("A dashboard task ended ({state})") if done else N_("A dashboard task started")
        return Entry(
            "dashboard",
            "dashboard.job",
            key,
            {"state": f.get("state") or "?"},
            "failed" if f.get("state") == "failed" else "ok",
            "info",
            "system",
        )
    table = {
        "login": (N_("Someone logged in to the dashboard"), "ok", "info"),
        "login_failed": (N_("A dashboard login failed"), "failed", "warn"),
        "login_blocked": (N_("A dashboard login was blocked: too many attempts"), "denied", "warn"),
        "start": (N_("The dashboard started"), "ok", "info"),
        "stop": (N_("The dashboard stopped"), "ok", "info"),
    }
    key, outcome, severity = table.get(action, (N_("Dashboard event: {action}"), "ok", "info"))
    return Entry(
        "dashboard",
        f"dashboard.{action}",
        key,
        {"action": action},
        outcome,
        severity,
        "user" if action.startswith("login") else "system",
    )


def connection(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    table = {
        "rejected": (N_("Rejected a connection: not authorized"), "denied", "warn"),
        "refused": (N_("Refused a connection ({reason})"), "denied", "warn"),
        "timeout": (N_("Closed a connection that stopped answering"), "failed", "warn"),
        "close": (N_("A connection closed"), "ok", "info"),
    }
    key, outcome, severity = table.get(action, (N_("Connection event: {action}"), "ok", "info"))
    return Entry(
        "connection",
        f"connection.{action}",
        key,
        {"action": action, "reason": f.get("reason") or "?"},
        outcome,
        severity,
        "system",
    )


def server(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    key = N_("The server started") if action == "start" else N_("The server stopped")
    return Entry("system", f"server.{action}", key, actor="system")


def executor(f: Fields) -> Entry:
    action = str(f.get("action") or "")
    return Entry(
        "system",
        f"executor.{action}",
        N_("Device event: {action}"),
        {"action": action},
        "failed" if "foreign" in action else "ok",
        "info",
        "system",
    )


def generic(event: str, f: Fields) -> Entry:
    """An event nobody wrote a sentence for: still a record, never silently dropped."""
    failed = "fail" in event or "error" in event
    return Entry(
        "system",
        f"event.{event}",
        N_("Recorded the event {event}"),
        {"event": event},
        "failed" if failed else "ok",
        "warn" if failed else "info",
        "system",
    )


_BY_EVENT: Final[dict[str, Callable[[Fields], Entry]]] = {
    "turn_start": turn_start,
    "llm_response": llm_response,
    "llm_empty_reply": llm_empty,
    "failover": failover,
    "web_search_failover": web_failover,
    "tool_call": tool_call,
    "memory_recall": memory_recall,
    "leak_quarantined": leak_quarantined,
    "history_scrubbed": history_scrubbed,
    "error": error,
    "voice": voice,
    "reminder": reminder,
    "auth": auth,
    "telegram": telegram,
    "dashboard": dashboard,
    "connection": connection,
    "server": server,
    "executor": executor,
}


def translate(event: str, fields: Fields) -> Entry | None:
    """The entry for ``event`` (None when it is noise for a person)."""
    if event in SKIPPED:
        return None
    builder = _BY_EVENT.get(event)
    return builder(fields) if builder is not None else generic(event, fields)
