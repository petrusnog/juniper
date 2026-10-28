---
title: Visão geral da arquitetura
group: Documentação Técnica
section: Arquitetura
order: 1
description: As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
status: guia
tags: arquitetura camadas cérebro clientes transporte regras modos local server pastas código fluxo
---

## Visão geral {#visao-geral}

A Juniper segue a lógica de um assistente sempre disponível: **um cérebro** (o agente, que pensa, lembra e decide) e **vários corpos** (os clientes: terminal, bot do Telegram, app Android, painel web). Os clientes são finos: capturam a mensagem e mostram a resposta. Quem decide é o cérebro ([ADR-007](adr-007.html)).

```text
 Clientes                    Transporte                          Cérebro (brain/)
┌──────────────────┐                                ┌────────────────────────────────────────┐
│ CLI (juniper)    │──┐                             │ clients/server.py  ── ChatHandler      │
│ Bot do Telegram  │──┼── WebSocket /v1/ws ────────►│        │                               │
│ App Android      │──┘   (token + rede privada)    │ core/: Agent ─► Dispatcher ─► tools/   │
│  (texto, voz,    │                                │          │            │                │
│   executor)      │◄── tokens, áudio, invoke ──────│        llm/ (router)  memory/ (MongoDB)│
└──────────────────┘                                │ dashboard/ (HTTP, só leitura)          │
 Navegador ──── HTTP :8766 ───────────────────────►│ voice/ (VAD, STT, TTS)                 │
                                                    └────────────────────────────────────────┘
                                                       │            │             │
                                              DeepInfra/Groq/   Google Calendar,  MongoDB
                                                 Ollama          YouTube, Google TTS
```

## Mapa em camadas {#camadas}

Leia de cima para baixo: o pedido desce pelas camadas até o mundo real, e a resposta sobe de volta.

```html-raw
<div class="layers">
  <div class="layer"><div class="lname"><b>5 · Interfaces</b><small>Onde se conversa. Finas: só capturam e exibem.</small></div><div class="lblocks">
    <div class="blk done"><b>CLI</b><small>juniper: chat, streaming, comandos de memória</small></div>
    <div class="blk done"><b>Telegram</b><small>bot por long polling (ADR-016)</small></div>
    <div class="blk done"><b>App Android</b><small>texto, voz e executor (ADR-022)</small></div>
    <div class="blk done"><b>Dashboard</b><small>painel web somente leitura (ADR-020)</small></div></div></div>
  <div class="lgap">↓ mensagem · resposta em stream ↑</div>
  <div class="layer"><div class="lname"><b>4 · Transporte</b><small>Existe quando o cérebro roda como servidor.</small></div><div class="lblocks">
    <div class="blk done"><b>WebSocket único</b><small>chat, sessões, memória, executor e voz (ADR-012)</small></div>
    <div class="blk done"><b>Token + rede privada</b><small>sem portas públicas (ADR-013)</small></div></div></div>
  <div class="lgap">↓ ↑</div>
  <div class="layer"><div class="lname"><b>3 · Cérebro</b><small>Decide o que fazer: brain/core/.</small></div><div class="lblocks">
    <div class="blk done"><b>Agent</b><small>decide → chama tool → reavalia</small></div>
    <div class="blk done"><b>Dispatcher</b><small>acha a tool, checa o tier, pede confirmação</small></div>
    <div class="blk done"><b>Session</b><small>persona + histórico</small></div>
    <div class="blk done"><b>Consent</b><small>entende o "sim" da conversa</small></div></div></div>
  <div class="lgap">↓ pede · devolve ↑</div>
  <div class="layer"><div class="lname"><b>2 · Capacidades</b><small>O que o cérebro usa para pensar, agir e lembrar.</small></div><div class="lblocks">
    <div class="blk done"><b>LLM Router</b><small>DeepInfra → Groq → Ollama</small></div>
    <div class="blk done"><b>Tools</b><small>agenda, música, git, memória, open_url</small></div>
    <div class="blk done"><b>Memória</b><small>sessão, longo prazo, perfil e V2</small></div>
    <div class="blk done"><b>Voz</b><small>VAD, STT e TTS no cérebro (ADR-023)</small></div></div></div>
  <div class="lgap">↓ ↑</div>
  <div class="layer"><div class="lname"><b>1 · Mundo externo</b><small>Fora do código da Juniper.</small></div><div class="lblocks">
    <div class="blk done"><b>DeepInfra / Groq / Ollama</b><small>modelos de linguagem</small></div>
    <div class="blk done"><b>Google Cloud</b><small>Calendar, YouTube, Text-to-Speech</small></div>
    <div class="blk done"><b>MongoDB</b><small>conversas, memórias e perfil</small></div>
    <div class="blk done"><b>Servidor</b><small>uma VPS com Docker (ADR-013)</small></div></div></div>
  <div class="lgap"></div>
  <div class="layer side-layer"><div class="lname"><b>Transversais</b><small>Atravessam todas as camadas.</small></div><div class="lblocks">
    <div class="blk done"><b>Configuração</b><small>config.yaml + .env, um só ponto de acesso</small></div>
    <div class="blk done"><b>Segurança em 4 tiers</b><small>READ · WRITE · DESTRUCTIVE · FORBIDDEN</small></div>
    <div class="blk done"><b>Logs estruturados</b><small>JSONL com segredos mascarados</small></div></div></div>
</div>
```

