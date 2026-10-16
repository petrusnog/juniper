---
title: Observabilidade
group: Núcleo
order: 6
description: O log estruturado (JSONL), os eventos, a redação de segredos e a medição de tempo por etapa.
status: pronta
tags: log eventlog timing jsonl show_timing eventos latência jq
---

## Visão geral {#visao-geral}

Tudo que a Juniper faz fica registrado: cada chamada ao LLM, cada tool, cada troca de provider e cada erro. É **uma linha JSON por evento, um arquivo por dia**, com segredos mascarados. Além disso, ela mede onde o tempo de cada resposta vai, para você saber o que está lento (o embedding local, o LLM, o banco) em vez de adivinhar.

## Log estruturado {#log kind=módulo file=brain/eventlog.py}

```text
~/.juniper/logs/2026-10-11.jsonl     # um arquivo por dia, permissão 600
```

```bash
tail -f ~/.juniper/logs/$(date +%F).jsonl | jq .     # acompanhar ao vivo
```

Cada linha tem `ts` (com fuso), `run` (id curto que agrupa os eventos de uma execução do CLI) e `event`, mais os campos do evento.

### Eventos {#eventos}

| Evento | Quando | Campos principais |
|---|---|---|
| `llm_request` | Antes de cada chamada ao LLM | `messages`, `tools` (quantidades) |
| `llm_response` | Quando a resposta termina | `provider`, `latency_ms`, `usage`, `finish_reason`, `tool_calls` (nomes) |
| `tool_call` | Depois de cada tool pedida | `name`, `tier`, `args`, `decision`, `success`, `output`/`error`, `duration_ms` |
| `failover` | Um provider falhou e o próximo assumiu | `provider`, `type`, `message` |
| `error` | Falha do LLM no turno, tool que quebrou ou gravação que falhou | `where`, `type`, `message` |
| `timing` | Fim de cada turno e de cada extração de memória | `scope`, `total_ms`, `stages_ms` |
| `memory_recall` | Memórias injetadas no prompt | ids, tipos e notas (sem o texto) |
| `embedding`, `embedding_model_load` | Embedding local | tempo de inferência por chamada; carga do modelo (uma vez por processo) |

Valores de `decision` em `tool_call`:

| `decision` | Significa |
|---|---|
| `auto` | Tool de leitura, executou direto. |
| `confirmed` | Pediu confirmação e você disse sim. |
| `denied` | Você negou, o tempo esgotou ou não havia como perguntar. |
| `forbidden`, `unknown`, `invalid` | Tool proibida, nome inventado pelo LLM ou argumentos errados. |
| `precheck_failed` | A tool recusou antes de te perguntar (ex.: conflito na agenda). |
| `crashed` | A tool levantou exceção (também gera um `error`). |

### Segredos no log {#segredos}

Nada vai para o disco sem passar por `redact()`:

| O que | Resultado |
|---|---|
| Campos com nome `api_key`, `token`, `secret`, `password`, `authorization`, `private_key` | `***` (contadores como `prompt_tokens` são preservados) |
| Textos com `gsk_…`, `Bearer …` ou blocos de chave privada | Trecho trocado por `***` |
| As chaves de API configuradas, onde quer que apareçam | `***` |
| Textos com mais de 2000 caracteres | Cortados, com a contagem do que sobrou |

O que **você** digita não é gravado (só quantidades), mas a saída das tools é: um `git_search` grava os commits que achou. Por isso o arquivo é criado com permissão `600`.

### Decisões {#decisoes-log}

- O log fica em `brain/eventlog.py`, **fora** de `core/`: router, dispatcher e agente o usam sem depender uns dos outros.
- **Logar nunca derruba a Juniper:** disco cheio ou pasta sem permissão pulam o evento.
- Os testes nunca escrevem no log real (`tests/conftest.py`).
- Não há rotação nem limpeza hoje: apagar os arquivos antigos é manual (em produção isso muda; veja [Produção](producao.html)). O `juniper.log` do shell é outro arquivo, com o texto simples dos comandos legados.

## Tempos por etapa {#tempos kind=conceito file=brain/timing.py}

```bash
JUNIPER_AGENT__SHOW_TIMING=true juniper     # ou show_timing: true no config.yaml
```

Depois de cada resposta aparece uma linha apagada, e no fim do chat uma da extração:

```text
⏱ total 2.4s · memória 210ms · LLM 1.9s · 1ª palavra 780ms · salvar sessão 4ms [memória: embedding 160ms · busca 40ms]
⏱ memória (extração) total 6.1s · LLM extração 2.2s · carga do modelo 1.4s · embedding 90ms · busca 12ms · LLM julgamento 2.1s · gravação 18ms · 2 chamada(s) ao LLM
```

| Etapa | O que é |
|---|---|
| memória | Buscar o perfil e as memórias da sua mensagem: perfil (leitura no banco), contagem, carga do modelo (uma vez por processo), embedding (a pergunta vira vetor, local, na CPU) e busca (ler e comparar os vetores). |
| LLM / 1ª palavra | Tempo total do LLM e até ele começar a responder. |
| ferramentas | Execução das tools (agenda, git, memória...). |
| salvar sessão | Gravar a conversa. |
| extração | LLM de extração (uma chamada), LLM de julgamento (uma por memória nova parecida com alguma guardada), carga do modelo, embedding, busca e gravação. |

Etapas abaixo de 1 ms não aparecem. Tudo vai também para o log, mesmo com `show_timing` desligado:

```bash
# cada resposta e cada extração
jq -c 'select(.event=="timing") | {scope, total_ms, stages_ms}' ~/.juniper/logs/*.jsonl
# o embedding local: tempo de inferência por chamada e a carga do modelo
jq -c 'select(.event=="embedding" or .event=="embedding_model_load")' ~/.juniper/logs/*.jsonl
```

### Como ler o resultado {#ler-tempos}

São hipóteses a partir do código; o seu uso diz qual vale.

| O que aparece | Quer dizer |
|---|---|
| Carga do modelo alta, só uma vez | O custo de abrir o modelo de embeddings na primeira vez do processo; acontece uma vez por chat. |
| Embedding alto em toda mensagem (centenas de ms) | É a CPU local. Teste `memory.embeddings.threads`: menos threads aliviam a máquina. |
| LLM julgamento e LLM extração altos | É rede e provider, não a máquina local. Cada memória nova parecida com uma antiga custa uma chamada a mais. |
| Busca alta | Ler os vetores do MongoDB; cresce com o número de memórias. |
| 1ª palavra alta | O provider demora para começar (ou houve failover). |

Os tempos são de relógio (wall-clock): com a máquina ocupada, qualquer etapa pesa mais. Compare várias respostas, não uma.

## Testes {#testes}

```bash
pytest tests/test_eventlog.py tests/test_timing.py -v
```

Cobrem o mascaramento (chaves, valores, chave configurada, contadores de token), o arquivo por dia com permissão `600`, o log que nunca levanta erro, os eventos do agente, cada `decision` do dispatcher, erro de tool, erro de LLM e o failover do router.
