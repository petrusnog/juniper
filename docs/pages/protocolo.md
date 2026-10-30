---
title: API WebSocket
group: Referência
order: 2
description: Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
status: pronta
tags: api websocket protocolo mensagens hello welcome chat.send chat.token chat.end heartbeat sessão memória executor invoke voz erros códigos fechamento json schema protocol_version
---

> **Nota:** o contrato vem do [ADR-012](../decisions/ADR-012.md), estendido pelos ADRs [014](../decisions/ADR-014.md) (executor) e [023](../decisions/ADR-023.md) (voz). A fonte da verdade em código são os modelos de `brain/clients/protocol.py`, exportados para o JSON Schema `docs/protocol/schema.json` (`python scripts/export_protocol_schema.py`). Em caso de divergência, vale o schema. Um guia prático para escrever um cliente está em [Integrar com o cérebro](integrar-cliente.md).

## Conexão {#conexao}

| Item | Valor |
|---|---|
| Endereço | `ws://<host>:<porta>/v1/ws` (porta padrão 8765). Sem TLS: use dentro de uma rede privada cifrada ([ADR-013](../decisions/ADR-013.md)). |
| Autenticação | Cabeçalho `Authorization: Bearer <client_id>.<client_secret>` no *handshake* HTTP ([Usuários e credenciais](usuarios.md)); enquanto `deployment.auth.legacy_token` for `true`, o token estático também vale. Sem ele, ou com credencial errada: HTTP 401 antes do upgrade. |
| Quadros | Texto, JSON UTF-8, **uma mensagem por quadro**. Quadros binários são recusados. |
| Tamanho | Até 1 MiB por mensagem; até 32.000 caracteres em `chat.send.text`. |
| Versão | `protocol_version: 1`. Mudanças compatíveis (mensagens, campos ou códigos novos) não mudam a versão. |
| Saúde | `GET /health` na mesma porta, sem autenticação ([API HTTP](api-http.md#health)). |

O `client_id` e o `client_secret` saem do cadastro de usuários ([Usuários e credenciais](usuarios.md)); o token estático, quando ainda vale, é gerado no primeiro boot e fica em `deployment.auth.token_file` ([Servidor WebSocket](servidor.md#iniciar)). O `welcome` traz `user` (`id`, `name`, `role`) com quem o servidor reconheceu; é só informação e não muda a versão do protocolo.

## Envelope {#envelope}

Toda mensagem tem `type`. Um pedido do cliente leva um `id` (único, escolhido por quem pede); a resposta e os eventos ligados a ele levam `reply_to` com esse `id`. Campos desconhecidos são **ignorados**; um `type` desconhecido gera `error` com `code: "bad_request"`, sem fechar a conexão.

```json
{ "type": "chat.send", "id": "c-7f3a", "text": "o que tenho amanhã?" }
```

## Handshake {#handshake}

Depois do upgrade, o cliente manda `hello` e espera `welcome`.

| Direção | `type` | Campos |
|---|---|---|
| cliente → servidor | `hello` | `protocol_version` (int), `client` {`kind`, `version`, `device_id`, `device_name?`}, `resume?` {`session_id`} ou {`latest`: true}, `trace?` (bool) |
| servidor → cliente | `welcome` | `protocol_version`, `server_version`, `heartbeat_seconds`, `session` {`id`, `resumed`, `turns`} |

```json
{ "type": "hello", "protocol_version": 1,
  "client": { "kind": "mobile", "version": "0.1.0", "device_id": "mobile-a1b2c3", "device_name": "Celular" },
  "resume": { "latest": true } }
```

```json
{ "type": "welcome", "protocol_version": 1, "server_version": "4.0.0", "heartbeat_seconds": 30,
  "session": { "id": "20261015-101500-ab12cd", "resumed": true, "turns": 6 } }
```

- `client.kind` identifica o cliente (`cli`, `telegram`, `mobile`...). O servidor usa isso para saber o canal do pedido ([Servidor WebSocket](servidor.md#conversas)).
- `resume` leva `session_id` **ou** `latest: true`, nunca os dois nem nenhum (`bad_request`). Sem `resume`, o servidor abre uma conversa nova.
- Versão incompatível: `error` com `unsupported_version` e fechamento `4426`.
- Uma segunda conexão com o mesmo `device_id` **substitui** a primeira (`4409`).

## Heartbeat {#heartbeat}

| Direção | `type` | Quando |
|---|---|---|
| servidor → cliente | `ping` | A cada `heartbeat_seconds` (30 s). |
| cliente → servidor | `pong` | Em resposta a cada `ping`. |

**90 s sem nenhum sinal**, em qualquer direção, e quem notou fecha a conexão (`4408`).

## Chat {#chat}

| Direção | `type` | Campos | Quando |
|---|---|---|---|
| cliente → servidor | `chat.send` | `id`, `text` | Uma mensagem do usuário. |
| servidor → cliente | `chat.started` | `reply_to`, `turn_id`, `session_id` | O turno começou. |
| servidor → cliente | `chat.token` | `turn_id`, `text` | Um pedaço da resposta, em ordem. |
| servidor → cliente | `chat.end` | `turn_id`, `finish`, `usage?`, `timing?` | O turno terminou. |
| cliente → servidor | `chat.cancel` | `turn_id` | Interrompe o turno em andamento. A mensagem do usuário fica no histórico; a resposta parcial não. |

`finish` é `ok`, `cancelled`, `max_iterations` ou `error`.

**Comandos do servidor no texto.** Um `chat.send` cujo texto é `/vazou [nota]` não vira turno: o servidor guarda o relato de vazamento ([Auditoria de vazamentos](vazamentos.md#relato)) e responde com a sequência normal `chat.started`, `chat.token`, `chat.end`, sem tocar no histórico. Não muda o `protocol_version` nem o schema ([ADR-012](../decisions/ADR-012.md), adendo de 2026-10-29): um cliente que não sabe do comando só vê uma resposta de texto. Esse "turno" não entra no histórico, então um cliente que conta turnos (como o bot do Telegram) não deve contá-lo.

```json
{ "type": "chat.started", "reply_to": "c-7f3a", "turn_id": "t-12", "session_id": "20261015-101500-ab12cd" }
{ "type": "chat.token", "turn_id": "t-12", "text": "Amanhã você tem " }
{ "type": "chat.end", "turn_id": "t-12", "finish": "ok" }
```

- **Um turno por vez por conversa:** `chat.send` durante um turno recebe `turn_in_progress`.
- **Confirmação:** o cliente não tem estado de confirmação. A Juniper pergunta em palavras dentro de `chat.token`, e o "sim" é só o próximo `chat.send`, interpretado pelo cérebro ([ADR-010](../decisions/ADR-010.md)).
- **Queda no meio do turno:** o turno termina no servidor e a conversa é salva com a resposta; os tokens não lidos são descartados. Ao retomar, `session.state.history` traz a resposta completa.

## Sessões {#sessoes}

| Direção | `type` | Campos | Efeito |
|---|---|---|---|
| cliente → servidor | `session.new` | `id` | Abre uma conversa nova (a atual fica salva). Responde `session.state`. |
| cliente → servidor | `session.resume` | `id`, `session_id` ou `latest` | Retoma uma conversa. Responde `session.state`. |
| servidor → cliente | `session.state` | `reply_to`, `session_id`, `turns`, `history?` [{`role`, `text`}], `note?` | O estado da conversa, para o cliente redesenhar a tela. |
| cliente → servidor | `session.list` | `id`, `limit?` | Pede a lista de conversas. |
| servidor → cliente | `session.listed` | `reply_to`, `sessions` [{`id`, `updated_at`, `turns`, `preview`}] | As conversas, mais recentes primeiro. |
| servidor → cliente | `session.detached` | `session_id`, `reason` | Outra conexão assumiu esta conversa. |

Uma conversa fica ligada a **uma conexão** por vez. A confirmação pendente **não sobrevive** a uma queda, a um `session.resume` (inclusive da própria conversa) nem a outra conexão assumir a conversa.

## Tools do servidor {#tools}

| Direção | `type` | Campos |
|---|---|---|
| cliente → servidor | `tools.request` | `id` |
| servidor → cliente | `tools.listed` | `reply_to`, `tools` [{`name`, `description`, `tier`, `locality`}] |

A lista inclui as tools dos executores conectados. É o que `juniper tools` mostra no modo servidor.

## Memória {#memoria}

| Direção | `type` | Campos |
|---|---|---|
| cliente → servidor | `memory.request` | `id`, `op`, `args` |
| servidor → cliente | `memory.result` | `reply_to`, `ok`, `data` |

```json
{ "type": "memory.request", "id": "m-1", "op": "search", "args": { "query": "framework php", "limit": 5 } }
{ "type": "memory.result", "reply_to": "m-1", "ok": true, "data": { "results": [ { "id": "mem_...", "text": "O projeto X usa Laravel 11.", "score": 0.86 } ] } }
```

| `op` | `args` | `data` |
|---|---|---|
| `list` | `type?`, `all?` | `memories[]` |
| `search` | `query`, `limit?`, `threshold?` | `results[]` com `score` |
| `forget` | `id` | `deleted` (a pergunta "Pode apagar?" é feita pelo **cliente** antes de enviar) |
| `export` | `vectors?` | `profile` e `memories[]` |
| `profile.get` | — | o perfil |
| `profile.set` | `text?`, `name?`, `birth_date?`, `nationality?`, `gender?` | o perfil |
| `profile.forget` | — | o perfil apagado |

Sem memória de longo prazo no servidor, o pedido recebe `error` com `memory_unavailable`. `memory reindex` e os comandos da Memória V2 são administrativos e só rodam no servidor.

## Executor {#executor}

Um cliente que cumpre tools no dispositivo ([Executor](executor.md)) usa a mesma conexão.

| Direção | `type` | Campos |
|---|---|---|
| cliente → servidor | `register` | `tools` [{`name`, `description`, `parameters?` (JSON Schema), `tier`}] |
| servidor → cliente | `registered` | `tools` (as aceitas) |
| servidor → cliente | `invoke` | `call_id`, `tool_name`, `arguments`, `tier?`, `timeout_ms` |
| cliente → servidor | `result` | `call_id`, `success`, `output?`, `error?`, `metadata?` |

```json
{ "type": "invoke", "call_id": "k-9", "tool_name": "open_url",
  "arguments": { "url": "https://music.youtube.com/watch?v=abc" }, "tier": "write", "timeout_ms": 10000 }
{ "type": "result", "call_id": "k-9", "success": true, "output": "Abri o link." }
```

- O registro é **por conexão**: some quando a conexão cai, e o executor se registra de novo ao reconectar (a cada `welcome`).
- O cérebro não escala tier: recusa registrar uma tool com tier mais permissivo do que a política que ele já tem para ela, e uma tool desconhecida do servidor tem piso `WRITE` ([ADR-014](../decisions/ADR-014.md)).
- O cliente **revalida** cada `invoke` (catálogo, schema, tier, política) e responde `result` com `success: false` e `metadata.code` (`unknown_tool`, `invalid_args`, `tier_mismatch`, `policy_denied`, `os_error`) quando recusa.
- Um `result` que chega depois do `timeout_ms` é ignorado.

## Voz {#voz}

Extensão compatível do [ADR-023](../decisions/ADR-023.md): uma sessão de voz por conexão. O áudio vai em **quadros de texto JSON com base64** (PCM 16 bits *little-endian*, mono; `seq` crescente; de 20 a 100 ms por quadro). Cada fala transcrita vira um turno de chat comum, então a resposta também chega como `chat.started`, `chat.token` (a legenda) e `chat.end` ([Voz por áudio](voz-audio.md)).

| Direção | `type` | Campos | Quando |
|---|---|---|---|
| cliente → servidor | `voice.start` | `id`, `format` {`encoding`: `pcm_s16le`, `sample_rate` (8000–48000), `channels`: 1}, `local_stt?`, `local_tts?`, `route?` (`speaker`, `headset`), `wake?` | Abre a sessão; `format` é o do microfone (prefira 16 kHz). `wake: true`: a sessão foi aberta no modo mãos livres (botão da notificação, atalho ou link `juniper://voz`); o cérebro cumprimenta primeiro e encerra sozinho depois de tocar uma música ou após `voice.wake_idle_seconds` sem fala. |
| servidor → cliente | `voice.ready` | `reply_to`, `input`, `output`, `stt`, `tts` | Sessão aberta: formatos e se STT e TTS são `cloud` ou `local`. |
| ambos | `voice.audio` | `seq`, `data`, `turn_id?`, `last?` | Do cliente: o microfone. Do servidor: a fala do turno; `last` fecha a fala. |
| servidor → cliente | `voice.state` | `state` (`listening`, `thinking`, `speaking`, `idle`), `turn_id?` | O que está acontecendo. `listening` **com** `turn_id` = descarte o áudio daquele turno (foi cortado). |
| servidor → cliente | `voice.transcript` | `text`, `final` | O que o usuário disse, como foi entendido. |
| servidor → cliente | `voice.say` | `turn_id`, `text`, `last?` | Fallback de TTS: o aparelho fala o texto. |
| servidor → cliente | `voice.fallback` | `component` (`stt`, `tts`), `mode` (`local`, `cloud`), `reason` (`quota`, `budget`, `error`, `recovered`) | Troca de provedor. |
| cliente → servidor | `voice.text` | `id`, `text` | Fala transcrita no aparelho (fallback de STT). |
| cliente → servidor | `voice.interrupt` | — | Calar a Juniper agora. |
| cliente → servidor | `voice.route` | `route` | A saída de áudio mudou (fone com fio ligado ou desligado; o Bluetooth conta como `speaker`). |
| ambos | `voice.end` | `reason` (`client`, `command`, `silence`, `error`, `server`, `playback`) | Encerra a sessão. `playback`: a música foi aberta e o comentário terminou; o app fecha a conversa de voz e a música segue tocando. |

`voice.audio` e `voice.end` valem nos dois sentidos; as demais só na direção da tabela (a outra recebe `bad_request`). Base64 inválido ou `seq` fora do limite também é `bad_request`. Sem fone (`route` diferente de `headset`, ou ausente), o servidor ignora o microfone enquanto a Juniper fala (e por 1 s depois), e o app também não o envia nessa janela.

## Diário de mensagens {#trace}

| Direção | `type` | Campos |
|---|---|---|
| servidor → cliente | `trace` | `layer`, `text`, `at` |

Só é enviado se o `hello` pediu (`"trace": true`) **e** o servidor tem `trace.enabled`. O cliente escreve as linhas no seu `message.log` ([Observabilidade](observabilidade.md#message-log)).

## Erros {#erros}

```json
{ "type": "error", "reply_to": "c-7f3a", "code": "llm_unavailable", "message": "Não consegui falar com nenhum LLM agora.", "retryable": true }
```

`message` é o texto amigável em português; `code` é para o programa decidir. A conexão **continua aberta** depois de qualquer erro de pedido; um erro de turno também fecha o turno com `chat.end` (`finish: "error"`).

| `code` | Quando | `retryable` |
|---|---|---|
| `unauthorized` | Token ausente ou inválido. | não |
| `unsupported_version` | `protocol_version` incompatível. | não |
| `bad_request` | Mensagem malformada, tipo desconhecido ou direção errada. | não |
| `turn_in_progress` | `chat.send` com um turno em andamento. | sim |
| `session_not_found` | `session.resume` de um id inexistente. | não |
| `llm_auth` | Nenhum provedor aceitou as credenciais. | não |
| `llm_rate_limit` | Limite de uso dos provedores. | sim |
| `llm_unavailable` | Provedores fora do ar. | sim |
| `llm_timeout` | Provedores não responderam a tempo. | sim |
| `llm_error` | Outro erro de LLM (pedido inválido, modelo inexistente). | não |
| `memory_unavailable` | MongoDB fora do ar ou memória desligada. | sim |
| `not_found` | Id de memória inexistente. | não |
| `executor_unavailable` | A tool roda num dispositivo que não está conectado. | sim |
| `voice_unavailable` | Voz desligada (`voice.enabled: false`) ou sem provedor configurado. | não |
| `voice_busy` | Já existe uma sessão de voz nesta conexão. | não |
| `internal` | Falha inesperada (o detalhe vai ao log, nunca ao cliente). | sim |

## Códigos de fechamento {#fechamento}

| Código | Significado | O que o cliente faz |
|---|---|---|
| `1001` | Servidor saindo. | Reconecta com recuo. |
| `1011` | Erro interno na conexão. | Reconecta com recuo. |
| `1012` | Servidor **reiniciando** (deploy). | Reconecta quase na hora. |
| `4400` | Protocolo violado. | Corrige o cliente. |
| `4401` | Não autorizado: credencial recusada, ou revogada (ou usuário desativado) com a conexão aberta. | Para e pede a credencial de novo. |
| `4408` | Sem sinal de vida. | Reconecta. |
| `4409` | Substituída por outra conexão com o mesmo `device_id`. | Para. |
| `4426` | Versão incompatível. | Para e pede atualização. |

## Reconexão {#reconexao}

- **Recuo exponencial com variação:** 1, 2, 4, 8, 16 e depois 30 s (±20%); zera depois de 30 s de conexão estável. Em `1012`, reconecta quase na hora. `4401`, `4409` e `4426` param e mostram o motivo.
- Ao reconectar, manda `hello` com a conversa que estava aberta e, se for executor, repete o `register`.
- Offline, o cliente **não enfileira** mensagens: avisa que a Juniper está indisponível e mantém o texto digitado.

## Para saber mais {#para-saber-mais}

- [Glossário](glossario.md) (Produto): Os termos usados nesta documentação, explicados em uma ou duas frases.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