## Modos de execução {#modos}

| Modo | `deployment.mode` | Onde o cérebro roda | Clientes possíveis |
|---|---|---|---|
| Local | `local` | Dentro do processo do comando `juniper` ([Instalação sem Docker](install.html)). | Só o próprio terminal. |
| Servidor | `server` | Num processo próprio (`juniper serve`), em geral num contêiner ([Servidor WebSocket](servidor.html)). | CLI remoto, Telegram, app Android, dashboard. |

O código do agente é o mesmo nos dois modos; muda só quem o hospeda. Em desenvolvimento e em produção o modo é `server` ([Primeiros passos](primeiros-passos.html), [Produção](producao.html)).

## Uma frase, passo a passo {#fluxo}

Exemplo: no app, você escreve *"amanhã quero uma ida ao dentista, às 16h"*.

| # | O que acontece | Onde |
|---|---|---|
| 1 | O app manda `chat.send` pelo WebSocket. | Cliente → [protocolo](protocolo.html#chat) |
| 2 | O servidor confere que a conversa está livre, abre o turno (`chat.started`) e entrega o texto ao [Agent](agente.html#agent). | [Servidor](servidor.html) |
| 3 | O agente monta o pedido: persona, perfil, memórias relevantes, a hora atual, o canal do pedido e a lista de tools. | [Agente](agente.html#ciclo), [Memória](memoria.html) |
| 4 | O [LLMRouter](llm.html#llmrouter) envia ao primeiro provedor da fila; se ele falhar antes de responder, tenta o próximo. | [LLM](llm.html) |
| 5 | O modelo pede `create_event` com título e horário resolvidos. | [Tools](tools.html) |
| 6 | O [Dispatcher](agente.html#dispatcher) acha a tool; o `precheck` consulta o Google Calendar **antes** de perguntar qualquer coisa. | [Agenda Google](google-calendar.html) |
| 7 | Sem conflito, a Juniper pergunta "Posso marcar «Dentista», segunda 16h–17h?"; a pergunta chega como `chat.token` e o turno termina com `chat.end`. | [Confirmação](agente.html#confirmacao) |
| 8 | O seu "sim" é o próximo `chat.send`; `consent.py` o interpreta e o evento é criado. | Cérebro |
| 9 | Tudo vai para o [log](observabilidade.html), a conversa é salva e, em segundo plano, o que vale lembrar vai para a [memória de longo prazo](memoria-longo-prazo.html#extracao). | Transversal |

## Regras que mantêm tudo simples {#regras}

1. **O cérebro não conhece as interfaces.** `brain/core/` nunca importa de `brain/clients/`. Por isso a mesma Juniper serve todos os clientes ([ADR-007](adr-007.html)).
2. **Cliente fino.** Nenhum cliente tem lógica de agente nem acesso ao MongoDB; histórico e confirmação pendente vivem no servidor ([ADR-012](adr-012.html)).
3. **Providers são trocáveis.** O cérebro fala com um `LLMProvider`, nunca com um provedor específico ([ADR-001](adr-001.html), [ADR-009](adr-009.html)).
4. **Tool nova = arquivo novo.** Cada capacidade é um plugin que se registra sozinho ([Tools](tools.html), [ADR-002](adr-002.html)).
5. **Perigo exige permissão.** Ler é livre; alterar pede um "sim" na conversa; destruir também, com aviso; o proibido nunca executa ([Segurança](seguranca.html), [ADR-006](adr-006.html), [ADR-010](adr-010.html)).
6. **Um só ponto de configuração.** Tudo vem de `brain.config.settings` ([Configuração](configuracao.html), [ADR-003](adr-003.html)).
7. **Falha esperada é resultado tipado**, nunca exceção que derruba a conexão ou o servidor.
8. **Shell é interface, Python é cérebro.** O `shell/` só orquestra os comandos legados; a lógica vive em `brain/`.

## Onde cada parte mora no código {#codigo}

| Parte | Pasta / arquivo | Página |
|---|---|---|
| Agente, dispatcher, sessão, confirmação, relógio, persona | `brain/core/` | [Agente](agente.html) |
| Provedores de LLM e roteador | `brain/llm/` | [LLM](llm.html) |
| Tools, registry, política de URL | `brain/tools/` | [Tools](tools.html) |
| Memória (sessão, longo prazo, V2) | `brain/memory/` | [Memória](memoria.html) |
| Servidor, chat, executores, memória remota, sessão de voz | `brain/clients/server.py`, `chat.py`, `executors.py`, `memory_service.py`, `voice_session.py` | [Servidor WebSocket](servidor.html) |
| Modelos do protocolo | `brain/clients/protocol.py` (gera `docs/protocol/schema.json`) | [API WebSocket](protocolo.html) |
| CLI e cliente remoto | `brain/clients/cli.py`, `remote.py`, `remote_cli.py` | [CLI](cli.html) |
| Bot do Telegram | `brain/clients/telegram.py` | [Bot do Telegram](telegram.html) |
| Voz (VAD, STT, TTS, orçamento) | `brain/voice/` | [Voz por áudio](voz-audio.html) |
| Música (YouTube, cota, OAuth, gosto) | `brain/music/` | [Música](musica.html) |
| Dashboard | `brain/dashboard/` | [Dashboard](dashboard.html) |
| Metadocumentação (busca local na própria documentação) | `brain/metadocs/`, `brain/tools/docs.py` | [Metadocumentação](metadocs.html) |
| Configuração | `brain/config.py` | [Configuração](configuracao.html) |
| Logs, tempos, diário, agendador | `brain/eventlog.py`, `httplog.py`, `logfiles.py`, `timing.py`, `trace.py`, `scheduler.py` | [Observabilidade](observabilidade.html) |
| Persona | `prompts/persona.core.md` | [Agente](agente.html#persona) |
| App Android | `mobile/` | [App mobile](mobile.html) |
| Comandos legados (git) | `shell/` | [Comandos git](git.html) |
| Contêineres | `Dockerfile`, `docker-compose.*.yml`, `docker/` | [Ambiente Docker](dev-docker.html), [Produção](producao.html) |

## Estado e evolução {#estado}

| Parte | Estado |
|---|---|
| Agente, LLM com failover, tools, agenda, git | Implementado. |
| Memória de sessão, de longo prazo, perfil e Memória V2 | Implementado (a V2 vem desligada). |
| Servidor WebSocket, CLI remoto, Telegram, dashboard | Implementado. |
| App Android (texto, voz, executor, autoplay) e voz no cérebro | Implementado; validação contínua em aparelhos reais. |
| Música (busca, tocar, playlists, gosto) | Implementado. |
| Metadocumentação (responder sobre a própria arquitetura) | Implementado ([ADR-025](adr-025.html)). |
| Login com MFA | Proposto ([ADR-021](adr-021.html)). |
| `run_shell`, `open_app`, `web_fetch`, `web_search` | Previstos no modelo de segurança, ainda não implementados. |
| Cliente desktop em Electron | Descartado em favor do app Android ([ADR-015](adr-015.html), [ADR-022](adr-022.html)). |

O histórico de cada escolha está nas [Decisões (ADRs)](adr.html).
