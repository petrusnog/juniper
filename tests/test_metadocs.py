"""The Juniper consulting her own documentation (ADR-025): index, detector, context and tool."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from brain.config import settings
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.memory.secretfilter import _TOKEN_PATTERNS as TOKEN_PATTERNS
from brain.memory.secretfilter import find_secret
from brain.metadocs import context as ctx
from brain.metadocs import index as idx
from brain.metadocs.detect import about_self
from brain.tools.base import Locality, Tier
from brain.tools.docs import DocsSearch
from brain.tools.registry import ToolRegistry
from tests.test_agent import FakeProvider, say

# --- tokenização -----------------------------------------------------------------------------


def test_fold_stem_and_tokens() -> None:
    assert idx.fold("Confirmação É") == "confirmacao e"
    assert idx.stem("confirmacoes") == idx.stem("confirmacao")
    assert idx.stem("memorias") == idx.stem("memoria")
    assert idx.stem("8765") == "8765"
    assert idx.tokens("Como você funciona?") == []  # só palavras vazias


def test_query_terms_expand_lay_words_with_half_weight() -> None:
    terms = idx.query_terms("quais ferramentas")
    assert terms[idx.stem("ferramentas")] == 1.0
    assert terms[idx.stem("tool")] == idx.SYNONYM_WEIGHT


# --- leitura das fontes ----------------------------------------------------------------------

PAGE = """---
title: Agente
group: Documentação Técnica
order: 1
description: O loop.
---

Texto de abertura.

```html-raw
<div>nada disto entra</div>
```

## Dispatcher {#dispatcher kind=classe file=brain/core/dispatcher.py}

O dispatcher acha a tool, confere o tier e pede **confirmação** na [conversa](agente.html).

| Tier | Comportamento |
|---|---|
| `READ` | executa direto |

## Testes

