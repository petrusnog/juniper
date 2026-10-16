---
title: Protocolo cliente ↔ brain
group: Produção
order: 2
description: O contrato do WebSocket entre os clientes e o cérebro: handshake, chat em stream, sessões, memória, erros e reconexão.
status: proposto
tags: websocket protocolo mensagens hello welcome chat.send chat.token heartbeat reconexão erros códigos fechamento sessão json envelope
---

> **Nota:** esta página resume o [ADR-012](adr-012.html) (proposto). O contrato em código (modelos pydantic e JSON Schema, T-074) será a fonte da verdade; em caso de divergência, vale o schema gerado.

## Visão geral {#visao-geral}

Cada cliente (CLI remoto, app Electron) abre **um único WebSocket** com o cérebro e usa a mesma conexão para chat, sessões, comandos de memória e (se for executor) tools remotas. O cliente é fino: o histórico e a confirmação pendente vivem **no servidor**.

- Endereço: `ws://<host>:<porta>/v1/ws` (dentro do Tailscale; o tráfego já é cifrado).
- Quadros de texto, JSON UTF-8, **uma mensagem por quadro**.
- `GET /health` na mesma porta (HTTP, sem autenticação) para o Docker e o deploy.

## Envelope {#envelope}

Toda mensagem tem `type` e, quando faz parte de um pedido, `id` (único, gerado por quem pede). Respostas e eventos levam `reply_to` com o `id` original. Campos desconhecidos são **ignorados**; tipos desconhecidos geram `error` com `code: "bad_request"` sem fechar a conexão. Limites: 1 MiB por mensagem; 32.000 caracteres por `chat.send` *(a confirmar)*.

```json
{ "type": "chat.send", "id": "c-7f3a", "text": "o que tenho amanhã?" }
```

## Conexão e autenticação {#conexao}

1. O cliente abre o WebSocket com `Authorization: Bearer <token>`. Token ausente ou errado: 401 antes do upgrade, ou fechamento `4401`.
2. O cliente manda `hello`; o servidor responde `welcome`.

```json
{ "type": "hello", "protocol_version": 1,
  "client": { "kind": "desktop", "version": "0.1.0", "device_id": "a1b2c3", "device_name": "PC do Petrus" },
  "resume": { "latest": true } }
```

```json
{ "type": "welcome", "protocol_version": 1, "server_version": "4.0.0", "heartbeat_seconds": 30,
  "session": { "id": "20261015-101500-ab12cd", "resumed": true, "turns": 6 } }
```

`resume` é opcional (`{"session_id": "..."}` ou `{"latest": true}`). Versão incompatível: `error` com `unsupported_version` e fechamento `4426`. Uma segunda conexão com o mesmo `device_id` **substitui** a primeira (`4409`).

## Heartbeat {#heartbeat}

O servidor manda `{"type":"ping"}` a cada 30 s e o cliente responde `{"type":"pong"}` (heartbeat de aplicação, igual em todos os clientes e através de proxies). **90 s sem nenhum sinal**, em qualquer direção, e quem notou fecha a conexão (`4408`).

## Chat {#chat}

| Direção | Tipo | Campos | Quando |
|---|---|---|---|
| cliente → servidor | `chat.send` | `id`, `text` | Uma mensagem sua. |
| servidor → cliente | `chat.started` | `reply_to`, `turn_id`, `session_id` | O turno começou. |
| servidor → cliente | `chat.token` | `turn_id`, `text` | Um pedaço da resposta (em ordem). |
| servidor → cliente | `chat.end` | `turn_id`, `finish`, `usage?`, `timing?` | O turno terminou. |
| cliente → servidor | `chat.cancel` | `turn_id` | Interrompe o turno em andamento. |
| servidor → cliente | `error` | `reply_to?`, `code`, `message`, `retryable` | Qualquer falha. |

`finish` é `ok`, `cancelled`, `max_iterations` ou `error`.

```json
{ "type": "chat.started", "reply_to": "c-7f3a", "turn_id": "t-12", "session_id": "20261015-101500-ab12cd" }
{ "type": "chat.token", "turn_id": "t-12", "text": "Amanhã você tem " }
{ "type": "chat.end", "turn_id": "t-12", "finish": "ok" }
```

