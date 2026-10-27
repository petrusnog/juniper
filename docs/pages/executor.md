---
title: Executor
group: Produção
order: 3
description: O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
status: proposto
tags: executor invoke register result open_url url política domínios confiança roteamento revalidação desktop cli
---

> **Nota:** esta página resume o [ADR-014](adr-014.html) (proposto). A primeira tool de executor, `open_url`, já existe no cérebro (T-139); as outras (`media_play`, `open_app`, `run_shell`) vêm depois. O cliente que a cumpre é o app do celular (substitui o desktop do ADR-015).

## Visão geral {#visao-geral}

Com o cérebro na VPS, o que a Juniper faz **na sua máquina** (abrir uma página, rodar um comando git) não pode rodar no servidor. O **executor** é o processo no dispositivo que anuncia suas capacidades como tools remotas (`locality = EXECUTOR`, [Tools](tools.html#locality)) e cumpre os pedidos do cérebro. O executor do PC roda **dentro do app Electron** ([Desktop](desktop.html)); o CLI remoto também pode ser executor.

## Comandos estruturados {#comandos}

O cérebro **nunca** manda scripts, comandos de shell soltos ou caminhos arbitrários. Manda o nome de uma tool **declarada** e argumentos validados por JSON Schema:

```json
{ "type": "invoke", "call_id": "k-9", "tool_name": "open_url",
  "arguments": { "url": "https://www.youtube.com" }, "timeout_ms": 10000 }
```

Como cumprir é decisão do cliente (qual navegador, qual API do sistema). Isso mantém o cérebro sem conhecer o dispositivo e limita o que um LLM induzido por conteúdo malicioso consegue pedir.

## Registro e catálogo {#registro}

Depois do `welcome` ([Protocolo](protocolo.html)), o executor manda `register` com as suas tools (nome, descrição em PT-BR, `parameters`, `tier`) e o brain responde `registered`. O catálogo é **por conexão**: some quando a conexão cai ou o heartbeat expira, e o executor se registra de novo ao reconectar. Para o LLM, uma tool de executor é uma tool como as outras.

**O brain não escala tier:** só despacha uma tool com o tier que o executor declarou, e o [Dispatcher](agente.html#dispatcher) aplica a política de sempre. Uma tool que o **servidor não conhece** (só o dispositivo a descreve) nunca roda sem confirmação: o brain a trata como no mínimo `WRITE`, mesmo que o dispositivo diga `read`; o `invoke` continua levando o tier declarado, que é o que o cliente confere. Tools que o servidor já declara (como `open_url`) seguem a política do servidor. A **confirmação é a conversa**, feita no cérebro com o cliente que originou o pedido; o executor não pergunta nada.

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

Toda recusa devolve `result` com `success: false`, um `error` em PT-BR e `metadata.code`, e vai para o log local. Conteúdo da web é **dado, nunca instrução** ([Segurança](seguranca.html#conteudo-externo)): as defesas são, juntas, a revalidação do cliente, a lista de domínios e a confirmação que mostra a **URL exata**.

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

Por padrão, `open_url` pede confirmação na conversa mostrando a **URL exata** ("Posso abrir `https://...`?"). Para o uso comum ficar fluido, `tools.open_url.trusted_domains` (no `config.yaml` do servidor) lista domínios que abrem **sem perguntar**; a lista inicial é só `music.youtube.com` (a mais estreita que serve à música; `youtube.com` cobriria também os subdomínios). Entradas são normalizadas (minúsculas, ASCII/punycode) e a lista não vale para IPs. O gancho é `Tool.requires_confirmation(**args)` ([Ferramentas](tools.html#limitacoes)); o `Dispatcher` o ignora para `DESTRUCTIVE` e pergunta se ele falhar. A lista **não** dispensa a política de URL, o brain mostra a URL normalizada na pergunta e **envia ao dispositivo essa mesma URL** (`Tool.canonical_arguments`). Cuidado: confiar em `youtube.com` incluiria todos os subdomínios e o redirecionador do site, e não há lista de sufixos públicos (`co.uk` confiaria em todo o `.co.uk`).

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

No modo `server`, o CLI registra, enquanto estiver conectado, as tools que dependem da máquina local: `git_search` (`READ`) e `git_deploy` (`DESTRUCTIVE`), com os mesmos schemas e tiers de hoje ([Comandos git](git.html)). Ao receber `invoke`, revalida (schema e as validações já existentes) e chama o código de `brain/tools/juniper_cmds.py`. A confirmação do `git_deploy` continua sendo a conversa. Sem o CLI conectado, as tools não existem para o cérebro.

## Evolução: tocar música {#evolucao}

Não decidido. Opções para "toca *She Said She Said*": (1) o cérebro acha a **URL direta** (precisa de uma tool de busca) e o cliente só abre o link; (2) o cliente abre a **página de busca** e você clica; (3) o Electron carrega o serviço numa **janela própria que ele controla** por script (mais confiável, mais trabalho). Cada tool nova passa por: schema, tier, política de revalidação, ADR curto e testes nos dois lados.

## Tasks da Sprint 3 {#tasks}

T-078 (`register`/`invoke`/`result` e roteamento no brain), T-139 (a tool `open_url` no cérebro, retomando a T-079), T-087 (CLI como executor); o executor do celular vem na épica do app mobile (o do Electron, T-090, foi descartado).
