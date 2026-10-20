---
title: Configuração
group: Núcleo
order: 5
description: Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
status: pronta
tags: config yaml env variáveis ambiente settings pydantic JUNIPER_ opções
---

## Visão geral {#visao-geral}

A configuração tem **um só ponto de acesso**: `from brain.config import settings` (`brain/config.py`, pydantic-settings). Nenhum outro módulo lê variável de ambiente, YAML ou `.env` diretamente (ADR-003).

- **`config.yaml`** guarda preferências (modelos, limites, agendas). Copie de `config.yaml.example`.
- **`.env`** guarda **segredos** (chaves de API, usuário e senha do banco). Nunca é versionado e fica com permissão `600`.

## Precedência {#precedencia}

Do que vence para o que perde:

1. Argumentos do construtor (`Settings(...)`)
2. Variáveis de ambiente (`JUNIPER_*`)
3. `.env` (`~/.juniper/.env`, depois `./.env`)
4. `config.yaml`
5. Padrões do código

O `config.yaml` é procurado em: `JUNIPER_CONFIG_PATH` → `~/.juniper/config.yaml` → `./config.yaml`. Se você já tinha um `config.yaml`, o valor dele vence o padrão do código (útil lembrar ao atualizar `llm.priority`).

## Variáveis de ambiente {#variaveis}

Prefixo `JUNIPER_`, aninhamento com `__`: `JUNIPER_LLM__OLLAMA__URL` vale `llm.ollama.url`.

| Variável | O que é |
|---|---|
| `JUNIPER_DEEPINFRA_API_KEY` | Chave da DeepInfra (deepinfra.com/dash/api_keys). |
| `JUNIPER_TELEGRAM_BOT_TOKEN` | Token do bot do Telegram (o `@BotFather` entrega). Só o bot usa; é um segredo, nunca vai para código, imagem, log nem PR ([Telegram](telegram.html)). |
| `JUNIPER_GROQ_API_KEY` | Chave da Groq (console.groq.com/keys). |
| `JUNIPER_MEMORY__MONGO__USERNAME` | Usuário do MongoDB (lido também pelo `docker-compose.yml`). |
| `JUNIPER_MEMORY__MONGO__PASSWORD` | Senha do MongoDB. |
| `JUNIPER_CONFIG_PATH` | Caminho do `config.yaml`. |
| `JUNIPER_AGENT__SHOW_TIMING` | Atalho de `agent.show_timing`. |

`settings.paths.{home,logs,config}` são calculados, não configuráveis.

## llm {#llm}

| Opção | Padrão | O que faz |
|---|---|---|
| `llm.priority` | `[deepinfra, groq, ollama]` | Ordem do failover; só esses três são válidos. |
| `llm.deepinfra.model` | `deepseek-ai/DeepSeek-V4-Flash-0731` | Modelo da DeepInfra (precisa suportar tool calling). |
| `llm.deepinfra.timeout` | `30` | Segundos (1 a 600). |
| `llm.groq.model` | `openai/gpt-oss-20b` (o `config.yaml.example` usa `openai/gpt-oss-120b`) | Modelo da Groq. |
| `llm.groq.timeout` | `30` | Segundos. |
| `llm.groq.reasoning_effort` | vazio | `low`, `medium` ou `high` (só gpt-oss); `low` economiza tokens de raciocínio. |
| `llm.ollama.url` | `http://localhost:11434` | Endereço do Ollama. |
| `llm.ollama.model` | `llama3.1` | Modelo local. |
| `llm.ollama.timeout` | `120` (o exemplo usa `300`) | Segundos; em CPU o primeiro pedido carrega o modelo. |

## agent {#agent}

