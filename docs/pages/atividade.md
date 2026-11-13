---
title: Registro de atividade e Auditoria
group: Documentação Técnica
section: Núcleo
order: 10
description: O que a Juniper fez, em frases pesquisáveis (data em UTC-3, termo, modelo, tipo de ação, desfecho), como cada texto é traduzível por catálogos .po e como usar a tela Auditoria, `juniper atividade` e a planilha.
status: pronta
tags: atividade auditoria log amigável busca utc-3 i18n gettext po pot idiomas traduzir activity audit msgid
---

## Visão geral {#visao-geral}

O [log técnico](observabilidade.md) diz o que o código fez. O **registro de atividade** diz o que a Juniper fez por você, numa frase: «Pesquisei «Evidências» no YouTube Music», «Abri o link https://…», «Quis usar delete_event em «Dentista», mas deixei pendente até você confirmar». Cada registro guarda também o evento técnico original, na aba **Técnico**, e fica no MongoDB (coleção `activity`) por `activity.retention_days` (90 dias). Os logs legados não mudam ([ADR-035](../decisions/ADR-035.md)).

Sem MongoDB (`memory.session.store: file`) não há registro de atividade; o log técnico segue como sempre.

## O registro {#registro kind=modelo file=brain/activity/model.py}

| Grupo | Campos |
|---|---|
| Quando | `ts` (UTC, indexado) e `ts_local` (ISO com o deslocamento, por exemplo `2026-11-13T14:32:08.412-03:00`) |
| Onde | `session`, `turn` (liga os passos de uma mesma mensagem), `run`, `channel`, `voice` |
| Quem e o quê | `actor` (`user`, `juniper`, `scheduler`, `system`), `category` (música, web, memória, llm…), `action` (`music.play_music`), `severity` |
| Desfecho | `outcome`: `ok`, `failed`, `pending` (esperando você confirmar), `denied` (você negou) ou `partial` |
| Técnico | `provider`, `model`, `tokens`, `duration_ms`, `tool`, `tier`, `decision`, `target`, `error_type`, `version`, `commit`, e `raw` (o evento do log, já mascarado) |
| Texto | `msg_key`, `msg_params`, `locale`, `summary` |

**Horários:** toda tela, exportação e comando mostra `13/11/2026 14:32:08 (UTC-3)`. O fuso vem de `agent.timezone`. Filtros por data também são lidos nele: «13/11» vai das 00:00 às 23:59 de Brasília, não do UTC.

## De evento a frase {#catalogo kind=módulo file=brain/activity/catalog.py}

`EventLog.log` escreve a linha de sempre e, depois, entrega o evento ao `recorder` (`brain/activity/recorder.py`), que o traduz (`catalog.translate`), monta o registro e o enfileira; uma thread grava no MongoDB, para o driver bloqueante nunca parar a conversa. Uma tool sem frase própria vira «Usei a ferramenta X»; um evento desconhecido, «Registrei o evento X»; tempos, embeddings e HTTP cru não entram. Para dar frase a uma tool nova, acrescente-a em `TOOLS` (`catalog.py`) e rode `python scripts/i18n.py update`.

Um teste confere que o JSONL com o recorder ligado tem exatamente os mesmos campos de sempre.

## Idiomas {#idiomas kind=módulo file=brain/i18n/__init__.py}

O registro guarda a **chave e os parâmetros** (`msg_key`: o texto-fonte em inglês; `msg_params`) além do `summary` já escrito em `activity.locale`. A tela e a planilha escrevem a frase de novo no idioma pedido (`?lang=en`, seletor da tela, `--idioma`). Sem catálogo para o idioma, sai o inglês; um parâmetro que falta fica visível como `{nome}`, nunca quebra.

```text
locale/juniper.pot                          # modelo, gerado do código
locale/pt_BR/LC_MESSAGES/juniper.po         # a tradução
```

```bash
python scripts/i18n.py extract   # reescreve o .pot a partir das chamadas N_() em brain/
python scripts/i18n.py update    # acrescenta os textos novos (vazios) em cada .po
python scripts/i18n.py check     # falha se o .pot está velho ou falta/erra uma tradução
```

Para um idioma novo, copie o `.pot` para `locale/<idioma>/LC_MESSAGES/juniper.po`, ajuste o cabeçalho (`Language`, `Plural-Forms`) e traduza; qualquer ferramenta de tradução serve. O plural segue a regra do cabeçalho: `msgstr[0]`, `msgstr[1]`…, escolhidos pelo parâmetro `count`. O `.po` é lido direto, sem compilar.

## A tela Auditoria {#tela}

