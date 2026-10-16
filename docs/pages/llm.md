---
title: LLM
group: Núcleo
order: 2
description: O contrato LLMProvider, os tipos, os erros, os três providers, o streaming e o LLMRouter com failover.
status: pronta
tags: provider deepinfra groq ollama router failover streaming erros tipos mensagem sse
---

## Visão geral {#visao-geral}

O agente só conhece o **contrato** `LLMProvider`. Quem fala com DeepInfra, Groq ou Ollama é um provider, que traduz entre o vocabulário da Juniper e o do modelo. Trocar de modelo, ou adicionar um provider novo, não exige mexer no agente.

```text
Agent ──► LLMRouter ──► DeepInfraProvider  (nuvem, principal)
                  ├──► GroqProvider        (nuvem, segunda opção)
                  └──► OllamaProvider      (local, reserva)
```

Código em `brain/llm/`. A ordem do failover vem de `llm.priority` (padrão: `deepinfra → groq → ollama`).

## Vocabulário {#vocabulario}

Os tipos de `brain/llm/types.py` são todos **imutáveis**.

### Message {#message kind=classe file=brain/llm/types.py}

Uma "carta" da conversa.

| Campo | O que é |
|---|---|
| `role` | `system`, `user`, `assistant` ou `tool` |
| `content` | O texto (pode ser vazio) |
| `tool_calls` | Pedidos de ferramenta (só em `assistant`) |
| `tool_call_id` | A qual pedido uma resposta `tool` responde |
| `name` | Nome da tool (em `tool`) |

`to_openai()` converte para o formato OpenAI function calling (ADR-002). O ciclo de uma tool: o `assistant` pede (`tool_calls`), a mensagem `tool` devolve o resultado com o mesmo `tool_call_id`, e o ciclo se repete até o modelo responder só com texto.

### ToolCall {#toolcall kind=classe file=brain/llm/types.py}

O modelo pedindo uma ferramenta: `id`, `name` e `arguments` (um `dict` já pronto, **não** texto JSON).

### LLMResponse {#llmresponse kind=classe file=brain/llm/types.py}

A resposta completa: `message` (a `Message` do assistente), `model`, `provider`, `usage` (opcional) e `finish_reason`.

### StreamChunk {#streamchunk kind=classe file=brain/llm/types.py}

Um pedaço da resposta no modo streaming: `delta` (o texto novo), e, só no último chunk, `tool_calls` completas, `usage` e `finish_reason`.

### TokenUsage {#tokenusage kind=classe file=brain/llm/types.py}

O consumo: `prompt_tokens`, `completion_tokens` e `total_tokens`, todos opcionais (nem todo provider informa).

## Erros {#erros}

O tipo do erro diz ao router o que fazer. Todos herdam de `LLMError` (`brain/llm/errors.py`).

### LLMError {#llmerror kind=classe file=brain/llm/errors.py}

Mãe de todos: capturar este pega qualquer falha de LLM.

| Erro | Quando | Failover |
|---|---|---|
| `LLMTimeoutError` | Demorou demais para responder. | sim |
| `LLMRateLimitError` | Limite de uso estourado (HTTP 429). | sim |
| `LLMUnavailableError` | Fora do ar: conexão recusada, 5xx, stream cortado. | sim |
| `LLMAuthError` | Chave ausente, inválida ou sem crédito (401/402/403). O provider está inutilizável. | sim |
| `LLMError` (outros) | Pedido inválido, modelo inexistente (404), resposta malformada. Falharia igual no outro provider. | **não** |

As mensagens de erro são montadas pela Juniper, **nunca** copiando o texto cru do SDK ou da API: assim a chave de API não vaza nem em mensagem nem em log.

## LLMProvider {#llmprovider kind=classe file=brain/llm/base.py}

O contrato. Um provider que não implementa os três métodos nem consegue ser criado.

| Método | O que faz |
|---|---|
| `chat(messages, *, tools, temperature, max_tokens)` | Manda as mensagens e espera a resposta inteira (`LLMResponse`). |
| `stream(messages, ...)` | Devolve a resposta aos pedaços (`AsyncIterator[StreamChunk]`). |
| `health_check()` | Pergunta "você está vivo?" (`True` ou `False`, nunca levanta). |

