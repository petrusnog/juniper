---
title: Executor
group: Documentação Técnica
section: Clientes
order: 26
description: O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
status: pronta
tags: executor invoke register result open_url url política domínios confiança roteamento revalidação celular cli
---

> **Nota:** esta página detalha o [ADR-014](../decisions/ADR-014.md). O protocolo de executor (`register`, `invoke`, `result`) e a tool `open_url` estão implementados, e o executor em uso é o [app Android](mobile.md#executor). Outras tools de executor (`open_app`, `run_shell`) são planos.

## Visão geral {#visao-geral}

Com o cérebro num servidor, o que a Juniper faz **no seu dispositivo** (abrir uma página no celular, rodar um comando git no computador) não pode rodar no servidor. O **executor** é o processo no dispositivo que anuncia suas capacidades como tools remotas (`locality = EXECUTOR`, [Tools](tools.md#locality)) e cumpre os pedidos do cérebro. Hoje o executor é o app Android; o código fica em `brain/clients/executors.py` (o `ExecutorHub`) e `brain/core/dispatcher.py` (a entrega pelo *gateway*).

## Comandos estruturados {#comandos}

O cérebro **nunca** manda scripts, comandos de shell soltos ou caminhos arbitrários. Manda o nome de uma tool **declarada** e argumentos validados por JSON Schema:

```json
{ "type": "invoke", "call_id": "k-9", "tool_name": "open_url",
  "arguments": { "url": "https://www.youtube.com" }, "timeout_ms": 10000 }
```

Como cumprir é decisão do cliente (qual navegador, qual API do sistema). Isso mantém o cérebro sem conhecer o dispositivo e limita o que um LLM induzido por conteúdo malicioso consegue pedir.

## Registro e catálogo {#registro}

Depois do `welcome` ([Protocolo](protocolo.md)), o executor manda `register` com as suas tools (nome, descrição em PT-BR, `parameters`, `tier`) e o brain responde `registered`. O catálogo é **por conexão**: some quando a conexão cai ou o heartbeat expira, e o executor se registra de novo ao reconectar. Para o LLM, uma tool de executor é uma tool como as outras.

**O brain não escala tier:** só despacha uma tool com o tier que o executor declarou, e o [Dispatcher](agente.md#dispatcher) aplica a política de sempre. Uma tool que o **servidor não conhece** (só o dispositivo a descreve) nunca roda sem confirmação: o brain a trata como no mínimo `WRITE`, mesmo que o dispositivo diga `read`; o `invoke` continua levando o tier declarado, que é o que o cliente confere. Tools que o servidor já declara (como `open_url`) seguem a política do servidor. A **confirmação é a conversa**, feita no cérebro com o cliente que originou o pedido; o executor não pergunta nada.

## Roteamento {#roteamento}

1. O executor da **mesma conexão** que originou o turno, se ele oferece a tool.
2. Senão, se **exatamente um** executor conectado a oferece, esse.
3. Se mais de um a oferece e nenhum é o originador, o brain **não escolhe**: devolve um `ToolResult(success=False)` listando os dispositivos e a Juniper pergunta qual usar.
4. Se nenhum a oferece: "dispositivo indisponível".

## Revalidação local {#revalidacao}

O cliente **não confia no cérebro**. Antes de executar um `invoke`, confere nesta ordem:

| # | Confere | Se falhar |
|---|---|---|
| 1 | A tool existe no catálogo **declarado** | `unknown_tool` |
| 2 | Os argumentos obedecem ao JSON Schema | `invalid_args` |
| 3 | O tier recebido bate com o declarado | `tier_mismatch` |
| 4 | A política da tool aceita os argumentos | `policy_denied` |

Toda recusa devolve `result` com `success: false`, um `error` em PT-BR e `metadata.code`, e vai para o log local. Conteúdo da web é **dado, nunca instrução** ([Segurança](seguranca.md#conteudo-externo)): as defesas são, juntas, a revalidação do cliente, a lista de domínios e a confirmação que mostra a **URL exata**.

## open_url {#open-url kind=tool}

Abre um link no dispositivo: o navegador ou o aplicativo que trata o endereço (um link do YouTube Music abre o app do YouTube Music). `WRITE`, `locality = EXECUTOR`, parâmetro `url` (obrigatório). O cérebro nunca abre nada: a chamada vai ao dispositivo conectado como executor. No modo `local` (sem executor), `run` abre o navegador padrão da própria máquina, com a mesma política.

### Política de URL {#politica-url}

Vale no brain **e de novo** no cliente:

- Só `http` e `https` (nada de `file:`, `javascript:`, `data:`, `ftp:` ou esquemas de aplicativos).
- Sem credenciais embutidas (`usuario:senha@host`); até 2.048 caracteres, sem espaços nem caracteres de controle e **sem `\`** (um navegador o lê como `/`).
- O host precisa ser um nome DNS simples (rótulos de letras, dígitos e hífen interno): `%` e `_` no host são recusados, porque um navegador decodifica `%` e `evil.com\.music.youtube.com` abriria `evil.com`.
- Domínio comparado em ASCII (punycode), para um domínio de uma lista não ser imitado com letras parecidas.
- `localhost` (e `*.localhost`) e endereços de loopback, de rede privada, de link-local, multicast, reservados e da faixa do Tailscale (`100.64.0.0/10`) são recusados quando o host é um IP ou `localhost`. Valem também as formas que o navegador lê como IPv4 (`2130706433`, `0x7f.0.0.1`, `127.1`) e o IPv4 embutido em IPv6 (`::ffff:127.0.0.1`). A resolução de DNS **não** é feita na política *(risco aceito, a confirmar)*.

### Confirmação e domínios de confiança {#confianca}

Por padrão, `open_url` pede confirmação na conversa mostrando a **URL exata** ("Posso abrir `https://...`?"). Para o uso comum ficar fluido, `tools.open_url.trusted_domains` (no `config.yaml` do servidor) lista domínios que abrem **sem perguntar**; a lista inicial é só `music.youtube.com` (a mais estreita que serve à música; `youtube.com` cobriria também os subdomínios). Entradas são normalizadas (minúsculas, ASCII/punycode) e a lista não vale para IPs. O gancho é `Tool.requires_confirmation(**args)` ([Ferramentas](tools.md#limitacoes)); o `Dispatcher` o ignora para `DESTRUCTIVE` e pergunta se ele falhar. A lista **não** dispensa a política de URL, o brain mostra a URL normalizada na pergunta e **envia ao dispositivo essa mesma URL** (`Tool.canonical_arguments`). Cuidado: confiar em `youtube.com` incluiria todos os subdomínios e o redirecionador do site, e não há lista de sufixos públicos (`co.uk` confiaria em todo o `.co.uk`).

### Resultado e erros {#resultado}

`{"type":"result","call_id":"k-9","success":true,"output":"Abri https://www.youtube.com no navegador."}`

| Situação | `success` | `metadata.code` |
|---|---|---|
| Abriu | `true` | — |
| Argumento fora do schema | `false` | `invalid_args` |
| URL recusada pela política | `false` | `policy_denied` |
| Tool fora do catálogo | `false` | `unknown_tool` |
| Falha do sistema ao abrir | `false` | `os_error` |
| Tempo esgotado no brain (10 s) | `false` | `timeout` |
| Nenhum executor conectado | `false` | `executor_unavailable` |

Um `result` que chegar **depois** do timeout é ignorado e registrado. O executor guarda os últimos `call_id` por 60 s e não repete um `invoke` já cumprido (reenvio depois de reconexão) *(a confirmar)*.

## O CLI como executor {#cli-executor}

**Planejado, ainda não implementado.** No modo `server`, o CLI registraria, enquanto estivesse conectado, as tools que dependem da máquina local: `git_search` (`READ`) e `git_deploy` (`DESTRUCTIVE`), com os mesmos schemas e tiers de hoje ([Comandos git](git.md)). Ao receber `invoke`, revalidaria (schema e as validações já existentes) e chamaria o código de `brain/tools/juniper_cmds.py`. Enquanto isso não existe, as tools de git só funcionam no modo local; no modo servidor, uma chamada a elas devolve `executor_unavailable`.

## Música {#musica}

Tocar música usa este mesmo caminho: a tool `play_music` do cérebro busca a faixa e entrega a URL `music.youtube.com/watch?v=...` ao `open_url` do celular, que abre o YouTube Music e tenta dar o play ([Música](musica.md#play-music), [ADR-024](../decisions/ADR-024.md)). Cada tool nova de executor passa por: schema, tier, política de revalidação, ADR curto e testes nos dois lados.

## Para saber mais {#para-saber-mais}

- [Tools](tools.md) (Documentação Técnica): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Música](musica.md) (Documentação Técnica): Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