| Opção | Padrão | O que faz |
|---|---|---|
| `agent.max_iterations` | `5` | Voltas máximas do loop de tools por pedido (1 a 50). |
| `agent.language` | `pt-BR` | Idioma da interação. |
| `agent.timezone` | `America/Sao_Paulo` | Fuso usado para "amanhã às 16h"; validado ao carregar. |
| `agent.show_timing` | `false` | Mostra no terminal o tempo de cada etapa ([Observabilidade](observabilidade.html#tempos)). |

## deployment {#deployment}

| Opção | Padrão | O que faz |
|---|---|---|
| `deployment.mode` | `local` | `local` (o cérebro roda dentro do CLI) ou `server` (cérebro remoto; [Produção](producao.html)). |
| `deployment.server.host` | `127.0.0.1` | Onde o servidor escuta. |
| `deployment.server.port` | `8765` | Porta. |
| `deployment.auth.token_file` | `~/.juniper/token` | Arquivo do token estático dos clientes. |

## trace {#trace}

| Opção | Padrão | O que faz |
|---|---|---|
| `trace.enabled` | `false` | Liga o diário das mensagens (`message.log`): cada interação contada em texto corrido, camada por camada ([Observabilidade](observabilidade.html#message-log)). Guarda o texto das conversas, por isso vem desligado. No `config.yaml` pessoal, liga o CLI e o contêiner de desenvolvimento juntos. |
| `trace.file` | `logs/message.log` | Onde escrever o diário. |

## telegram {#telegram-config}

O bot do Telegram ([Telegram](telegram.html), [ADR-016](adr-016.html)). O token fica em `JUNIPER_TELEGRAM_BOT_TOKEN`, não aqui.

| Opção | Padrão | O que faz |
|---|---|---|
| `telegram.rate_limit_per_minute` | `20` | Mensagens por minuto por usuário (de 1 a 600). Acima disso o bot avisa **uma vez** por janela e **não chama o cérebro**: um erro de loop não vira conta de LLM. |
| `telegram.delete_after_hours` | `0` | Apagar a mensagem do usuário e a resposta do bot depois de N horas (de 0 a 48). `0` desliga. O Telegram só deixa o bot apagar mensagens com menos de 48 h, então o prazo efetivo é limitado a 47,5 h. O agendamento fica em memória: se o bot reiniciar, o que estava marcado **não** é apagado. |
| `telegram.allowed_user_ids` | `[]` | IDs **numéricos** do Telegram de quem pode falar com o bot. **Lista vazia = ninguém** (nunca "todos"). Pelo ambiente: `JUNIPER_TELEGRAM__ALLOWED_USER_IDS=[123456789]`. |

## tools {#tools}

| Opção | Padrão | O que faz |
|---|---|---|
| `tools.security.shell_allowlist` | `git, ls, cat, grep, rg, fd, jq, find, wc, head, tail` | Binários que a futura `run_shell` aceita. |
| `tools.security.shell_require_confirmation` | `true` | Exige confirmação para a `run_shell`. |
| `tools.security.confirmation_timeout` | `60` | Segundos que o modo de confirmação por callback espera (1 a 600). |

## google {#google}

Veja a página [Agenda Google](google-calendar.html).

| Opção | Padrão | O que faz |
|---|---|---|
| `google.credentials_file` | `~/.juniper/certs/gcp-key.json` | Chave da conta de serviço. |
| `google.calendar_id` | vazio | Agenda "Juniper" (sempre gerenciável). |
| `google.read_calendar_ids` | `[]` | Agendas só lidas (entram na checagem de conflitos). |
| `google.manage_calendar_ids` | `[]` | Agendas suas em que ela também cria, altera e apaga. |
| `google.default_calendar_id` | vazio | Onde caem os eventos novos. Vazio = a primeira de `manage_calendar_ids`; sem nenhuma, a agenda Juniper. |
| `google.default_duration_minutes` | `60` | Duração quando você não diz (5 a 1440). |
| `google.reminder_minutes` | `30` | Lembrete de cada evento criado. |

## memory {#memory}

Veja [Memória](memoria.html).

| Opção | Padrão | O que faz |
|---|---|---|
| `memory.session.max_turns` | `8` | Turnos seus mantidos no histórico. |
| `memory.session.max_tokens` | `6000` | Orçamento (estimado) do histórico; os turnos mais antigos saem primeiro. |
| `memory.session.dir` | `~/.juniper/sessions` | Pasta das conversas em arquivos. |
| `memory.session.store` | `file` | `file` (JSON) ou `mongo`. |
| `memory.mongo.uri` | `mongodb://localhost:27017` | Sem credenciais (usuário e senha vêm do `.env`). |
| `memory.mongo.database` | `juniper` | Nome do banco. |
| `memory.mongo.username` | vazio | Usuário do banco: **só no `.env`** (`JUNIPER_MEMORY__MONGO__USERNAME`). |
| `memory.mongo.password` | vazio | Senha do banco: **só no `.env`**; entregue ao driver à parte, sem ir na URI. |
| `memory.mongo.timeout_ms` | `3000` | Quanto esperar pelo servidor antes de avisar que caiu. |
| `memory.embeddings.model` | `intfloat/multilingual-e5-small` | Modelo local de embeddings; trocar exige `juniper memory reindex`. |
| `memory.embeddings.dir` | `~/.juniper/models` | Onde o modelo baixado fica. |
| `memory.embeddings.max_length` | `512` | Tokens por texto; o resto é cortado. |
| `memory.embeddings.batch_size` | `16` | Textos por lote. |
| `memory.embeddings.threads` | `0` | Threads de CPU do onnxruntime; `0` = automático. |
| `memory.embeddings.download_timeout` | `300` | Segundos por arquivo do modelo. |
| `memory.retrieval.top_k` | `5` | Máximo de memórias por busca. |
| `memory.retrieval.threshold` | `0.80` | Similaridade mínima (cosseno, -1 a 1). |
| `memory.retrieval.max_tokens` | `500` | Orçamento das memórias injetadas por mensagem. |
| `memory.extraction.enabled` | `true` | Liga a memória de longo prazo (só com `store: mongo`). |
| `memory.extraction.mode` | `turn` | `turn` (segundo plano a cada resposta) ou `exit` (só ao encerrar). |
| `memory.extraction.max_per_run` | `5` | Memórias novas, no máximo, por conversa. |
| `memory.extraction.max_chars` | `300` | Tamanho máximo de cada memória. |
| `memory.extraction.max_turns` | `20` | Turnos recentes enviados ao LLM de extração. |
| `memory.extraction.neighbors` | `3` | Memórias parecidas comparadas antes de gravar. |
| `memory.extraction.neighbor_threshold` | `0.75` | Similaridade mínima para contar como "parecida". |
| `memory.extraction.save_feelings` | `true` | Guarda sentimentos e acontecimentos pessoais (sensível). |
| `memory.profile.enabled` | `true` | Perfil do usuário em toda mensagem. |
| `memory.profile.max_chars` | `500` | Teto do parágrafo do perfil. |

## Como adicionar uma opção {#adicionar}

1. Crie ou altere o submodelo pydantic em `brain/config.py`.
2. Atualize o `config.yaml.example`.
3. Adicione o teste em `tests/test_config.py` e a opção nesta página (um teste confere que toda opção das settings aparece aqui).