`tools` usa o schema OpenAI (ADR-002). `compact_prompt` (atributo de classe) pede ao router uma persona e descrições de tool mais curtas, usado só pelo Ollama.

## Providers {#providers}

### DeepInfraProvider {#deepinfraprovider kind=classe file=brain/llm/deepinfra_provider.py}

O LLM principal na nuvem (ADR-009). API compatível com a da OpenAI via `httpx`, sem SDK.

| Item | Valor |
|---|---|
| Endpoint | `POST https://api.deepinfra.com/v1/openai/chat/completions` |
| Autenticação | `Authorization: Bearer <chave>` (`JUNIPER_DEEPINFRA_API_KEY`) |
| Streaming | SSE: linhas `data: {...}` até `data: [DONE]` |
| Tools | `tools` + `tool_choice: "auto"`; argumentos voltam como string JSON e viram `dict` |
| Saúde | `GET /models` (falso se não há chave) |
| Modelo padrão | `deepseek-ai/DeepSeek-V4-Flash-0731` (precisa suportar tool calling) |

Mapeamento HTTP: 401/402/403 → `LLMAuthError`; 429 → `LLMRateLimitError`; 5xx, conexão e stream cortado → `LLMUnavailableError`; timeout → `LLMTimeoutError`; demais 4xx → `LLMError` (sem failover). Chave ausente falha na **primeira chamada** (não no construtor), para um `.env` sem a chave não derrubar a partida: o router segue para a Groq.

### GroqProvider {#groqprovider kind=classe file=brain/llm/groq_provider.py}

Segunda opção, na nuvem, pelo SDK oficial `groq`. A Groq fala o formato OpenAI, então a ida é direta (`Message.to_openai()`); o trabalho está em ler a volta e traduzir os erros do SDK.

- `max_retries=0` no SDK: retry e failover são do router.
- `arguments` da tool chega como string JSON; JSON quebrado, ou que não seja objeto, é `LLMError`; vazio vira `{}`.
- `llm.groq.reasoning_effort: low` (só modelos gpt-oss) reduz os tokens de raciocínio, que são cobrados como resposta.
- O limite do plano gratuito (HTTP 429) é o motivo do `LLMRateLimitError` existir.

### OllamaProvider {#ollamaprovider kind=classe file=brain/llm/ollama_provider.py}

Reserva local, via `POST /api/chat` (`httpx`, `stream:false` no `chat`, NDJSON no `stream`). Tem conversão própria (não usa `to_openai()`), porque o formato não é igual:

| Campo | OpenAI | Ollama |
|---|---|---|
| `content` vazio | `null` | `""` |
| `arguments` da tool | string JSON | `dict` |
| id da chamada | sempre presente | ausente: a Juniper gera `call_<12 hex>` |
| resposta de tool | `tool_call_id` | `tool_name` |
| `max_tokens` | `max_tokens` | `options.num_predict` |