- **Um turno por vez por conversa:** `chat.send` durante um turno recebe `turn_in_progress`.
- **Confirmação:** o cliente não tem estado de confirmação. A Juniper pergunta em palavras e o "sim" é só o próximo `chat.send`; o sistema o interpreta ([Agente](agente.html#confirmacao)).

## Sessões {#sessoes}

| Tipo | Efeito |
|---|---|
| `session.new` | Abre uma conversa nova (a atual fica salva). |
| `session.resume` (`session_id` ou `latest`) | Retoma uma conversa; responde `session.state` com `turns` e o `history` (só `role` e `text`) para o cliente redesenhar a tela. |
| `session.list` | Responde `session.listed` com as conversas (`id`, `updated_at`, `turns`, `preview`). |
| `session.detached` (servidor → cliente) | Outra conexão assumiu esta conversa. |

Uma conversa fica ligada a **uma conexão** por vez *(a confirmar)*. Uma **confirmação pendente não sobrevive** a uma queda ou a um `session.resume`: depois de retomar, nada executa sem um novo "sim".

## Comandos de memória {#memoria}

```json
{ "type": "memory.request", "id": "m-1", "op": "search", "args": { "query": "framework php", "limit": 5 } }
{ "type": "memory.result", "reply_to": "m-1", "ok": true, "data": { "results": [ { "id": "mem_...", "text": "O projeto X usa Laravel 11.", "score": 0.86 } ] } }
```

| `op` | `args` | Resultado |
|---|---|---|
| `list` | `type?`, `all?` | `memories[]` |
| `search` | `query`, `limit?`, `threshold?` | `results[]` com `score` |
| `forget` | `id` | `deleted` (a confirmação "Pode apagar?" é feita pelo **cliente** antes de enviar) |
| `export` | `vectors?` | `profile` e `memories[]` |
| `profile.get`, `profile.set`, `profile.forget` | — | o perfil |

`memory reindex` é administrativo e local ao servidor.

## Erros {#erros}

`{"type":"error","reply_to":"...","code":"...","message":"...","retryable":true}`. `message` é o texto amigável em PT-BR; `code` é para o programa decidir.

| `code` | Quando | `retryable` |
|---|---|---|
| `unauthorized` | Token ausente ou inválido. | não |
| `unsupported_version` | `protocol_version` incompatível. | não |
| `bad_request` | Mensagem malformada ou tipo desconhecido. | não |
| `turn_in_progress` | `chat.send` com um turno em andamento. | sim |
| `session_not_found` | `session.resume` de um id inexistente. | não |
| `llm_auth` / `llm_rate_limit` / `llm_unavailable` / `llm_timeout` / `llm_error` | Falhas de LLM (hierarquia de [LLM](llm.html#erros)). | só as 3 do meio |
| `memory_unavailable` | MongoDB fora do ar. | sim |
| `not_found` | Id de memória inexistente. | não |
| `executor_unavailable` | A tool roda num dispositivo desconectado. | sim |
| `internal` | Falha inesperada (o detalhe vai ao log, nunca ao cliente). | sim |

A conexão **continua aberta** depois de qualquer erro de pedido; um erro de turno também fecha o turno com `chat.end` (`finish: "error"`).

**Códigos de fechamento:** `4400` protocolo violado, `4401` não autorizado, `4408` sem sinal de vida, `4409` substituída, `4426` versão incompatível; `1001` servidor saindo, `1012` servidor **reiniciando** (deploys).

## Executor {#executor}

As mensagens `register`, `registered`, `invoke` e `result` do ADR-007 viajam na **mesma conexão**. Veja [Executor](executor.html).

## Reconexão e queda do cliente {#reconexao}

- **Backoff exponencial com jitter:** 1, 2, 4, 8, 16 e depois 30 s (±20%); zera depois de uma conexão estável por 30 s. Em `1012` (deploy), reconecta quase na hora. `4401` e `4426` param e mostram o motivo.
- Ao reconectar, manda `hello` com a conversa que estava aberta e, se for executor, repete o `register`.
- Offline, o cliente **não enfileira** mensagens: mostra "Juniper indisponível no momento" e deixa o texto na caixa.
- **Se a conexão cai no meio de um turno,** o turno **termina no servidor** (limitado por `agent.max_iterations` e por um tempo total de 120 s, *a confirmar*), a conversa é salva com a resposta e os tokens que ninguém leu são descartados. Ao retomar, `session.state.history` traz a resposta completa. A [extração de memória](memoria-longo-prazo.html#backgroundextractor) pertence à conversa, não à conexão, e conclui mesmo com o cliente fora.

## Tasks da Sprint 3 {#tasks}

T-074 (modelos e JSON Schema), T-075 (servidor, auth, heartbeat), T-076 (chat, sessões), T-077 (memória), T-078 (executores), T-086 (CLI remoto), T-088 a T-090 (Electron).
