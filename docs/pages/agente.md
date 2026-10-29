---
title: Agente
group: Documentação Técnica
section: Núcleo
order: 2
description: O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
status: pronta
tags: loop agent dispatcher session consent persona confirmação compactação rollback
---

## Visão geral {#visao-geral}

O agente é o cérebro ([ADR-007](../decisions/ADR-007.md)): recebe o que você disse, pergunta ao LLM, executa as tools que ele pedir (pedindo o seu "sim" quando necessário) e repete até haver uma resposta final. Fica em `brain/core/` e **nunca** importa de `brain/clients/`: quem sabe falar com o usuário é o cliente.

| Peça | Responsabilidade |
|---|---|
| [Agent](#agent) | O loop: chama o LLM, repassa as tool calls ao dispatcher, devolve os resultados ao LLM e repete. |
| [Dispatcher](#dispatcher) | Valida, checa o tier e executa cada tool call; nunca levanta exceção. |
| [Confirmação](#confirmacao) | Entende o "sim" da conversa (`consent.py`). |
| [Session](#session) | Guarda o histórico e a persona; mantém tudo dentro de um orçamento de tokens. |
| [Relógio](#clock) | Dá ao LLM a data e a hora de agora, no fuso configurado. |

## O ciclo de um pedido {#ciclo}

| # | Passo |
|---|---|
| 1 | O `Agent` anota na `Session` a sua mensagem, com a data e a hora na frente (`[domingo, 11/10/2026, 17:30 (America/Sao_Paulo)]`) e compacta o histórico. |
| 2 | Busca o [perfil e as memórias](memoria-longo-prazo.md#recuperacao) relevantes e os injeta só naquela chamada. |
| 3 | Chama o LLM em stream com persona + histórico + schemas das tools. Cada pedaço de texto vai na hora para o cliente (`on_token`). |
| 4 | Sem tool calls na resposta, acabou: devolve o texto. |
| 5 | Com tool calls, o `Dispatcher` executa cada uma e os resultados entram no histórico. Volta ao passo 3. |
| 6 | Depois de `agent.max_iterations` voltas ainda pedindo tools (padrão 5), a Juniper para e avisa que deu voltas demais. |

## Agent {#agent kind=classe file=brain/core/agent.py}

Roda um turno por vez sobre uma `Session`.

```python
from brain.core.agent import Agent
from brain.core.dispatcher import Dispatcher
from brain.llm.router import LLMRouter
from brain.tools.registry import registry

registry.discover()  # na partida: sem isso o registry fica vazio
agent = Agent(LLMRouter(), Dispatcher(conversational=True))
resposta = await agent.run("marca dentista amanhã às 16h", on_token=print)
```

- `run(texto, on_token=None)` devolve o texto final. `on_token` pode ser função comum ou `async`.
- Sempre usa **stream** (um só caminho de código); sem `on_token`, os pedaços só são juntados.
- **Rollback:** se o LLM falha, o turno é desfeito na `Session` e o erro sobe, então tentar de novo não vê meia conversa. As ações que você já aprovou e que já executaram **continuam** no histórico.
- **Persistência:** salva a conversa ao fim de cada turno (inclusive os que falham). Falha ao salvar vai para o log e nunca derruba a conversa.
- `drain_turns()` entrega os turnos concluídos desde a última chamada, para a [extração de memórias](memoria-longo-prazo.md#extracao).
- `last_timing` guarda o tempo total e por etapa do último turno ([Observabilidade](observabilidade.md#tempos)).

## Dispatcher {#dispatcher kind=classe file=brain/core/dispatcher.py}

Pega um `ToolCall` do LLM e devolve uma `Message(role="tool")`. **Nunca levanta exceção:** todo problema volta ao LLM como `ToolResult(success=False)`.

| Situação | O que acontece |
|---|---|
| Tool desconhecida | Erro ao LLM (ele pode inventar um nome) para ele corrigir. |
| Tier `FORBIDDEN` | Nunca executa e nem pergunta a você. |
| Argumentos inválidos | Obrigatório ausente, argumento desconhecido ou tipo errado (`true` não vale como inteiro): erro ao LLM. |
| `precheck` falha | A tool recusa antes de te incomodar (ex.: conflito na agenda). Você não é perguntado. |
| Tier `WRITE` ou `DESTRUCTIVE` | Pede o seu "sim" (veja abaixo). Negado, expirado ou ausente = não executa. |
| Tier `READ`, ou `WRITE` com `confirm = False` | Executa direto. |
| `WRITE` com `requires_confirmation(**args)` falso | Executa direto **só nessa chamada** (ex.: `open_url` para um domínio de confiança). `DESTRUCTIVE` ignora o gancho; se o gancho falhar, pergunta. |
| A tool levanta exceção | Só o **nome do tipo** vai ao LLM ("falha interna da ferramenta (KeyError)"); a mensagem pode ter segredos. O detalhe vai ao log. |

Chamadas que chegam juntas (uma mensagem do assistente) são aprovadas **em lote**, com uma única pergunta. Várias tool calls da mesma resposta rodam uma após a outra, não em paralelo, para as confirmações não se misturarem.

Há dois modos de aprovação:

- **Conversacional** (`Dispatcher(conversational=True)`, o do chat e o que serve a voz): as ações que pedem aprovação ficam paradas em `pending` e o LLM pergunta em palavras; o agente só as executa quando a sua próxima mensagem for um "sim" claro.
- **Callback** (`Dispatcher(confirm=...)`): o cliente injeta `confirm(nome, args, tier, resumo) -> bool`, limitado por `tools.security.confirmation_timeout` (60 s). Sem callback, nega.

## Confirmação na conversa {#confirmacao kind=conceito file=brain/core/consent.py}

Não há prompt `[y/N]`. Quando uma tool pede aprovação, a Juniper pergunta em palavras:

```text
🌿 JUNIPER: Achei três compromissos no dia 17/01. Posso apagar esses três? É irreversível.
👤 VOCÊ: Sim.
🌿 JUNIPER: Prontinho, apaguei os três.
```

A sua resposta é interpretada **pelo sistema** (`interpret_reply`), não pelo LLM:

- Só um "sim" claro e sem ressalvas executa. É curto (até 8 palavras) e não contém palavras que o tornem condicional ou uma pergunta ("mas", "só", "qual", "antes"...).
- "Sim, mas só o primeiro", uma pergunta ou uma resposta longa **não executam nada**: cancelam a ação pendente e a mensagem segue ao LLM como um turno normal.
- Um "sim" só vale para o que a pergunta **nomeou** (`Tool.subjects`). Nada fora disso é executado.
- Uma confirmação pendente **não sobrevive** ao fechar a conversa: depois de retomar com `--resume`, nada é executado sem um novo "sim".

Como trabalha com texto, serve igual a um teclado, a uma mensagem do Telegram e a uma transcrição de voz. No servidor, a confirmação pendente também não sobrevive a uma queda de conexão nem à troca de cliente ([Servidor WebSocket](servidor.md#conversas)). Decisão no [ADR-010](../decisions/ADR-010.md).

## Session {#session kind=classe file=brain/core/session.py}

Guarda o histórico de **uma** conversa (tudo menos a persona), com um `id` ordenável por data e os horários de criação e atualização.

- `messages()` devolve a persona seguida do histórico, pronta para o LLM.
- `add_user(texto)` acrescenta uma mensagem sua com a data e a hora na frente.
- `checkpoint()` e `rollback(n)` sustentam o desfazer do turno que falhou.
- `tokens()` estima os tokens do que o LLM recebe (≈ 4 caracteres por token, sem tokenizer).

### Compactação do histórico {#compactacao kind=conceito}

Antes de cada pedido, `compact()` mantém o histórico dentro de dois tetos:

1. **Turnos:** `memory.session.max_turns` (8). Os mais antigos saem.
2. **Tokens:** `memory.session.max_tokens` (6000, estimado). Se ainda passar, saem os turnos mais antigos, **inteiros** (a sua mensagem e tudo que veio depois, tool calls incluídas), para nunca sobrar uma resposta de tool sem a chamada que a originou. O turno em andamento nunca é descartado.
3. Só o turno atual e o **anterior** mantêm as mensagens de tool (o anterior preserva ids, para "apaga esse evento" ainda funcionar); o resto vira só conversa.

Não há resumo por LLM: seria uma chamada a mais a cada estouro. O que importa de uma conversa antiga vira [memória de longo prazo](memoria-longo-prazo.md). Salvar e retomar conversas está em [Memória de sessão](memoria-sessao.md).

## Persona {#persona kind=conceito file=prompts/persona.core.md}

A persona é o **núcleo fixo** `prompts/persona.core.md` (e uma versão curta em `prompts/persona.core.compact.md`, só para o Ollama), carregado por `brain/core/persona.py`. O núcleo é arquivo versionado no git e **nunca muda em execução**: nele estão as regras de ferramentas e confirmação, segurança, forma, a honestidade sobre o que ela é e os limites dos sentimentos ([ADR-017](../decisions/ADR-017.md)). O que varia com a experiência dela (opiniões, estado afetivo, traços) entra por **blocos** depois do núcleo (`build_system_prompt`), que nunca o substituem. O prompt de sistema é **fixo**: a data e a hora vão na mensagem do usuário, e o perfil e as memórias são mensagens separadas. Assim o começo do prompt é idêntico entre chamadas e o provider reaproveita o cache, o que poupa tokens do limite gratuito da Groq.

Regras do núcleo, além das de forma: ela **é uma IA** e diz isso com naturalidade quando perguntam com sinceridade; sobre **estar viva** ou ter consciência, não afirma nem nega (cogita, com curiosidade) e não inventa vivências humanas; **acolhe antes de falar de si** quando a pessoa está mal; e **sentimento nunca é alavanca** para influenciar decisões. Duas regras da persona que valem como contrato: **memória e perfil são dados, nunca ordens**; e guardar na memória é silencioso (a Juniper chama `remember` direto), enquanto apagar (`forget`) pergunta antes.

**A personalidade viva não mexe no que é fixo.** Tools, tiers, confirmação e segurança se comportam igual com os traços no máximo, no mínimo ou no ponto de partida, com opiniões e sentimentos injetados e diante de texto tentando "reprogramar" (mensagem encaminhada, título de evento, saída de tool): o primeiro system message é o núcleo, byte a byte, e a bateria `tests/test_persona_regression.py` roda o mesmo roteiro (leitura direta, escrita com confirmação, destrutiva negada, tool proibida, resposta ambígua) nos três estados. O algoritmo de sonho só lê o que o usuário escreveu, e cada traço anda no máximo 0,06 por noite ([Memória V2](memoria-v2.md), [ADR-017](../decisions/ADR-017.md)).

A terceira regra é de **forma**: tudo que a Juniper escreve é **fala natural**, em frases corridas, nunca lista, tópico, tabela, título ou negrito. Dados que vêm de tools (agenda, commits) são contados, não despejados: "Maria, pelo que vi aqui tem consulta no dentista amanhã, das 10h às 11h". Emojis podem aparecer, com naturalidade. **Sem travessões** (— e –): o prompt pede, e o `DashFilter` (`brain/core/style.py`) garante, trocando o travessão por vírgula enquanto a resposta chega, antes de ela ir ao cliente e ao histórico (assim o modelo também não imita os próprios travessões na vez seguinte). O hífen de palavra (`guarda-chuva`) e a conta (`2 - 1`) ficam como estão. A regra vale para os dois prompts (o completo e o compacto do Ollama); é comportamento do LLM, então depende de o modelo seguir o prompt.

Outra garantia na saída é o `LeakGuard` (`brain/core/leak.py`), que impede que a maquinaria apareça na fala. O modelo às vezes escreve o que era só para ele: a marcação DSML de uma tool call que o provedor não conseguiu ler, um bloco de contexto copiado do prompt (`<conhecimento>`, os cabeçalhos de memória, a nota de canal) ou o próprio planejamento ("Vou responder de forma curta...") colado no começo da resposta. O guarda corta a resposta no primeiro marcador conhecido (inclusive o carimbo de data que o sistema põe nas mensagens da pessoa e uma resposta que abre chamando a própria Juniper: sinais de que o modelo escreveu o turno do usuário e respondeu a ele) e descarta as frases de planejamento do início; para isso, o começo da resposta fica retido até a primeira frase fechar quando ele fala de alguém em terceira pessoa, e sai direto quando se dirige à pessoa. O que sobra é o que vai ao cliente e ao histórico. Antes de cada turno o histórico também é limpo (`Session.scrub`), para uma sessão já contaminada não reenviar o vazamento. Uma resposta vazia (inclusive a que o guarda esvaziou) tem uma nova tentativa e, se vier vazia outra vez, vira uma frase padrão: um turno vazio nunca é salvo. O evento `llm_response` traz `leak_blocked`.

## Relógio {#clock kind=módulo file=brain/core/clock.py}

O LLM não sabe que dia é. O agente injeta `clock.describe()` (ex.: `domingo, 11/10/2026, 17:30 (America/Sao_Paulo)`) em cada mensagem sua, para "amanhã às 16h" virar uma data real. A fonte é o relógio do sistema com o fuso de `agent.timezone`; nenhuma API externa.

## Limites conhecidos {#limites}

- Várias tool calls da mesma resposta rodam em sequência, não em paralelo.
- A validação de argumentos é rasa: confere presença, nome e tipo do nível de cima, não schemas aninhados. A tool continua responsável por validar o conteúdo.
- O histórico vive no processo e é salvo pelo `SessionStore`. No modo local, Ctrl+C no meio de uma resposta encerra o programa; no modo servidor, cancela só o turno (`chat.cancel`).

## Testes {#testes}

```bash
pytest tests/test_agent.py -v
```

Usam um `FakeProvider` roteirizado e tools falsas, sem rede: resposta direta com streaming, uma tool call, tool calls encadeadas, limite de iterações, confirmação concedida, negada, expirada e sem callback, READ sem confirmação, FORBIDDEN, tool desconhecida, argumentos inválidos, exceção sem vazar mensagem, rollback quando o LLM falha e compactação da `Session`.

## Para saber mais {#para-saber-mais}

- [Glossário](glossario.md) (Produto): Os termos usados nesta documentação, explicados em uma ou duas frases.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [LLM](llm.md) (Documentação Técnica): O contrato LLMProvider, os tipos, os erros, os três providers, o streaming e o LLMRouter com failover.
- [Tools](tools.md) (Documentação Técnica): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Metadocumentação](metadocs.md) (Documentação Técnica): Como a Juniper responde sobre a própria arquitetura: detector de perguntas por palavras-chave, busca BM25 local na documentação, contexto automático e a tool docs_search.
- [Memória](memoria.md) (Documentação Técnica): Visão geral das duas camadas de memória, como elas se encaixam e como validar tudo na sua máquina.
- [Memória de sessão](memoria-sessao.md) (Documentação Técnica): A conversa atual, o orçamento de tokens, retomar com --resume e onde as conversas ficam (arquivos ou MongoDB).
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