pytest tests/test_agent.py
"""


def test_parse_sections_titles_anchors_and_plain_text() -> None:
    sections = idx.parse(PAGE, "docs/pages/agente.md")
    assert [s.heading for s in sections] == ["", "Dispatcher", "Testes"]
    intro, dispatcher, _ = sections
    assert intro.page == "Agente" and "nada disto" not in intro.text
    assert dispatcher.anchor == "dispatcher"
    assert "confirmação na conversa" in dispatcher.text  # sem ** nem link
    assert "READ · executa direto" in dispatcher.text  # tabela achatada, sem a linha ---
    adr = idx.parse("# ADR-099 — Teste\n\n## Contexto\n\nTexto.\n", "docs/decisions/ADR-099.md")
    assert adr[-1].page == "ADR-099 — Teste" and adr[-1].anchor == "contexto"


def _docs(root: Path) -> Path:
    (root / "pages").mkdir(parents=True)
    (root / "decisions").mkdir()
    (root / "pages" / "agente.md").write_text(PAGE, encoding="utf-8")
    (root / "pages" / "llm.md").write_text(
        "---\ntitle: LLM\n---\n\n## Failover {#failover}\n\n"
        "O router troca de provider quando um falha: DeepInfra, Groq, Ollama.\n",
        encoding="utf-8",
    )
    (root / "decisions" / "ADR-001.md").write_text(
        "# ADR-001 — Tools\n\n## Decisão\n\nCada tool é um plugin com schema.\n", encoding="utf-8"
    )
    return root


def test_only_versioned_sources_are_read(tmp_path: Path) -> None:
    root = _docs(tmp_path / "docs")
    secret = tmp_path / "secret.md"
    secret.write_text("JUNIPER_GROQ_API_KEY=gsk_" + "a" * 40, encoding="utf-8")
    (root / ".env").write_text("TOKEN=x", encoding="utf-8")
    (root / "pages" / "notas.txt").write_text("fora", encoding="utf-8")
    (root / "decisions" / "rascunho.md").write_text("fora", encoding="utf-8")
    os.symlink(secret, root / "pages" / "atalho.md")  # aponta para fora da pasta: ignorado
    names = sorted(p.name for p in idx.source_files(root))
    assert names == ["ADR-001.md", "agente.md", "llm.md"]


def test_search_ranks_by_relevance_and_uses_synonyms(tmp_path: Path) -> None:
    index = idx.DocsIndex.build(idx.load_sections(_docs(tmp_path / "docs")))
    assert index.search("o que acontece quando um provider falha?")[0].section.anchor == "failover"
    # "ferramenta" (leigo) acha o texto que só diz "tool"
    assert any(h.section.source.endswith("ADR-001.md") for h in index.search("ferramentas"))
    assert index.search("o que você?") == []  # só palavras vazias: nada a buscar
    assert index.search("xyzzy plugh") == []  # palavras que a documentação não tem
    assert all(h.section.heading != "Testes" for h in index.search("pytest testes agente"))


def test_index_is_rebuilt_only_when_a_file_changes(tmp_path: Path) -> None:
    root = _docs(tmp_path / "docs")
    first = idx.get_index(root)
    assert idx.get_index(root) is first
    page = root / "pages" / "llm.md"
    page.write_text(page.read_text(encoding="utf-8") + "\nNovo parágrafo sobre zumbis.\n")
    second = idx.get_index(root)
    assert second is not first and second.search("zumbis")


def test_excerpt_masks_secrets_and_respects_the_budget() -> None:
    key = "gsk_" + "A1b2C3d4" * 6
    section = idx.Section(
        "docs/pages/x.md", "X", "Chaves", "chaves", f"A chave é {key} no .env.\n\n" + "y " * 500
    )
    text = idx.excerpt(section, "chave", 120)
    assert key not in text and len(text) <= 121


# --- detector ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "como funciona a sua memória?",
        "qual a sua arquitetura?",
        "o que é o dispatcher?",
        "como você faz failover entre LLMs?",
        "me explica como você lembra das coisas",
        "por que você pergunta antes de apagar?",
        "quais ferramentas você tem?",
        "Juniper, como você foi construída?",
    ],
)
def test_detector_recognizes_questions_about_herself(message: str) -> None:
    assert about_self(message)


@pytest.mark.parametrize(
    "message",
    [
        "oi, tudo bem?",
        "como foi seu dia?",
        "marca dentista amanhã às 16h",
        "o que tem na minha agenda amanhã?",
        "toca Oceano do Djavan",
        "qual o clima hoje?",
        "lembra que eu uso ZSH",
    ],
)
def test_detector_ignores_everything_else(message: str) -> None:
    assert not about_self(message)


# --- a documentação real ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "page"),
    [
        ("o que é o dispatcher?", "agente.md"),
        ("como você faz failover entre LLMs?", "llm"),
        ("como funciona o sonho da memória v2?", "memoria-v2.md"),
        ("como funciona o protocolo websocket?", "protocolo.md"),
        ("qual a sua arquitetura?", "architecture.md"),
    ],
)
def test_real_docs_answer_questions_with_the_right_pages(question: str, page: str) -> None:
    hits = ctx.search(question)
    assert any(page in h.section.source for h in hits), [h.section.where for h in hits]


def test_no_excerpt_of_the_real_docs_carries_a_key_or_token() -> None:
    # Every excerpt went through the secret filter: no key, token or card pattern survives.
    leaks = []
    for doc in idx.get_index().docs:
        text = idx.excerpt(doc.section, doc.section.heading or doc.section.page, 4000)
        if any(pattern.search(text) for _, pattern in TOKEN_PATTERNS):
            leaks.append(doc.section.where)
    assert not leaks


def test_a_key_pasted_into_the_docs_never_reaches_the_model(tmp_path: Path) -> None:
    root = _docs(tmp_path / "docs")
    key = "gsk_" + "Z9y8X7w6" * 6
    (root / "pages" / "vazou.md").write_text(
        f"---\ntitle: Vazou\n---\n\n## Chave da Groq\n\nJUNIPER_GROQ_API_KEY={key}\n",
        encoding="utf-8",
    )
    index = idx.DocsIndex.build(idx.load_sections(root))
    hits = index.search("chave da groq")
    assert hits and hits[0].section.source.endswith("vazou.md")
    block = ctx.render(hits, "chave da groq", 1500)
    assert key not in block and find_secret(block.replace(ctx.HEADER, "")) is None


def test_docs_context_only_for_questions_about_her(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ctx.docs_context("marca dentista amanhã às 16h") is None
    block = ctx.docs_context("como você pede confirmação antes de apagar algo?")
    assert block is not None
    assert block.startswith(ctx.HEADER) and "<documentacao>" in block
    assert len(block) <= len(ctx.HEADER) + settings.metadocs.max_chars + 600  # títulos e fontes
    monkeypatch.setattr(settings.metadocs, "auto_context", False)
    assert ctx.docs_context("como você pede confirmação antes de apagar algo?") is None


def test_docs_context_never_breaks_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disco")

    monkeypatch.setattr(ctx, "get_index", boom)
    assert ctx.docs_context("qual a sua arquitetura?") is None


# --- a tool -------------------------------------------------------------------------------------


def test_tool_is_read_on_the_brain_and_only_takes_a_query(monkeypatch: pytest.MonkeyPatch) -> None:
    assert DocsSearch.tier is Tier.READ and DocsSearch.locality is Locality.CLOUD
    assert set(DocsSearch.parameters["properties"]) == {"query"}
    assert DocsSearch.available()
    monkeypatch.setattr(settings.metadocs, "enabled", False)
    assert not DocsSearch.available()


def test_tool_returns_excerpts_and_handles_empty_queries() -> None:
    result = asyncio.run(DocsSearch().run(query="como funciona a confirmação na conversa"))
    assert result.success and result.output and "<documentacao>" in result.output
    assert result.metadata and result.metadata["results"] >= 1
    empty = asyncio.run(DocsSearch().run(query="   "))
    assert not empty.success
    nothing = asyncio.run(DocsSearch().run(query="xyzzy plugh"))
    assert nothing.success and "não fala disso" in (nothing.output or "")


# --- o agente -----------------------------------------------------------------------------------


def _agent(provider: FakeProvider, self_docs: object) -> Agent:
    return Agent(
        provider,
        Dispatcher(conversational=True, tool_registry=ToolRegistry()),
        tool_registry=ToolRegistry(),
        self_docs=self_docs,  # type: ignore[arg-type]
    )


def test_agent_puts_the_excerpts_right_before_the_question() -> None:
    provider = FakeProvider([say("Eu funciono assim.")])
    agent = _agent(provider, lambda text: "TRECHOS" if "funciona" in text else None)
    asyncio.run(agent.run("como você funciona?"))
    messages = provider.seen[0]
    position = next(i for i, m in enumerate(messages) if m.content == "TRECHOS")
    assert messages[position].role == "system" and messages[position + 1].role == "user"
    question = messages[position + 1].content or ""
    assert "como você funciona?" in question
    # Só naquela chamada: nada disso fica no histórico.
    assert all(m.content != "TRECHOS" for m in agent.session.history)


def test_agent_without_excerpts_or_with_a_broken_provider_keeps_going() -> None:
    def broken(text: str) -> str:
        raise RuntimeError("quebrou")

    for self_docs in (lambda text: None, broken):
        provider = FakeProvider([say("Oi!")])
        assert asyncio.run(_agent(provider, self_docs).run("oi")) == "Oi!"
        assert not any(m.role == "system" and m.content == "TRECHOS" for m in provider.seen[0])
