---
title: Integrar com o cérebro
group: Produção
order: 8
description: Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
status: guia
tags: integração cliente api websocket python exemplo hello chat.send streaming confirmação reconexão executor register invoke dashboard http automação
---

O cérebro não tem SDK: qualquer programa que fale WebSocket e JSON pode ser um cliente. Os clientes oficiais (o [CLI](cli.html), o [bot do Telegram](telegram.html) e o [app Android](mobile.html)) usam exatamente o contrato descrito na [API WebSocket](protocolo.html). Este guia mostra o essencial para começar.

## Regras de um bom cliente {#regras}

Um cliente da Juniper é **fino** ([ADR-012](adr-012.html)):

- Só envia o que o usuário escreveu e mostra o que o cérebro devolve. Não decide nada, não guarda memória, não acessa o MongoDB.
- **Não interpreta confirmações.** Quando a Juniper pergunta "Posso marcar?", a resposta do usuário é só o próximo `chat.send`; quem decide se é um "sim" é o cérebro ([ADR-010](adr-010.html)). Nunca mande um "sim" automático.
- Não enfileira mensagens offline: se a conexão caiu, avise e deixe o usuário reenviar.
- Guarda o token como segredo (cofre do sistema, variável de ambiente, arquivo `600`); nunca em código, URL ou log.
- Usa um `device_id` estável por instalação (uma segunda conexão com o mesmo `device_id` derruba a primeira).

## Exemplo mínimo em Python {#python}

Requer `pip install websockets`. O cliente conecta, faz o handshake, responde ao heartbeat, manda uma mensagem e imprime a resposta conforme ela chega.

```python
import asyncio
import json
import os
import uuid

from websockets.asyncio.client import connect

URL = os.environ.get("JUNIPER_URL", "ws://127.0.0.1:8765/v1/ws")
TOKEN = os.environ["JUNIPER_TOKEN"]          # o token dos clientes


async def ask(text: str) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with connect(URL, additional_headers=headers, max_size=1 << 20) as ws:
        await ws.send(json.dumps({
            "type": "hello",
            "protocol_version": 1,
            "client": {"kind": "script", "version": "0.1.0", "device_id": "script-exemplo"},
            "resume": {"latest": True},
        }))
        welcome = json.loads(await ws.recv())
        if welcome["type"] != "welcome":
            raise RuntimeError(welcome.get("message", welcome))

        request_id = f"c-{uuid.uuid4().hex[:8]}"
        await ws.send(json.dumps({"type": "chat.send", "id": request_id, "text": text}))

        async for raw in ws:
            msg = json.loads(raw)
            kind = msg["type"]
            if kind == "ping":
                await ws.send(json.dumps({"type": "pong"}))
            elif kind == "chat.token":
                print(msg["text"], end="", flush=True)
            elif kind == "chat.end":
                print(f"\n[fim: {msg['finish']}]")
                return
            elif kind == "error":
                print(f"\n[erro {msg['code']}: {msg['message']}]")
                if msg.get("reply_to") == request_id:
                    return


asyncio.run(ask("o que tenho amanhã na agenda?"))
```

Para testar contra o ambiente de desenvolvimento: `JUNIPER_TOKEN="$(cat .juniper-dev/state/token)" python exemplo.py` ([Primeiros passos](primeiros-passos.html)). O repositório traz um cliente de teste parecido, `scripts/smoke_server.py`, que também mostra `--resume` e `--health`.

## Confirmações {#confirmacoes}

Uma ação que altera algo termina o turno com uma pergunta, em texto, dentro de `chat.token`:

```text
→ chat.send   "marca dentista amanhã às 16h"
← chat.token  "Posso marcar «Dentista», amanhã das 16h às 17h?"
← chat.end    finish: ok
→ chat.send   "sim"                         (o que o usuário respondeu)
← chat.token  "Pronto, marquei."
```

Mostre a pergunta ao usuário e mande a resposta dele como um `chat.send` comum. A pergunta pendente morre se a conexão cair ou se o cliente trocar de conversa ([API WebSocket](protocolo.html#sessoes)).

## Conversas {#conversas}

- `hello.resume` com `{"latest": true}` retoma a conversa mais recente; sem `resume`, abre uma nova.
- `session.list` → `session.listed` lista as conversas; `session.resume` → `session.state` traz o histórico (`role` e `text`) para redesenhar a tela.
- Se outro cliente assumir a conversa, chega `session.detached`.

## Reconexão {#reconexao}

Implemente o recuo da [API WebSocket](protocolo.html#reconexao): 1, 2, 4, 8, 16 e depois 30 s, com variação de ±20%, zerando depois de 30 s estáveis. Trate os códigos de fechamento: `1012` (o servidor está reiniciando, por exemplo num deploy) reconecta quase na hora; `4401` (token), `4409` (substituída) e `4426` (versão) param e avisam. Ao reconectar, mande `hello` com o `session_id` da conversa que estava aberta.

## Expor capacidades do dispositivo (executor) {#executor}

Um cliente também pode oferecer tools que rodam no dispositivo dele ([Executor](executor.html), [ADR-014](adr-014.html)). Depois de cada `welcome`, registre:

```json
{ "type": "register", "tools": [ {
    "name": "open_url",
    "description": "Abre um link no dispositivo.",
    "parameters": { "type": "object", "properties": { "url": { "type": "string" } }, "required": ["url"] },
    "tier": "write" } ] }
```

O cérebro responde `registered` com as tools aceitas e, quando o modelo usar uma delas (e a confirmação, se houver, for dada), manda `invoke`. Responda sempre com um `result` (`success`, `output` ou `error`), nunca deixe um `invoke` sem resposta, e **revalide** os argumentos antes de executar: o cliente não confia no cérebro. Uma tool que o servidor não conhece é tratada como no mínimo `WRITE`, e uma tool que o servidor já declara não pode ser registrada com um tier mais permissivo.

## Gerar tipos {#tipos}

O contrato completo está em `docs/protocol/schema.json` (JSON Schema, gerado de `brain/clients/protocol.py`). Use-o para gerar tipos na linguagem do seu cliente, como o app Android faz para TypeScript (`mobile/scripts/gen-protocol-types.js`). Campos desconhecidos devem ser ignorados: novas versões compatíveis acrescentam campos e mensagens sem mudar `protocol_version`.

## Ler dados sem conversar {#http}

Para um painel, um relatório ou uma automação que só lê, a [API HTTP](api-http.html) do dashboard entrega a memória em JSON, com `Authorization: Bearer <token>`:

```bash
curl -s -H "Authorization: Bearer $JUNIPER_TOKEN" "http://<servidor>:8766/api/memories?limit=10"
curl -s "http://<servidor>:8765/health"
```

Ela é somente leitura. Mudanças na memória passam pelo WebSocket (`memory.request`) ou pela própria conversa.

## Segurança {#seguranca}

- Conecte só por uma rede privada cifrada: o protocolo é `ws://` ([Visão geral da produção](producao.html#rede)).
- Um cliente com o token tem o mesmo poder de qualquer outro: todas as tools, com confirmação. Dê o token só a programas em que você confia.
- Trate todo texto que vem do cérebro como **texto**: nunca o interprete como HTML ou comando.