No [dashboard](dashboard.md): **Auditoria** (`#/auditoria`).

- **Busca e período:** caixa de texto (casa o texto amigável e o técnico, sem diferença de maiúsculas ou acento; todas as palavras precisam aparecer), atalhos (última hora, hoje, ontem, 7 e 30 dias, tudo) e um intervalo livre com data e hora em UTC-3.
- **Filtros em chips**, combináveis, com a contagem de cada valor dentro do filtro atual: tipo de ação, desfecho, modelo, provedor, canal, severidade, quem. As ações mais comuns também são chips. Tudo fica no endereço (`#/auditoria?outcome=failed&q=youtube`): dá para guardar ou mandar o link.
- **Resumo do período:** registros, falhas, pendentes, negadas e trocas de provedor; gráfico por hora (ou por dia, em períodos longos) com as falhas em vermelho (clicar numa barra filtra aquele intervalo); ranking dos modelos (chamadas, tempo médio, tokens).
- **Linha do tempo:** hora (UTC-3), a frase, o desfecho, o modelo e o canal. Os passos de uma mesma mensagem ficam numa linha que se abre («Mensagem das 14:32: 6 passos, 1 com falha»).
- **Detalhe de um registro:** todos os campos, o evento técnico (com botão de copiar) e os outros passos da mensagem; atalhos para ver tudo da mensagem, tudo da sessão e a conversa.
- **Planilha (.xlsx):** o resultado filtrado, mais antigo primeiro, até 5.000 registros, com a frase e o log técnico lado a lado; o texto nunca vira fórmula e passa pelo mesmo mascaramento do resto do dashboard.

## No terminal {#cli}

```bash
juniper atividade                                  # hoje
juniper atividade -b "youtube" --periodo 7d        # por termo
juniper atividade --modelo llama --desfecho failed # por modelo e desfecho
juniper atividade --de 2026-11-13 --ate 2026-11-13T18:00 --tipo music
juniper atividade --ver 3f9a2c10                   # um registro, com o log técnico
```

Lê o MongoDB do processo onde roda (na VPS, dentro do contêiner do brain). `--idioma en` mostra as frases em inglês.

## API {#api kind=módulo file=brain/dashboard/activity.py}

Somente leitura, com o login do dashboard: `GET /api/activity` (filtros `q`, `period`, `from`, `to`, `category`, `action`, `outcome`, `model`, `provider`, `channel`, `severity`, `actor`, `session`, `turn`, `tool`, `lang`, `limit`, `offset`; devolve itens, total e facetas), `/api/activity/summary`, `/api/activity/<id>` (com `raw` e os passos da mensagem) e `/api/activity/export.xlsx`. Data sem hora em `to` inclui o dia inteiro; data inválida é 400.

## Configuração {#configuracao}

`activity.enabled` (padrão `true`), `activity.retention_days` (90) e `activity.locale` (`pt_BR`): [Configuração](configuracao.md#activity-config).

## Limitações {#limitacoes}

- O modelo de um registro vem do router da conversa em curso; eventos que nascem fora de uma conversa (agendador, login) não têm modelo.
- A busca por termo varre o período filtrado; o resumo lê no máximo os 20 mil registros mais novos.
- Só o português tem catálogo; outros idiomas caem no inglês até alguém traduzir.
- O registro vai para a fila e é gravado por uma thread: ao desligar o servidor, o que ainda estava na fila tem alguns segundos para sair.

## Testes {#testes}

```bash
pytest tests/test_activity.py tests/test_dashboard_activity.py tests/test_dashboard_frontend.py -v
python scripts/i18n.py check
```

Cobrem a leitura do `.po` (plural, texto fuzzy, linhas longas), o catálogo de frases (ferramenta que deu certo, ficou pendente, foi negada, falhou, desconhecida), o horário em UTC-3, o mascaramento de segredos, o JSONL legado intacto, a busca por termo (acentos, caracteres de regex), data, modelo, desfecho e canal, as facetas, o resumo sem buracos no eixo, a API, a planilha, o `juniper atividade` e a tela.

## Para saber mais {#para-saber-mais}

- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
- [Auditoria de vazamentos](vazamentos.md) (Documentação Técnica): Como a Juniper registra as respostas em que escapou maquinaria, o que cada registro guarda e como consultar com `juniper vazamentos`.
- [CLI](cli.md) (Documentação Técnica): Os comandos do `juniper`, o modo remoto e os erros em português.
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
- [ADR-035](../decisions/ADR-035.md) (Decisões): Por que o registro fica no MongoDB, por que o texto é chave mais parâmetros e por que o `.po` é lido direto.