Limitações: em CPU, o Ollama lê ~30 tokens de prompt por segundo, então recebe o **prompt compacto** ([abaixo](#prompt-compacto)); o contexto padrão é de 4096 tokens; e o 8B segue persona e tool calling pior que os modelos da nuvem, por isso é o último da fila.

## Streaming {#streaming kind=conceito}

`stream()` entrega a resposta em pedaços, à medida que o modelo escreve. Cada pedaço de texto sai num `StreamChunk` com `delta`; o **último** chunk traz o que só se sabe no fim (`tool_calls`, `usage`, `finish_reason`).

- **Regra de ouro:** juntar todos os `delta` dá o mesmo texto que `chat()` devolveria.
- **Tool calls só no chunk final**, completas e juntas, mesmo que o provider as envie antes ou em fragmentos: uma ferramenta pela metade não serve ao agente. Na Groq e na DeepInfra elas chegam fragmentadas por `index` (o `id` e o `name` no primeiro fragmento, os `arguments` em pedaços) e são montadas no fim; só então o JSON é interpretado.
- **Stream cortado** (sem `done`/`finish_reason`) vira `LLMUnavailableError`: entregar uma resposta pela metade como completa seria pior que um erro.
- Os erros de `stream()` aparecem **durante a iteração**, não na chamada.
- Não há retry de stream: retomar no meio duplicaria texto.

## LLMRouter {#llmrouter kind=classe file=brain/llm/router.py}

Um provider "de mentira" que esconde vários de verdade. Implementa a mesma interface (`chat`, `stream`, `health_check`); o agente fala só com ele.

- Cada chamada começa pelo primeiro provider de `llm.priority`. Se ele responde, os outros nem são tocados.
- **Troca de provider** em `LLMUnavailableError`, `LLMTimeoutError`, `LLMRateLimitError` **e** `LLMAuthError`. Outros `LLMError` são relançados (esconder um 404 de modelo atrás do Ollama dificultaria o diagnóstico).
- **No stream, só troca antes do primeiro chunk.** Depois de já ter entregue texto, relança o erro: reenviar a pergunta repetiria o começo da resposta.
- Se **todos** falham, sai um `LLMError` com a causa de cada provider.
- `last_provider` diz quem atendeu a última chamada; `providers` lista (somente leitura); `health_check()` consulta todos em paralelo e vale `True` se **algum** estiver saudável; `aclose()` fecha todos.
- `on_failover(falhou, erro, próximo)` é um gancho para o cliente avisar o usuário ("A DeepInfra recusou as credenciais. Vou tentar a Groq.") em vez de ficar em silêncio. Toda troca também vai para o [log](observabilidade.html) (evento `failover`).

```python
router = LLMRouter()                  # lê llm.priority
async for c in router.stream(msgs):
    print(c.delta, end="")
print(router.last_provider)           # "deepinfra", "groq" ou "ollama"
await router.aclose()
```

### Prompt compacto {#prompt-compacto kind=conceito file=brain/llm/compact.py}

Um Ollama em CPU levaria mais de um minuto para ler o prompt completo (persona + todas as tools, ~2.200 tokens) antes do primeiro token. Para providers com `compact_prompt = True`, o router envia uma persona curta (`prompts/juniper.compact.md`) e schemas de tool enxutos (descrições de até 100 caracteres, parâmetros até 60). Nomes, tipos, `required` e `enum` não mudam, então o tool calling continua funcionando. DeepInfra e Groq recebem o prompt completo.

## Configuração {#configuracao}

```yaml
llm:
  priority: [deepinfra, groq, ollama]   # só estes três; a ordem é a do failover
  deepinfra:
    model: deepseek-ai/DeepSeek-V4-Flash-0731
    timeout: 30
  groq:
    model: openai/gpt-oss-120b
    timeout: 30
    reasoning_effort: low
  ollama:
    url: http://localhost:11434
    model: llama3.1
    timeout: 300
```

Segredos no `.env`: `JUNIPER_DEEPINFRA_API_KEY` e `JUNIPER_GROQ_API_KEY`. Sem a chave da DeepInfra a Juniper não quebra: o provider falha com "chave ausente" e o router começa pela Groq. Se você já tinha um `config.yaml`, a linha `priority` dele vence o padrão do código. Referência completa em [Configuração](configuracao.html#llm).

```bash
juniper -p deepinfra "oi"    # força só a DeepInfra, sem failover
```

## Limitações conhecidas {#limitacoes}

- Sem controle de custo: nenhum limite de tokens ou gasto é imposto; acompanhe o painel do provider.
- O formato exato dos erros 4xx da DeepInfra não é documentado; o provider tenta `error`, `detail` e `message`.
- O stream não pode ser cancelado de forma explícita; interromper a iteração fecha a conexão.

## Testes {#testes}

```bash
pytest tests/test_llm_base.py tests/test_ollama_provider.py tests/test_groq_provider.py \
       tests/test_deepinfra_provider.py tests/test_llm_router.py tests/test_llm_compact.py -v
```

Sem rede nem chave: `httpx.MockTransport` para Ollama e DeepInfra, um cliente falso do SDK (com as exceções reais) para a Groq e providers falsos para o router (1º responde; 1º falha e o 2º responde; todos falham; falha no meio do stream não duplica saída).
