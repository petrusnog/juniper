"""`open_url` (T-139, ADR-014): URL policy, confirmation per domain and routing to the phone."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

import pytest

from brain.clients import protocol as p
from brain.config import OpenUrlSettings, Settings, settings
from brain.core.dispatcher import Dispatcher
from brain.llm.types import ToolCall
from brain.tools.base import Locality, Tier, ToolResult
from brain.tools.open_url import OpenUrl
from brain.tools.registry import ToolRegistry, registry
from tests.test_agent import FakeProvider, say
from tests.test_chat_server import texts
from tests.test_executors import Setup, tool_result_seen_by_llm, use

MUSIC = "https://music.youtube.com/watch?v=abc123"
DEVICE_TOOL = {
    "name": "open_url",
    "description": "Abre um link no celular.",
    "parameters": OpenUrl.parameters,
    "tier": "write",
}


@pytest.fixture(autouse=True)
def trusted_music(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.tools.open_url, "trusted_domains", ["music.youtube.com"])


class FakeGateway:
    def __init__(self, result: ToolResult | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result or ToolResult(success=True, output="Abri no celular.")

    async def invoke(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append((tool_name, arguments))
        return self.result


def make_dispatcher(
    gateway: FakeGateway, asked: list[tuple[str, str | None]] | None = None, answer: bool = True
) -> Dispatcher:
    reg = ToolRegistry()
    reg.register(OpenUrl)

    async def confirm(
        name: str, args: dict[str, Any], tier: Tier, summary: str | None = None
    ) -> bool:
        if asked is not None:
            asked.append((args["url"], summary))
        return answer

    return Dispatcher(confirm if asked is not None else None, tool_registry=reg, gateway=gateway)


# --- the tool's declaration --------------------------------------------------------------


def test_declaration_follows_adr_014() -> None:
    assert OpenUrl.name == "open_url"
    assert OpenUrl.tier is Tier.WRITE
    assert OpenUrl.locality is Locality.EXECUTOR
    assert OpenUrl.parameters["required"] == ["url"]
    assert OpenUrl.parameters["properties"]["url"]["type"] == "string"
    assert OpenUrl.confirm is True  # the per-call hook is what relaxes it


def test_it_is_discovered_by_the_registry() -> None:
    registry.discover()  # importing brain.tools.open_url runs its @register_tool
    assert registry.get("open_url") is OpenUrl
    assert "open_url" in {s["function"]["name"] for s in registry.schemas()}


# --- policy in precheck ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:text/html,x",
        "http://localhost:8766/",
        "http://127.0.0.1/",
        "http://100.64.0.1:8766/",
        "http://192.168.0.10/admin",
        "https://user:pw@example.com/",
        "youtube.com",
        "",
    ],
)
async def test_precheck_refuses_what_the_policy_refuses(url: str) -> None:
    refusal = await OpenUrl().precheck(url=url)
    assert refusal is not None and not refusal.success
    assert refusal.metadata == {"code": "policy_denied"}
    assert refusal.error and refusal.error.startswith("Não posso abrir esse endereço")


async def test_precheck_lets_a_good_url_through() -> None:
    assert await OpenUrl().precheck(url=MUSIC) is None
    assert await OpenUrl().precheck(url="https://exemplo.com/a?b=c") is None


# --- confirmation depends on the domain --------------------------------------------------


async def test_a_trusted_domain_opens_without_asking() -> None:
    assert await OpenUrl().requires_confirmation(url=MUSIC) is False
    assert await OpenUrl().requires_confirmation(url="https://MUSIC.youtube.com./x") is False


async def test_other_domains_ask() -> None:
    assert await OpenUrl().requires_confirmation(url="https://exemplo.com/") is True
    assert await OpenUrl().requires_confirmation(url="https://youtube.com/") is True  # parent
    assert (
        await OpenUrl().requires_confirmation(url="https://music.youtube.com.evil.example/") is True
    )


async def test_trust_follows_the_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.tools.open_url, "trusted_domains", ["youtube.com"])
    assert await OpenUrl().requires_confirmation(url=MUSIC) is False
    assert await OpenUrl().requires_confirmation(url="https://www.youtube.com/") is False
    monkeypatch.setattr(settings.tools.open_url, "trusted_domains", [])
    assert await OpenUrl().requires_confirmation(url=MUSIC) is True


async def test_a_refused_url_never_counts_as_trusted() -> None:
    assert await OpenUrl().requires_confirmation(url="http://127.0.0.1/") is True
    assert await OpenUrl().requires_confirmation(url=None) is True


async def test_the_question_shows_the_exact_url() -> None:
    assert await OpenUrl().describe(url="https://Exemplo.com./a b") is None  # refused: no summary
    assert await OpenUrl().describe(url="HTTPS://Exemplo.com./a") == "abrir https://exemplo.com/a"


# --- through the dispatcher (the server path) --------------------------------------------


async def test_a_trusted_link_goes_straight_to_the_device() -> None:
    gateway = FakeGateway()
    asked: list[tuple[str, str | None]] = []
    message = await make_dispatcher(gateway, asked).dispatch(
        ToolCall("c1", "open_url", {"url": MUSIC})
    )
    assert asked == []
    assert gateway.calls == [("open_url", {"url": MUSIC})]
    assert message.content == "Abri no celular."


async def test_another_link_asks_with_the_exact_url_and_runs_on_yes() -> None:
    gateway = FakeGateway()
    asked: list[tuple[str, str | None]] = []
    url = "https://exemplo.com/pagina"
    await make_dispatcher(gateway, asked).dispatch(ToolCall("c1", "open_url", {"url": url}))
    assert asked == [(url, f"abrir {url}")]
    assert gateway.calls == [("open_url", {"url": url})]


async def test_declining_keeps_the_phone_untouched() -> None:
    gateway = FakeGateway()
    asked: list[tuple[str, str | None]] = []
    message = await make_dispatcher(gateway, asked, answer=False).dispatch(
        ToolCall("c1", "open_url", {"url": "https://exemplo.com/"})
    )
    assert gateway.calls == [] and "negou" in (message.content or "")


async def test_unattended_non_trusted_links_are_denied() -> None:
    gateway = FakeGateway()
    message = await make_dispatcher(gateway).dispatch(
        ToolCall("c1", "open_url", {"url": "https://exemplo.com/"})
    )
    assert gateway.calls == [] and "negou" in (message.content or "")


async def test_a_refused_url_is_neither_asked_nor_sent() -> None:
    gateway = FakeGateway()
    asked: list[tuple[str, str | None]] = []
    for url in ("http://localhost/", "javascript:alert(1)", "https://u:p@music.youtube.com/"):
        message = await make_dispatcher(gateway, asked).dispatch(
            ToolCall("c1", "open_url", {"url": url})
        )
        assert (message.content or "").startswith("ERRO: Não posso abrir esse endereço")
    assert asked == [] and gateway.calls == []


async def test_a_trusted_domain_does_not_lift_the_policy() -> None:
    """A name that ends in the trusted domain, or carries it as credentials, is not trusted."""
    gateway = FakeGateway()
    asked: list[tuple[str, str | None]] = []
    for url in (
        "https://music.youtube.com@evil.example/",
        "https://evil.example\\.music.youtube.com/",
        "https://127.0.0.1\\.music.youtube.com/",
        "https://%31%32%37.0.0.1.music.youtube.com/",
    ):
        message = await make_dispatcher(gateway, asked).dispatch(
            ToolCall("c1", "open_url", {"url": url})
        )
        assert (message.content or "").startswith("ERRO: Não posso abrir esse endereço"), url
    assert gateway.calls == [] and asked == []


async def test_the_device_receives_the_url_the_user_was_shown() -> None:
    """What was checked, asked about and sent is one and the same normalised string."""
    gateway = FakeGateway()
    asked: list[tuple[str, str | None]] = []
    raw = "  HTTPS://Exemplo.COM./a b".replace(" b", "")  # padded, upper-case host, trailing dot
    await make_dispatcher(gateway, asked).dispatch(ToolCall("c1", "open_url", {"url": raw}))
    assert asked == [("https://exemplo.com/a", "abrir https://exemplo.com/a")]
    assert gateway.calls == [("open_url", {"url": "https://exemplo.com/a"})]


async def test_a_trusted_link_is_sent_normalised_too() -> None:
    gateway = FakeGateway()
    await make_dispatcher(gateway).dispatch(
        ToolCall("c1", "open_url", {"url": "HTTPS://Music.YouTube.com./watch?v=abc"})
    )
    assert gateway.calls == [("open_url", {"url": "https://music.youtube.com/watch?v=abc"})]


async def test_open_url_names_no_subjects_so_a_yes_in_context_never_covers_it() -> None:
    assert await OpenUrl().subjects(url=MUSIC) is None


async def test_a_device_failure_comes_back_as_a_typed_result() -> None:
    gateway = FakeGateway(
        ToolResult(
            success=False, error="O dispositivo não respondeu.", metadata={"code": "timeout"}
        )
    )
    message = await make_dispatcher(gateway).dispatch(ToolCall("c1", "open_url", {"url": MUSIC}))
    assert message.content == "ERRO: O dispositivo não respondeu."


# --- local mode: the brain is the user's machine -----------------------------------------


async def test_run_opens_the_default_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    result = await OpenUrl().run(url="HTTPS://Exemplo.com./a")
    assert result.success and opened == ["https://exemplo.com/a"]
    assert result.output == "Abri https://exemplo.com/a no navegador."
    assert result.metadata == {"url": "https://exemplo.com/a"}


async def test_run_reports_a_machine_without_a_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webbrowser, "open", lambda url: False)
    result = await OpenUrl().run(url=MUSIC)
    assert not result.success
    assert result.metadata == {"code": "os_error", "url": MUSIC}


async def test_run_revalidates_and_never_opens_a_refused_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
    result = await OpenUrl().run(url="file:///etc/passwd")
    assert not result.success and result.metadata == {"code": "policy_denied"}
    assert opened == []


# --- configuration -----------------------------------------------------------------------


def test_default_trusted_domain_is_only_youtube_music() -> None:
    assert OpenUrlSettings().trusted_domains == ["music.youtube.com"]


def test_trusted_domains_are_normalised_and_deduplicated() -> None:
    cfg = OpenUrlSettings(
        trusted_domains=[" Music.YouTube.com. ", "music.youtube.com", "Bücher.example"]
    )
    assert cfg.trusted_domains == ["music.youtube.com", "xn--bcher-kva.example"]


@pytest.mark.parametrize(
    "entry", ["https://music.youtube.com/", "music.youtube.com:443", "10.0.0.1", ""]
)
def test_trusted_domains_reject_what_is_not_a_domain(entry: str) -> None:
    with pytest.raises(ValueError, match="trusted_domains"):
        OpenUrlSettings(trusted_domains=[entry])


def test_trusted_domains_load_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JUNIPER_TOOLS__OPEN_URL__TRUSTED_DOMAINS", '["youtube.com"]')
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.tools.open_url.trusted_domains == ["youtube.com"]


# --- end to end: server, phone as executor -----------------------------------------------


async def test_the_phone_gets_the_normalised_url(tmp_path: Path) -> None:
    provider = FakeProvider(
        [use("open_url", url="HTTPS://Music.YouTube.com./watch?v=abc123 ".strip()), say("Abri.")]
    )
    async with Setup(tmp_path, provider) as s:
        s.registry.register(OpenUrl)
        phone, _ = await s.executor("celular", DEVICE_TOOL)
        await phone.send(type="chat.send", id="c-1", text="toca")
        invoke = (await phone.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke) and invoke.arguments == {"url": MUSIC}
        await phone.send(type="result", call_id=invoke.call_id, success=True, output="Abri.")
        await phone.until(p.ChatEnd)


async def test_trusted_link_reaches_the_phone_without_a_question(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_url", url=MUSIC), say("Abri para você.")])
    async with Setup(tmp_path, provider) as s:
        s.registry.register(OpenUrl)
        phone, registered = await s.executor("celular", DEVICE_TOOL)
        assert [t.name for t in registered.tools] == ["open_url"]
        assert s.handler._registry.get("open_url") is OpenUrl  # the server's policy wins

        await phone.send(type="chat.send", id="c-1", text="toca Djavan")
        invoke = (await phone.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke)
        assert invoke.tool_name == "open_url" and invoke.arguments == {"url": MUSIC}
        assert invoke.tier == "write"
        await phone.send(type="result", call_id=invoke.call_id, success=True, output="Abri.")
        assert texts(await phone.until(p.ChatEnd)) == "Abri para você."
    assert tool_result_seen_by_llm(provider) == "Abri."


async def test_other_link_is_asked_in_words_and_runs_on_yes(tmp_path: Path) -> None:
    url = "https://exemplo.com/pagina"
    provider = FakeProvider(
        [use("open_url", url=url), say(f"Posso abrir {url}?"), say("Pronto, abri.")]
    )
    async with Setup(tmp_path, provider) as s:
        s.registry.register(OpenUrl)
        phone, _ = await s.executor("celular", DEVICE_TOOL)

        first = await phone.chat("abre essa página")
        assert not any(isinstance(m, p.Invoke) for m in first)  # nothing reached the phone
        assert url in texts(first)

        await phone.send(type="chat.send", id="c-2", text="sim")
        invoke = (await phone.until(p.Invoke))[-1]
        assert isinstance(invoke, p.Invoke) and invoke.arguments == {"url": url}
        await phone.send(type="result", call_id=invoke.call_id, success=True, output="Abri.")
        assert texts(await phone.until(p.ChatEnd)) == "Pronto, abri."


async def test_a_refused_link_never_reaches_the_phone(tmp_path: Path) -> None:
    provider = FakeProvider([use("open_url", url="http://100.64.0.1:8766/"), say("Não abro isso.")])
    async with Setup(tmp_path, provider) as s:
        s.registry.register(OpenUrl)
        phone, _ = await s.executor("celular", DEVICE_TOOL)
        messages = await phone.chat("abre o painel")
        assert not any(isinstance(m, p.Invoke) for m in messages)
        assert texts(messages) == "Não abro isso."
    assert "Não posso abrir esse endereço" in tool_result_seen_by_llm(provider)


async def test_without_a_phone_connected_the_juniper_says_so(tmp_path: Path) -> None:
    from tests.test_chat_server import connect_client

    provider = FakeProvider([use("open_url", url=MUSIC), say("O celular está desconectado.")])
    async with Setup(tmp_path, provider) as s:
        s.registry.register(OpenUrl)
        client = await connect_client(s.server, "cli")  # a chat client, not an executor
        messages = await client.chat("toca algo")
        assert texts(messages) == "O celular está desconectado."
    assert "não está conectado" in tool_result_seen_by_llm(provider)


# --- search pages are not music (found in the first real run on the VPS) ----------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://music.youtube.com/search?q=Sheena+is+a+punk+rocker",
        "https://music.youtube.com/search/",
        "https://www.youtube.com/results?search_query=ramones",
    ],
)
async def test_a_search_page_is_sent_back_to_music_search_when_music_is_on(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.tools import music as music_tools

    monkeypatch.setattr(music_tools, "enabled", lambda: True)
    result = await OpenUrl().precheck(url=url)
    assert result is not None and not result.success
    assert result.metadata == {"code": "use_music_search"}
    assert "music_search" in (result.error or "")


@pytest.mark.parametrize(
    "url",
    [
        "https://music.youtube.com/watch?v=abc123",
        "https://music.youtube.com/playlist?list=PLabcdefghijklmnopqrstuvwxyz012345",
        "https://example.com/search?q=x",
        "https://music.youtube.com/searching",
    ],
)
async def test_tracks_playlists_and_other_sites_still_open(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from brain.tools import music as music_tools

    monkeypatch.setattr(music_tools, "enabled", lambda: True)
    assert await OpenUrl().precheck(url=url) is None


async def test_without_the_music_tools_a_search_page_opens_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from brain.tools import music as music_tools

    monkeypatch.setattr(music_tools, "enabled", lambda: False)
    assert await OpenUrl().precheck(url="https://music.youtube.com/search?q=x") is None


async def test_the_model_that_builds_a_search_link_is_redirected_to_music_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real log: "procura X no YouTube Music" became open_url on a results page. The phone
    must not be touched, and the model gets the way to the right tool."""
    from brain.tools import music as music_tools

    monkeypatch.setattr(music_tools, "enabled", lambda: True)
    provider = FakeProvider(
        [
            use(
                "open_url", "k1", url="https://music.youtube.com/search?q=Sheena+is+a+punck+rocker"
            ),
            say("Vou procurar direito."),
        ]
    )
    async with Setup(tmp_path, provider) as s:
        s.registry.register(OpenUrl)
        phone, _ = await s.executor("celular", DEVICE_TOOL)
        messages = await phone.chat("procura Sheena is a punk rocker dos Ramones no YouTube Music")
        assert not any(isinstance(m, p.Invoke) for m in messages)
        assert texts(messages) == "Vou procurar direito."
    assert "music_search" in tool_result_seen_by_llm(provider)
