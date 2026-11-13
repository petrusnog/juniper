"""Observabilidade V2: registro de atividade, textos amigáveis e i18n (T-219, T-220, T-221)."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import mongomock
import pytest

from brain import i18n
from brain.activity import catalog, model
from brain.activity.recorder import Recorder, recorder
from brain.eventlog import EventLog, event_log
from brain.memory.activity import ActivityStore, Filters
from brain.memory.mongo import MongoConnection

ROOT = Path(__file__).resolve().parents[1]
SECRET = "gsk_abcdefghijklmnopqrstuvwxyz"


def connection() -> MongoConnection:
    conn = MongoConnection()
    conn._client = mongomock.MongoClient(tz_aware=True)  # type: ignore[assignment]
    return conn


@pytest.fixture
def store() -> ActivityStore:
    return ActivityStore(connection(), retention_days=90)


@pytest.fixture
def live(tmp_path: Path, store: ActivityStore) -> Iterator[ActivityStore]:
    """The global event log writing into ``tmp_path`` and the recorder into ``store``."""
    old = event_log._directory
    event_log.directory = tmp_path
    recorder.start(store.add, threaded=False)
    try:
        yield store
    finally:
        recorder.stop()
        event_log.directory = old


def entry(event: str, **fields: Any) -> catalog.Entry:
    built = catalog.translate(event, fields)
    assert built is not None
    return built


def sentence(event: str, locale: str = "pt_BR", **fields: Any) -> str:
    built = entry(event, **fields)
    return i18n.render(built.key, model.clean_params(built.params), locale)


# --- i18n -------------------------------------------------------------------------------------


def test_render_translates_with_the_catalog_and_falls_back_to_english() -> None:
    key = "Searched {query} on YouTube Music"
    assert (
        i18n.render(key, {"query": "Evidências"}, "pt_BR")
        == "Pesquisei «Evidências» no YouTube Music"
    )
    assert i18n.render(key, {"query": "x"}, "en") == "Searched x on YouTube Music"
    assert i18n.render(key, {"query": "x"}, "fr_FR") == "Searched x on YouTube Music"
    assert (
        i18n.render("texto novo sem catálogo {a}", {"a": 1}, "pt_BR") == "texto novo sem catálogo 1"
    )


def test_render_leaves_a_missing_parameter_visible_instead_of_failing() -> None:
    assert i18n.render("Searched {query} on YouTube Music", {}, "en") == (
        "Searched {query} on YouTube Music"
    )


def test_plural_follows_the_language_rule() -> None:
    key = "Looked in your memory and found {count} relevant memory"
    assert i18n.render(key, {"count": 1}, "pt_BR").endswith("1 lembrança relevante")
    assert i18n.render(key, {"count": 0}, "pt_BR").endswith("0 lembrança relevante")  # n > 1
    assert i18n.render(key, {"count": 5}, "pt_BR").endswith("5 lembranças relevantes")
    assert i18n.render(key, {"count": 1}, "en").endswith("1 relevant memory")
    assert i18n.render(key, {"count": 2}, "en").endswith("2 relevant memories")


def test_locale_names_are_normalised() -> None:
    assert i18n.normalize_locale("pt-br") == "pt_BR"
    assert i18n.normalize_locale("PT_BR.UTF-8") == "pt_BR"
    assert i18n.normalize_locale("en") == "en"
    assert i18n.normalize_locale("") == i18n.DEFAULT_LOCALE
    assert i18n.available()[0] == "en" and "pt_BR" in i18n.available()


def test_po_parser_reads_multiline_strings_plurals_and_skips_fuzzy(tmp_path: Path) -> None:
    po = (
        'msgid ""\nmsgstr ""\n"Plural-Forms: nplurals=2; plural=(n > 1);\\n"\n\n'
        'msgid "Hello"\nmsgstr "Ol"\n"á"\n\n'
        '#, fuzzy\nmsgid "Maybe"\nmsgstr "Talvez"\n\n'
        'msgid "One {count}"\nmsgid_plural "Many {count}"\nmsgstr[0] "Um {count}"\n'
        'msgstr[1] "Vários {count}"\n'
    )
    directory = tmp_path / "xx" / "LC_MESSAGES"
    directory.mkdir(parents=True)
    (directory / "juniper.po").write_text(po, encoding="utf-8")
    catalog_ = i18n.load_catalog("xx", tmp_path)
    assert catalog_ is not None
    assert catalog_.lookup("Hello", None) == "Olá"
    assert catalog_.lookup("Maybe", None) is None  # fuzzy is not trusted
    assert catalog_.lookup("One {count}", 1) == "Um {count}"
    assert catalog_.lookup("One {count}", 0) == "Um {count}"
    assert catalog_.lookup("One {count}", 2) == "Vários {count}"
    assert i18n.load_catalog("zz", tmp_path) is None


def test_catalogs_are_in_sync_with_the_code() -> None:
    spec = importlib.util.spec_from_file_location("i18n_script", ROOT / "scripts" / "i18n.py")
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.check() == 0  # .pot up to date, every text translated, parameters kept


# --- the catalog of sentences -----------------------------------------------------------------


def test_the_examples_of_the_brief_read_as_asked() -> None:
    assert sentence(
        "tool_call", name="music_search", args={"query": "Evidências"}, success=True
    ) == ("Pesquisei «Evidências» no YouTube Music")
    assert (
        sentence(
            "tool_call", name="open_url", args={"url": "https://exemplo.com/xpto"}, success=True
        )
        == "Abri o link https://exemplo.com/xpto"
    )
    assert (
        sentence("tool_call", locale="en", name="web_search", args={"query": "dólar"}, success=True)
        == "Searched the web for dólar"
    )


def test_a_tool_that_did_not_simply_work_says_so() -> None:
    pending = entry(
        "tool_call",
        name="delete_event",
        args={"event_id": "Dentista"},
        decision="pending",
        success=False,
    )
    assert pending.outcome == "pending"
    assert "pendente" in sentence(
        "tool_call", name="delete_event", args={"event_id": "Dentista"}, decision="pending"
    )
    denied = entry(
        "tool_call",
        name="delete_event",
        args={"event_id": "Dentista"},
        decision="denied",
        success=False,
    )
    assert denied.outcome == "denied"
    failed = entry(
        "tool_call", name="web_fetch", args={"url": "https://x.dev"}, success=False, error="timeout"
    )
    assert (failed.outcome, failed.severity) == ("failed", "warn")
    assert "timeout" in sentence(
        "tool_call", name="web_fetch", args={"url": "https://x.dev"}, success=False, error="timeout"
    )


def test_unknown_tools_and_events_are_never_lost() -> None:
    assert sentence("tool_call", name="ferramenta_nova", args={}, success=True) == (
        "Usei a ferramenta ferramenta_nova"
    )
    built = entry("evento_inventado", x=1)
    assert (built.category, built.action) == ("system", "event.evento_inventado")


def test_noise_events_are_skipped() -> None:
    for event in ("timing", "embedding", "http_request", "llm_request"):
        assert catalog.translate(event, {}) is None


def test_llm_and_failover_sentences_carry_provider_tokens_and_time() -> None:
    built = entry(
        "llm_response",
        provider="groq",
        latency_ms=1300,
        chars=120,
        usage={"total_tokens": 1842},
        tool_calls=[],
        finish_reason="stop",
    )
    assert built.facts["tokens"] == 1842 and built.facts["duration_ms"] == 1300
    assert sentence("llm_response", provider="groq", latency_ms=1300, chars=120) == (
        "groq respondeu em 1.3 s com 120 caracteres"
    )
    fail = entry("failover", provider="groq", type="LLMTimeoutError", message="x")
    assert (fail.outcome, fail.action) == ("failed", "llm.failover")


def test_every_event_the_code_logs_has_a_sentence_or_a_generic_one() -> None:
    for event in (
        "voice",
        "reminder",
        "auth",
        "telegram",
        "dashboard",
        "connection",
        "server",
        "executor",
        "error",
        "memory_recall",
        "turn_start",
        "leak_quarantined",
    ):
        built = catalog.translate(event, {"action": "login", "where": "x"})
        assert built is not None and i18n.render(built.key, built.params, "pt_BR")


# --- the record --------------------------------------------------------------------------------


def test_every_record_has_the_local_time_with_the_offset() -> None:
    moment = datetime(2026, 11, 13, 17, 32, 8, 412000, tzinfo=UTC)
    record = model.build(
        entry("turn_start", input_chars=3), {"event": "turn_start"}, event="turn_start", now=moment
    )
    assert record["ts"] == moment
    assert record["ts_local"] == "2026-11-13T14:32:08.412-03:00"
    assert record["locale"] == "pt_BR" and record["summary"].startswith("Recebi sua mensagem")


def test_secrets_never_reach_the_sentence_or_the_haystack() -> None:
    built = entry("tool_call", name="web_search", args={"query": f"chave {SECRET}"}, success=True)
    record = model.build(built, {"args": {"query": "***"}}, event="tool_call")
    assert SECRET not in record["summary"]
    assert SECRET not in record["haystack"]


def test_fold_ignores_case_and_accents() -> None:
    assert model.fold("Evidências Chitãozinho") == "evidencias chitaozinho"


def test_public_view_renders_in_another_language_and_hides_the_haystack() -> None:
    record = model.build(
        entry("tool_call", name="open_url", args={"url": "https://a.b"}, success=True),
        {},
        event="tool_call",
    )
    shown = model.public(record, "en")
    assert shown["summary"] == "Opened the link https://a.b"
    assert "haystack" not in shown and "_id" not in shown and shown["id"] == record["_id"]


# --- recorder: the legacy log stays the same -----------------------------------------------------


def test_recording_does_not_change_the_legacy_jsonl(tmp_path: Path, store: ActivityStore) -> None:
    plain = EventLog(tmp_path / "plain")
    plain.run_id = "run00001"
    plain.log("tool_call", name="open_url", args={"url": "https://a.b"}, success=True)
    legacy = (tmp_path / "plain").glob("*.jsonl")
    before = [json.loads(line) for f in legacy for line in f.read_text().splitlines()][0]

    old = event_log._directory
    event_log.directory = tmp_path / "live"
    event_log.run_id = "run00001"
    recorder.start(store.add, threaded=False)
    try:
        event_log.log("tool_call", name="open_url", args={"url": "https://a.b"}, success=True)
    finally:
        recorder.stop()
        event_log.directory = old
    after = [
        json.loads(line)
        for f in (tmp_path / "live").glob("*.jsonl")
        for line in f.read_text().splitlines()
    ][0]
    assert set(after) == set(before)  # not one field added to the technical line


def test_an_event_becomes_a_record_with_the_raw_line_inside(live: ActivityStore) -> None:
    event_log.log(
        "tool_call",
        name="music_search",
        args={"query": "Evidências"},
        success=True,
        tier="read",
        duration_ms=640,
        decision="auto",
    )
    rows, total = live.search(Filters())
    assert total == 1
    record = rows[0]
    assert record["summary"] == "Pesquisei «Evidências» no YouTube Music"
    assert (record["category"], record["action"], record["tool"]) == (
        "music",
        "music.music_search",
        "music_search",
    )
    assert record["duration_ms"] == 640 and record["raw"]["name"] == "music_search"
    assert record["ts_local"].endswith("-03:00")


def test_steps_of_one_message_share_a_turn(live: ActivityStore) -> None:
    event_log.log("turn_start", input_chars=10)
    event_log.log("tool_call", name="open_url", args={"url": "https://a.b"}, success=True)
    event_log.log("llm_response", provider="groq", latency_ms=900, chars=5)
    rows, _ = live.search(Filters(), oldest_first=True)
    turns = {r["turn"] for r in rows}
    assert len(turns) == 1 and next(iter(turns)).startswith("t-")
    assert [r["action"] for r in live.turn(next(iter(turns)))] == [
        "message.received",
        "web.open_url",
        "llm.call",
    ]


def test_a_failing_sink_never_breaks_the_log(tmp_path: Path) -> None:
    def broken(_: dict[str, Any]) -> None:
        raise RuntimeError("mongo caiu")

    local = Recorder()
    local.start(broken, threaded=False)
    local.on_event("turn_start", {"input_chars": 1, "run": "r"})
    assert local.failed == 1  # counted, never raised


def test_threaded_writer_drains_and_a_full_queue_drops_instead_of_blocking(
    store: ActivityStore,
) -> None:
    local = Recorder()
    local.start(store.add)
    for index in range(20):
        local.on_event("turn_start", {"input_chars": index, "run": "r"})
    local.flush()
    assert store.search(Filters())[1] == 20
    local.stop()


# --- the store ---------------------------------------------------------------------------------


def seed(store: ActivityStore) -> datetime:
    base = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)  # 14:00 in UTC-3
    specs = [
        ("music_search", {"query": "Evidências Chitãozinho"}, True, "auto", "groq", "llama-70b", 0),
        ("open_url", {"url": "https://exemplo.com"}, True, "auto", "groq", "llama-70b", 10),
        ("web_fetch", {"url": "https://x.dev"}, False, "auto", "deepinfra", "qwen3", 20),
        ("delete_event", {"event_id": "Dentista"}, False, "pending", "groq", "llama-70b", 30),
    ]
    for name, args, ok, decision, provider, served, minutes in specs:
        built = catalog.translate(
            "tool_call",
            {
                "name": name,
                "args": args,
                "success": ok,
                "decision": decision,
                "error": None if ok else "falhou",
            },
        )
        assert built is not None
        built.facts.update(provider=provider, model=served)
        store.add(
            model.build(
                built,
                {"name": name},
                event="tool_call",
                now=base + timedelta(minutes=minutes),
                session="s1",
                channel="app",
            )
        )
    return base


def test_search_by_term_ignores_case_and_accents_and_needs_every_word(store: ActivityStore) -> None:
    seed(store)
    assert store.search(Filters(q="evidencias"))[1] == 1
    assert store.search(Filters(q="PESQUISEI chitaozinho"))[1] == 1
    assert store.search(Filters(q="evidencias dentista"))[1] == 0
    assert store.search(Filters(q="exemplo.com"))[1] == 1
    assert store.search(Filters(q="(.*"))[1] == 0  # regex characters are literal


def test_search_by_model_action_outcome_channel_and_session(store: ActivityStore) -> None:
    seed(store)
    assert store.search(Filters(model=["qwen3"]))[1] == 1
    assert store.search(Filters(provider=["groq"]))[1] == 3
    assert store.search(Filters(outcome=["failed", "pending"]))[1] == 2
    assert store.search(Filters(action=["web.open_url"]))[1] == 1
    assert store.search(Filters(category=["music"]))[1] == 1
    assert store.search(Filters(channel=["app"]))[1] == 4
    assert store.search(Filters(session="nenhuma"))[1] == 0
    assert store.search(Filters(tool="open_url"))[1] == 1


def test_search_by_date_window_is_half_open(store: ActivityStore) -> None:
    base = seed(store)
    window = Filters(since=base + timedelta(minutes=10), until=base + timedelta(minutes=30))
    rows, total = store.search(window)
    assert total == 2  # 10 and 20 minutes; the 30 is excluded
    assert [r["tool"] for r in rows] == ["web_fetch", "open_url"]  # newest first


def test_pages_and_order(store: ActivityStore) -> None:
    seed(store)
    first, total = store.search(Filters(), limit=3)
    rest, _ = store.search(Filters(), limit=3, offset=3)
    assert total == 4 and len(first) == 3 and len(rest) == 1
    assert first[0]["tool"] == "delete_event"


def test_facets_count_by_field_inside_the_filter(store: ActivityStore) -> None:
    seed(store)
    facets = store.facets(Filters(provider=["groq"]))
    assert dict(facets["outcome"]) == {"ok": 2, "pending": 1}
    assert dict(facets["model"]) == {"llama-70b": 3}


def test_summary_counts_outcomes_and_buckets_by_local_day_or_hour(store: ActivityStore) -> None:
    seed(store)
    summary = store.summary(Filters())
    assert (summary["total"], summary["failed"], summary["pending"]) == (4, 1, 1)
    assert summary["by"] == "hour"
    assert summary["timeline"][0]["at"] == "2026-11-13T14:00"  # UTC-3, not UTC


def test_ttl_index_follows_the_retention(store: ActivityStore) -> None:
    store.add({"_id": "a" * 16, "ts": datetime.now(UTC)})
    info = store.connection.collection("activity").index_information()
    ttl = [v for v in info.values() if v.get("expireAfterSeconds") is not None]
    assert ttl and ttl[0]["expireAfterSeconds"] == 90 * 86400


def test_get_by_prefix(store: ActivityStore) -> None:
    seed(store)
    rows, _ = store.search(Filters())
    wanted = rows[0]["_id"]
    assert store.get(wanted)["_id"] == wanted
    assert store.get(wanted[:8])["_id"] == wanted
    assert store.get("zz") is None


def test_timeline_has_no_gaps_so_the_chart_axis_is_honest(store: ActivityStore) -> None:
    base = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)  # 11:00 in UTC-3
    for hours in (0, 3):
        store.add(
            model.build(
                entry("turn_start", input_chars=1),
                {},
                event="turn_start",
                now=base + timedelta(hours=hours),
            )
        )
    timeline = store.summary(Filters())["timeline"]
    assert [b["at"] for b in timeline] == [
        "2026-11-13T11:00",
        "2026-11-13T12:00",
        "2026-11-13T13:00",
        "2026-11-13T14:00",
    ]
    assert [b["count"] for b in timeline] == [1, 0, 0, 1]


# --- CLI ---------------------------------------------------------------------------------------


def test_cli_lists_by_term_with_utc_minus_3_and_shows_the_technical_log(
    store: ActivityStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rich.console import Console
    from typer.testing import CliRunner

    from brain.clients import activity_cli

    seed(store)
    import typer

    app = typer.Typer()
    console = Console(width=200, force_terminal=False)
    activity_cli.install(app, console)
    monkeypatch.setattr(activity_cli, "_open_store", lambda _console: store)
    store.connection.close = lambda: None  # type: ignore[method-assign]
    runner = CliRunner()
    result = runner.invoke(app, ["--periodo", "", "--buscar", "evidencias", "--de", "2026-11-13"])
    assert result.exit_code == 0, result.output
    assert "Pesquisei" in result.output and "UTC-3" in result.output
    record_id = store.search(Filters(q="evidencias"))[0][0]["_id"]
    shown = runner.invoke(app, ["--ver", record_id[:8]])
    assert shown.exit_code == 0 and "Log técnico" in shown.output
    bad = runner.invoke(app, ["--de", "ontem"])
    assert bad.exit_code == 1 and "data inválida" in bad.output


# --- security review: links, secrets, search term, reserved fields -------------------------------


def test_a_link_is_kept_without_credentials_query_or_fragment() -> None:
    for url in (
        "https://bob:s3cr3t@host.com/x",
        "https://host.com/x?code=abc123#access_token=zzz99",
    ):
        built = entry("tool_call", name="open_url", args={"url": url}, success=True)
        record = model.build(built, {"args": {"url": url}}, event="tool_call")
        assert "s3cr3t" not in record["summary"] and "abc123" not in record["summary"]
        assert "access_token" not in record["summary"]
        assert "s3cr3t" not in str(record["msg_params"]) and "s3cr3t" not in record["target"]
    assert sentence(
        "tool_call", name="open_url", args={"url": "https://a.com/p?q=1"}, success=True
    ) == ("Abri o link https://a.com/p")


def test_secrets_in_the_technical_event_are_not_searchable(store: ActivityStore) -> None:
    built = entry("tool_call", name="fs_read", args={"path": "/x"}, success=True)
    record = model.build(
        built, {"output": "senha: abc123XYZ e cartão 4111 1111 1111 1111"}, event="tool_call"
    )
    store.add(record)
    assert store.search(Filters(q="abc123xyz"))[1] == 0
    assert store.search(Filters(q="4111"))[1] == 0


def test_a_fact_can_never_overwrite_a_base_field() -> None:
    built = entry("turn_start", input_chars=1)
    built.facts.update(outcome="hacked", summary="falso", ts="x", model="llama")
    record = model.build(built, {}, event="turn_start")
    assert record["outcome"] == "ok" and record["summary"] != "falso" and record["model"] == "llama"


def test_search_term_is_bounded_and_control_characters_are_dropped(store: ActivityStore) -> None:
    seed(store)
    assert store.search(Filters(q="evidencias\x00"))[1] == 1  # no 500 from a NUL byte
    many = Filters(q=" ".join(["a"] * 50)).query()
    assert len(many["$and"]) == 5


def test_lang_cannot_point_outside_the_catalogs() -> None:
    from brain.dashboard import activity as panel

    assert panel.language({"lang": ["../../etc"]}) == "pt_BR"
    assert panel.language({"lang": ["en"]}) == "en"
    assert panel.language({"lang": ["fr"]}) == "fr"  # well-formed: English comes out


def test_the_cli_masks_secrets_like_the_dashboard() -> None:
    from brain.clients.activity_cli import _safe

    assert "abc123" not in _safe("senha: abc123")


# --- architecture review: turn scope, stable pages, recorder health ------------------------------


def test_an_event_after_the_turn_belongs_to_no_message(live: ActivityStore) -> None:
    event_log.log("turn_start", input_chars=3)
    event_log.log("timing", total_ms=10)  # the last event of a turn
    event_log.log("server", action="start")
    rows, _ = live.search(Filters(), oldest_first=True)
    assert rows[0]["turn"].startswith("t-") and rows[-1]["turn"] is None


def test_pages_are_stable_when_records_share_a_millisecond(store: ActivityStore) -> None:
    moment = datetime(2026, 11, 13, 17, 0, tzinfo=UTC)
    for _ in range(7):
        store.add(
            model.build(entry("turn_start", input_chars=1), {}, event="turn_start", now=moment)
        )
    seen: list[str] = []
    for offset in range(0, 7, 3):
        seen += [r["_id"] for r in store.search(Filters(), limit=3, offset=offset)[0]]
    assert len(seen) == 7 and len(set(seen)) == 7  # none twice, none lost


def test_a_deep_offset_is_capped() -> None:
    from brain.memory.activity import MAX_OFFSET

    assert MAX_OFFSET <= 100_000


def test_a_full_queue_drops_and_warns_once_and_stop_never_blocks(
    store: ActivityStore, caplog: pytest.LogCaptureFixture
) -> None:
    import threading

    gate = threading.Event()

    def slow(record: dict[str, Any]) -> None:
        gate.wait(5)

    local = Recorder()
    local.start(slow)
    with caplog.at_level("WARNING"):
        for index in range(5100):
            local.on_event("turn_start", {"input_chars": index, "run": "r"})
        assert local.dropped >= 90
        assert sum("fila encheu" in r.message for r in caplog.records) == 1
    local.stop(timeout=0.2)  # the queue is full and the writer stuck: stop returns anyway
    gate.set()


def test_a_failing_sink_is_counted_and_warned_once(caplog: pytest.LogCaptureFixture) -> None:
    def broken(_: dict[str, Any]) -> None:
        raise RuntimeError("mongo caiu")

    local = Recorder()
    local.start(broken, threaded=False)
    with caplog.at_level("WARNING"):
        for _ in range(3):
            local.on_event("turn_start", {"input_chars": 1, "run": "r"})
    assert (
        local.failed == 3
        and len([r for r in caplog.records if "registro de atividade" in r.message]) == 1
    )


def test_count_given_as_text_still_picks_the_plural() -> None:
    key = "Added {count} track to a playlist"
    assert i18n.render(key, {"count": "2"}, "pt_BR") == "Adicionei 2 faixas a uma playlist"
    assert i18n.render(key, {"count": "abc"}, "pt_BR").startswith("Adicionei abc faixa")
