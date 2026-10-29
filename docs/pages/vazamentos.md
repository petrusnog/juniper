---
title: Auditoria de vazamentos
group: Documentação Técnica
section: Núcleo
order: 9
description: Como a Juniper registra as respostas em que escapou maquinaria (tags, instruções, trechos do prompt), o que cada registro guarda, como consultar com `juniper vazamentos` e por que esses turnos ficam fora da memória.
status: pronta
tags: vazamento leak leakguard auditoria leak_audit prompt eco tag conhecimento parameter instrução quarentena sonho memória mongodb ttl vazamentos
---

## Visão geral {#visao-geral}

O [LeakGuard](agente.md#persona) corta da fala os vazamentos que conhece, por uma lista de marcadores. A auditoria cobre os que ele **não** conhece e serve de investigação: registra, em cada chamada ao LLM suspeita, o texto **bruto** do modelo, o texto **entregue** e o que disparou.

Ela **não muda a resposta**. O que a pessoa vê continua a ser decidido só pelo guarda; a auditoria apenas lê e registra.

```text
LLM ──texto bruto──► LeakGuard ──texto entregue──► cliente e histórico
          │                            │
          └──────── detector amplo ────┘──► registro (MongoDB, leak_audit) e turno fora da memória
```

## O detector {#detector kind=módulo file=brain/core/leak_audit.py}

As regras são largas de propósito. Um falso positivo aqui só gera um registro a mais, que uma pessoa lê.

| Regra | O que procura |
|---|---|
| `tool_markup` | Marcação de tool call que o provedor não leu: `DSML`, `<function`, `</parameter>`, `<tool_call>`. |
| `special_token` | Tokens de template de chat: `<|im_end|>`, as barras largas `<｜`. |
| `tag` | Qualquer tag do tipo `<palavra>` ou `</palavra>`. Os blocos de contexto são tags (`<conhecimento>`, `<memorias>`, `<estado>`). |
| `bracket_start` | Resposta que começa com `[` ou `]`: abrir ou fechar uma nota de sistema (`[Canal: …]`). |
| `role_prefix` | Linha que fala como um papel do chat: `Mensagem de sistema:`, `system:`, `assistant:`, `Usuário:`. |
| `instruction` | Instrução para o modelo, nunca para a pessoa: "Responda como a Juniper", "não mencione", "no contexto e nas memórias". |
| `prompt_echo` | Uma sequência de `leak_audit.echo_words` (10) palavras igual a algum bloco de sistema **daquela chamada** (persona, perfil, memórias, documentação, nota de canal). É a regra que não depende de formato: pega um vazamento de um jeito que ninguém listou ainda. |

O detector olha o texto entregue e, se ele for diferente, o bruto. Cada acerto leva a regra, onde foi visto (`delivered` se a pessoa viu, `raw` se só o modelo escreveu e o guarda cortou), a posição e um trecho curto.

## O registro {#registro kind=conceito file=brain/memory/leak_audit.py}

Um registro é gravado quando o guarda cortou algo **ou** o detector acusou. Ele fica na coleção `leak_audit` do MongoDB e **expira sozinho** depois de `leak_audit.retention_days` (30), por um índice TTL.

| Campo | O que é |
|---|---|
| `leaked` | A pessoa viu algo suspeito (algum acerto em `delivered`). `false` quer dizer que só o bruto tinha, e o guarda limpou. |
| `guard_triggered` | O `LeakGuard` cortou algo nessa chamada. |
| `rules`, `hits` | As regras disparadas e cada acerto (regra, onde, posição, trecho). |
| `raw`, `delivered` | O texto inteiro do modelo antes do guarda e o que foi entregue. |
| `user_message` | A mensagem da pessoa como o modelo a leu (com o carimbo de data). |
| `blocks` | Os blocos de sistema do prompt: **tipo e tamanho**, nunca o conteúdo (`persona`, `profile`, `memories`, `docs`, `channel`). |
| `channel`, `voice` | O cliente do turno (`telegram`, `mobile`, `cli`…) e se era voz. |
| `provider`, `model`, `finish_reason`, `tool_calls` | Quem atendeu (depois do failover, se houve), como terminou e quais tools pediu. |
| `version`, `commit` | A versão da Juniper e o commit da imagem (`GIT_COMMIT`, do *build*). |

O registro guarda texto de conversa, por isso vive só no seu MongoDB e expira. Sem MongoDB (`memory.session.store: file`), nada é gravado; só o evento `leak_audit` vai ao [log técnico](observabilidade.md), **sem texto** (id, `leaked`, regras, provider, modelo, canal). Uma falha ao gravar nunca derruba o turno: vira um evento `error` com `where: leak_audit`.

## Fora da memória {#quarentena kind=conceito file=brain/core/agent.py}

Quando a pessoa **viu** algo suspeito (`leaked`), o turno é separado da memória:

- O `Turn` do agente sai com `leaked: true` e a resposta **vazia**. A mensagem da pessoa continua valendo; o que a Juniper disse naquele turno não chega à extração de memórias nem à transcrição que o sonho lê.
- A transcrição da [Memória V2](memoria-v2.md) grava o turno com `leaked: true` e `assistant` vazio. O sonho trata como uma resposta que não existe, igual faz com a resposta de um turno com tool.
- O texto vazado só existe no registro de auditoria, até expirar.
- O evento `leak_quarantined` marca o turno no log.

Isso vale mesmo com `leak_audit.enabled: false`: desligar a auditoria só deixa de gravar o registro. Um turno que o guarda limpou, sem nada suspeito no texto entregue, segue para a memória normalmente. Turnos transcritos antes desta separação não são reprocessados.

O histórico da conversa em andamento não muda: a resposta entregue continua nele, como a pessoa a viu, e o `Session.scrub` segue limpando os marcadores conhecidos antes de cada turno.

## Consultar {#consultar}

O comando lê o MongoDB do processo onde roda. Na VPS, use o atalho do contêiner ([Operação](operacao.md#atalho)).

```bash
juniper vazamentos                 # os registros dos últimos 7 dias
juniper vazamentos --vazados       # só os que chegaram à pessoa
juniper vazamentos --resumo        # contagem por regra, modelo e canal
juniper vazamentos --dias 30 -n 100
juniper vazamentos --ver 3f9a1c2b  # um caso inteiro (o id ou o começo dele)
```

Para cruzar com o resto do turno, o `session` do registro filtra o log técnico: `juniper logs -s <sessão>`.

## Configuração {#configuracao}

| Opção | Padrão | O que faz |
|---|---|---|
| `leak_audit.enabled` | `true` | Grava os registros. Desligada, o turno suspeito continua fora da memória. |
| `leak_audit.retention_days` | `30` | Dias até o registro expirar (de 1 a 365). Mudar o valor ajusta o índice existente. |
| `leak_audit.echo_words` | `10` | Palavras seguidas iguais a um bloco de sistema que contam como `prompt_echo` (de 5 a 50). |

## Testes {#testes}

```bash
pytest tests/test_leak_audit.py -v
```

Cobrem os vazamentos reais (`</parameter>`, `]` seguido de "Mensagem de sistema…", `<conhecimento>`), a fala natural sem acertos, o eco do prompt, o registro, a separação do turno, o sink quebrado que não derruba a conversa, o armazenamento e o TTL.

## Para saber mais {#para-saber-mais}

- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Memória V2](memoria-v2.md) (Documentação Técnica): A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com o algoritmo de sonho.
- [Operação e manutenção](operacao.md) (Produção): O dia a dia do servidor: comandos dentro do contêiner, logs, saúde e comandos administrativos.
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
