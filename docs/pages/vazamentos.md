---
title: Auditoria de vazamentos
group: Documentação Técnica
section: Núcleo
order: 9
description: Como a Juniper registra as respostas em que escapou maquinaria (tags, instruções, trechos do prompt), como marcar um vazamento na hora com /vazou (texto e voz), o que cada registro guarda, como consultar com `juniper vazamentos` e por que esses turnos ficam fora da memória.
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

O detector toma **duas decisões, com precisões diferentes**:

- **Auditoria:** usa todas as regras, largas de propósito. Um falso positivo (um `<div>` numa resposta de programação, "Sistema: Android 14", um fato citado da memória) só gera um registro a mais, que uma pessoa lê.
- **Quarentena** (o turno fora da memória, abaixo): só as regras marcadas com ✓, que a fala natural não dispara.

| Regra | Quarentena | O que procura |
|---|---|---|
| `tool_markup` | ✓ | Marcação de tool call que o provedor não leu: `DSML`, `<function`, `</parameter>`, `<tool_call>`. |
| `special_token` | ✓ | Tokens de template de chat: `<|im_end|>`, as barras largas `<｜`. |
| `context_tag` | ✓ | Tag de um bloco de contexto dela (`<conhecimento>`, `<memorias>`, `<estado>`…): as conhecidas e qualquer tag presente nos blocos de sistema daquela chamada. |
| `tag` | | Qualquer outra tag do tipo `<palavra>` (`Optional<String>`, `<div>`). |
| `system_role` | ✓ | Linha que fala como o sistema ou o modelo: `Mensagem de sistema:`, `system:`, `assistant:`. |
| `role_prefix` | | Linha que começa com uma palavra que pode ser papel ou só rótulo: `Sistema:`, `Usuário:`, `Ferramenta:`. |
| `bracket_start` | | Resposta que começa com `[` ou `]`: abrir ou fechar uma nota de sistema (`[Canal: …]`), mas também um link Markdown. |
| `instruction` | ✓ | Instrução para o modelo, nunca para a pessoa: "Responda como a Juniper", "não mencione", "no contexto e nas memórias". |
| `prompt_echo` | ✓ | Uma sequência de `leak_audit.echo_words` (10) palavras igual às **instruções** da chamada: a persona e a nota de canal. É a regra que não depende de formato. |
| `context_echo` | | A mesma sequência igual aos **dados** da chamada: perfil, memórias, documentação e os blocos de dados dentro da persona (`<opinioes>`, `<estado>`). Citar uma memória é muitas vezes o que se pediu a ela. |

O detector olha o texto entregue e, se ele for diferente, o bruto. Cada acerto leva a regra, onde foi visto (`delivered` se a pessoa viu, `raw` se só o modelo escreveu e o guarda cortou), a posição e um trecho curto. Um acerto que aparece nos dois textos conta uma vez, como `delivered`. Os textos são lidos **com segredos mascarados** (`mask_secrets`, como na [memória](memoria-longo-prazo.md)): o trecho nunca leva um pedaço de token.

## O registro {#registro kind=conceito file=brain/memory/leak_audit.py}

Um registro é gravado quando o guarda cortou algo **ou** o detector acusou. Ele fica na coleção `leak_audit` do MongoDB e **expira sozinho** depois de `leak_audit.retention_days` (30), por um índice TTL.

| Campo | O que é |
|---|---|
| `leaked` | A pessoa viu algo suspeito (algum acerto em `delivered`, de qualquer regra). |
| `quarantined` | A pessoa viu um vazamento de uma regra de quarentena: o turno ficou fora da memória. |
| `guard_triggered` | O `LeakGuard` cortou algo nessa chamada. |
| `rules`, `hits` | As regras disparadas e cada acerto (regra, onde, posição, trecho). |
| `raw`, `delivered` | O texto inteiro do modelo antes do guarda e o que foi entregue, com segredos mascarados. |
| `user_message` | A mensagem da pessoa como o modelo a leu (com o carimbo de data), com segredos mascarados. |
| `blocks` | Os blocos de sistema do prompt: **tipo e tamanho**, nunca o conteúdo (`persona`, `profile`, `memories`, `docs`, `channel`). |
| `channel`, `voice` | O cliente do turno (`telegram`, `mobile`, `cli`…) e se era voz. |
| `provider`, `model`, `finish_reason`, `tool_calls` | Quem atendeu **aquela** chamada (depois do failover, se houve; o router guarda isso por conversa, então duas conversas ao mesmo tempo não trocam os nomes), como terminou e quais tools pediu. |
| `version`, `commit` | A versão da Juniper e o commit da imagem (`GIT_COMMIT`, do *build*). |

O registro guarda texto de conversa, por isso vive só no seu MongoDB e expira. Sem MongoDB (`memory.session.store: file`), nada é gravado; só o evento `leak_audit` vai ao [log técnico](observabilidade.md), **sem texto** (id, `leaked`, `quarantined`, regras, provider, modelo, canal). A gravação nunca derruba o turno: uma falha vira um evento `error` com `where: leak_audit`, e um MongoDB lento segura o fim do turno por no máximo 1 s.

## Fora da memória {#quarentena kind=conceito file=brain/core/agent.py}

Quando a pessoa **viu** um vazamento de uma regra de quarentena (`quarantined`), o turno é separado da memória:

- O `Turn` do agente sai com `leaked: true` e a resposta **vazia**. A mensagem da pessoa continua valendo; o que a Juniper disse naquele turno não chega à extração de memórias nem à transcrição que o sonho lê. Basta uma chamada do turno vazar (inclusive o texto falado antes de uma tool).
- A transcrição da [Memória V2](memoria-v2.md) grava o turno com `leaked: true` e `assistant` vazio. O material do sonho põe no lugar da resposta a nota "(a resposta dela foi separada por vazamento de instruções; fica de fora)", como faz com a resposta de um turno com tool, e o [dashboard](dashboard.md) mostra "(a resposta foi separada: vazamento)".
- O texto vazado só existe no registro de auditoria, até expirar.
- O evento `leak_quarantined` marca o turno no log.

Isso vale mesmo com `leak_audit.enabled: false`: desligar a auditoria só deixa de gravar o registro. Uma resposta com acertos só das regras largas (código, rótulos, citação da memória), ou que o guarda limpou antes de chegar à pessoa, segue para a memória normalmente. Turnos transcritos antes desta separação não são reprocessados.

O histórico da conversa em andamento não muda: a resposta entregue continua nele, como a pessoa a viu, e o `Session.scrub` segue limpando só os marcadores conhecidos antes de cada turno. Um vazamento que só a auditoria reconhece ainda volta ao modelo na próxima chamada.

## Marcar na hora: /vazou {#relato kind=comando file=brain/core/leak_report.py}

O detector só acusa o que as regras conhecem. Quando **você** vê um vazamento, marque:

| Onde | Como |
|---|---|
| Telegram | `/vazou` ou `/vazou o que você viu` |
| App (texto) | digite `/vazou` ou `/vazou nota` no chat |
| Voz | diga, sozinho na fala: «isso vazou», «vazou», «marcar vazamento», «registrar vazamento» |

O comando é do **servidor** (`ChatHandler`): vale para qualquer cliente, e o app não precisa de uma versão nova. Ele **não** chega ao modelo, **não** entra no histórico da conversa nem na memória, e funciona mesmo com uma resposta ainda em andamento (muitas vezes é ela o vazamento). Por voz, a frase tem de ser **a fala inteira** (como «encerrar conversa»): uma frase que só menciona vazamento vai ao modelo como qualquer outra. Dito por voz, ela **corta** o que estiver falando, anota e responde em uma frase: «Anotei o vazamento.»

Cada relato guarda, na coleção `leak_reports` do MongoDB:

- as **últimas 10 interações** da conversa (a sua mensagem e a resposta dela, como você viu), mais velhas primeiro. Uma resposta que você cortou falando por cima vem com o que ela tinha dito até ali (ou, se o guarda ainda segurava o começo, o que o modelo tinha escrito) e a marca `interrupted`. Os textos passam por `mask_secrets` e são cortados em 8.000 caracteres;
- a sessão, o canal (`telegram`, `mobile`…), se foi por voz, a sua nota (até 500 caracteres), a versão e o commit;
- os ids dos registros automáticos da **mesma sessão** nas últimas 24 h (até 3), para ligar o que você viu ao texto bruto do modelo.

Diferente dos registros automáticos, o relato **não expira**: foi você quem escolheu guardá-lo. Apague com `juniper vazamentos --apagar <id>`. As interações vêm da memória do processo (até 10 numa conversa em andamento); numa conversa retomada, começam pelo que a sessão salva ainda tem (até 8 turnos). Sem MongoDB, o comando responde que não tem onde guardar.

Os relatos aparecem na tela **Ocorrências** do [dashboard](dashboard.md) e saem em planilha.

## Consultar {#consultar}

O comando lê o MongoDB do processo onde roda. Na VPS, use o atalho do contêiner ([Operação](operacao.md#atalho)).

```bash
juniper vazamentos                 # os registros dos últimos 7 dias
juniper vazamentos --vazados       # só os que chegaram à pessoa
juniper vazamentos --resumo        # contagem por regra, modelo e canal
juniper vazamentos --dias 30 -n 100
juniper vazamentos --ver 3f9a1c2b  # um caso inteiro (o id ou o começo dele)
juniper vazamentos --relatos       # os relatos que você marcou com /vazou
juniper vazamentos --apagar 3f9a1c2b   # apaga um relato
```

Para cruzar com o resto do turno, o `session` do registro filtra o log técnico: `juniper logs -s <sessão>`.

## Configuração {#configuracao}

| Opção | Padrão | O que faz |
|---|---|---|
| `leak_audit.enabled` | `true` | Grava os registros. Desligada, o turno em quarentena continua fora da memória. |
| `leak_audit.retention_days` | `30` | Dias até o registro expirar (de 1 a 365). Mudar o valor ajusta o índice existente. |
| `leak_audit.echo_words` | `10` | Palavras seguidas iguais a um bloco de sistema que contam como `prompt_echo` (de 5 a 50). |

## Testes {#testes}

```bash
pytest tests/test_leak_audit.py -v
```

Cobrem os vazamentos reais (`</parameter>`, `]` seguido de "Mensagem de sistema…", `<conhecimento>`), a fala natural sem acertos, respostas legítimas (código, rótulos, eco da memória) que nunca entram em quarentena, o eco do prompt, o mascaramento de segredos, a separação do turno (inclusive com tool), o sink quebrado ou lento, o provider e o modelo por conversa, o material do sonho, o armazenamento, o TTL e o comando, que imprime o texto sem obedecer a markup nem a caracteres de controle.

## Para saber mais {#para-saber-mais}

- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Memória V2](memoria-v2.md) (Documentação Técnica): A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com o algoritmo de sonho.
- [Voz por áudio](voz-audio.md) (Documentação Técnica): Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do protocolo.
- [Operação e manutenção](operacao.md) (Produção): O dia a dia do servidor: comandos dentro do contêiner, logs, saúde e comandos administrativos.
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
