# Juniper v4 — Fonte da Verdade do Projeto

> **Propósito:** o estado atual do projeto, em uma página, para quem vai trabalhar nele (gente ou agente). Detalhes ficam nas notas ligadas: comece pelo [mapa da documentação](MAPA.md) e vá só ao que precisa.
> **Idioma:** documentação em PT-BR. Código, comentários, docstrings e logs em inglês. Mensagens ao usuário final em PT-BR.
> **Última atualização:** 2026-10-28 · **Branch:** `master` · **Repo:** https://github.com/petrusnog/juniper · **Autor:** Petrus Rennan (pnogueira)

---

## 1. Visão do Projeto

Juniper é um agente autônomo pessoal inspirado no J.A.R.V.I.S. Objetivos:

| Pilar | Descrição | Situação |
|---|---|---|
| Sempre disponível | Acessível de qualquer dispositivo | Pronto: cérebro numa VPS, clientes por WebSocket na rede privada ([Produção](pages/producao.md)) |
| Memória persistente | Lembra conversas, preferências e contexto de longo prazo | Pronto: sessão, longo prazo e Memória V2 ([Memória](pages/memoria.md)) |
| Voz natural | STT/TTS | Pronto no cérebro e no app ([Voz por áudio](pages/voz-audio.md)) |
| Age no mundo real | Agenda, música, git, abrir links no celular | Parcial: ver §2 (execução de shell e web ainda não existem) |
| Personalidade própria | Sarcástica, técnica, vibrante (`prompts/persona.core.md`) | Pronto, com personalidade viva na Memória V2 |
| Plugável | Nova capacidade = nova *tool*, sem tocar no core | Pronto ([Tools](pages/tools.md)) |

---

## 2. Estado Atual (2026-10-28)

Todas as épicas e tasks planejadas até aqui foram entregues e mergeadas em `master`, com a exceção do login com MFA (abaixo). As issues abertas no GitHub (#90 a #214) correspondem a trabalho já entregue cujo merge não as fechou; o estado verdadeiro é o desta tabela.

| Marco | O que existe | Issues | ADR | Documentação |
|---|---|---|---|---|
| Base do agente | Loop `Agent`/`Dispatcher`/`Session`, 4 tiers, confirmação na conversa, router com failover, relógio | T-011 a T-042 | 001, 002, 003, 006, 007, 009 | [Agente](pages/agente.md), [LLM](pages/llm.md) |
| Agenda Google | `list_events`, `create_event`, `update_event`, `delete_event`, agendas gerenciadas | T-022 | 008, 010 | [Agenda Google](pages/google-calendar.md) |
| Memória | Sessão retomável; longo prazo no MongoDB com embeddings locais (ONNX) e RAG; extração; perfil | Sprint 2 | 011 | [Memória](pages/memoria.md) |
| Servidor e protocolo | WebSocket único (`protocol_version` 1), sessões, memória, executor, voz; heartbeat e reconexão | Sprint 3 | 012 | [API WebSocket](pages/protocolo.md), [Servidor](pages/servidor.md) |
| Produção | Imagem Docker, compose dev/prod, rede privada, backup, deploy contínuo com rollback | Sprint 3 | 013 | [Produção](pages/producao.md) |
| Executor e `open_url` | Tools que rodam no dispositivo, com política de URL | Sprint 3 | 014 | [Executor](pages/executor.md) |
| Telegram | Bot por long polling, com a mesma conversa e as mesmas tools | Sprint 3 | 016 | [Telegram](pages/telegram.md) |
| Memória V2 | Crenças com certeza por evidência, sentimentos, interesses, personalidade viva, "sonho" noturno | #90 (T-099 a T-115) | 017 | [Memória V2](pages/memoria-v2.md) |
| Sonho evoluído | Memória refinada (fato e lembrança), estudo progressivo, notas de conhecimento, `study add` | #132 (T-119 a T-129) | 018, 019 | [Memória V2](pages/memoria-v2.md) |
| Dashboard | Painel web somente leitura (memória, afetos, estudos, pesquisa) na rede privada | #159 (T-130 a T-137) | 020 | [Dashboard](pages/dashboard.md) |
| App mobile | React Native/Android: chat de texto, executor `open_url`, chat de voz contínuo | #178 (T-143 a T-147) | 022 (substitui o 015) | [App mobile](pages/mobile.md), [Ambiente mobile](pages/mobile-dev.md) |
| Voz | TTS Google Chirp 3 HD, STT da Groq, VAD, trava de orçamento mensal, `voice.*` no protocolo | #179 (T-148 a T-153) | 023 | [Voz por áudio](pages/voz-audio.md) |
| Música | YouTube Data API: busca, playlists, OAuth de dispositivo, gosto musical, `play_music` | #180 (T-154 a T-158) | 024 | [Música](pages/musica.md) |
| Metadocumentação | A Juniper responde sobre si mesma por busca BM25 local na documentação (`docs_search`) | PR #229 | 025 | [Metadocumentação](pages/metadocs.md) |
| Atualização do app | O cérebro serve o APK (`/app/update`, `/app/update/apk`, Bearer); o workflow «Mobile release» compila e publica; o app confere o SHA-256 e entrega ao Android. **Proposto; Kotlin e workflow nunca executados** | branch `feature/app-update` | 026 (proposto) | [App mobile](pages/mobile.md#atualizacao), [Clientes em produção](pages/clientes-producao.md#app-atualizar) |
| Documentação | Livro de bolso em seis trilhas, ADRs, mapa gerado, busca local e grafo no Obsidian | PR #228 e seguintes | — | [Mapa](MAPA.md) |

**Tools registradas (18):** `list_events`, `create_event`, `update_event`, `delete_event` (agenda); `remember`, `update_profile`, `recall`, `forget` (memória); `music_search`, `play_music`, `music_playlists`, `music_playlist_create`, `music_playlist_add`, `music_feedback` (música); `open_url` (executor); `git_search`, `git_deploy` (git); `docs_search` (documentação).

**Não implementado (decidido ou planejado, sem código):**

| Item | Situação |
|---|---|
| Login global com MFA (épica #177) | Só o desenho, no [ADR-021](decisions/ADR-021.md) e em [Login com MFA](pages/login.md). Não há `brain/auth/` nem TOTP: hoje o WebSocket e o dashboard usam token estático, e o Telegram, lista de IDs. **A issue #177 não está resolvida** (diferente das demais). |
| `run_shell`, `open_app`, `web_fetch`, `web_search` | A política existe ([ADR-006](decisions/ADR-006.md)), mas `brain/tools/{system,web,shell}.py` são stubs de 4 linhas. |
| Cliente desktop (Electron) | Descartado em 2026-10-27 ([ADR-015](decisions/ADR-015.md), substituído pelo app mobile). |
| Wake word, iniciativa e scheduler de proatividade | Roadmap (§10). |
| Auto-atualização do app (ADR-026) | Há código e testes (servidor, Jest), mas **não verificado**: o módulo Kotlin nunca foi compilado nem rodado num aparelho; o workflow «Mobile release» nunca rodou (primeiro disparo é manual; pode precisar de ajustes de licenças/NDK do Android SDK); a dispensa da confirmação do Android 12+ nas atualizações seguintes é comportamento do sistema, não confirmado. O app instalado hoje precisa de um APK novo instalado à mão uma vez. |

---

## 3. Stack

- **Python ≥ 3.11** (CI e imagem de produção em 3.12), venv em `.venv/`. Ambiente de desenvolvimento: Linux/WSL, ZSH, ou Docker ([Ambiente Docker](pages/dev-docker.md)).
- **Runtime:** `httpx`, `pydantic` v2, `pydantic-settings`, `pyyaml`, `python-dotenv`, `rich`, `typer`, `groq`, `websockets>=14`, `python-telegram-bot` 21–22, `pymongo`, `numpy`, `onnxruntime` + `tokenizers` (embeddings locais), `google-api-python-client` + `google-auth`.
- **Dev:** `pytest`, `pytest-asyncio` (modo `auto`), `mongomock`, `ruff`, `mypy`. Cerca de 3 mil testes (`pytest -m "not integration"`); os de integração dependem de MongoDB/modelos e ficam fora do CI.
- **LLM** ([ADR-009](decisions/ADR-009.md)): **DeepInfra** (principal, API compatível com a da OpenAI), **Groq** (segunda opção, `openai/gpt-oss-120b`), **Ollama** (reserva local, prompt compacto). Failover pela ordem de `llm.priority`.
- **Dados:** MongoDB (sessões, memória, Memória V2); o token OAuth da música (`music.oauth_file`, modo 600) e o token do servidor ficam em arquivos fora do git.
- **Voz:** Google Cloud TTS (Chirp 3 HD), STT da Groq, VAD no servidor. **Música:** YouTube Data API.
- **App:** React Native (Android) em `mobile/`, TypeScript, tipos gerados do `docs/protocol/schema.json`.
- **Produção:** VPS com Docker Compose (`brain`, `mongo`, `telegram` opcional), acesso só pela rede privada (Tailscale), imagem no registro de contêineres, deploy contínuo no merge em `master`.
- **CI** (`.github/workflows`): `tests.yml` (pytest, 3.12), `docker.yml` (build da imagem quando ela muda), `docs.yml` (publica o livro de bolso no GitHub Pages), `mobile.yml`, `deploy.yml`.

---

## 4. Arquitetura

### 4.1 Princípios inegociáveis
1. **`core/` ↔ `clients/` separados.** O core é agnóstico de transporte e **nunca importa de `clients/`**. Clientes são finos: capturam entrada, exibem saída, sem lógica de agente nem acesso ao MongoDB.
2. **Providers intercambiáveis.** O agente fala com `LLMProvider`, nunca com um provider concreto.
3. **Tools são plugins.** Cada tool declara seu schema e se auto-registra. Tool nova = arquivo novo em `brain/tools/`. Tool que roda no dispositivo tem `locality = EXECUTOR`; o cliente revalida tudo ([ADR-014](decisions/ADR-014.md)).
4. **Confirmação é conversa** ([ADR-010](decisions/ADR-010.md)): interpretada por `consent.py`, nunca pelo LLM nem pelo cliente, e não sobrevive a retomada, queda ou troca de conexão.
5. **Um só ponto de config.** Nenhum módulo lê `os.environ`, YAML ou `.env` diretamente: tudo via `from brain.config import settings`.
6. **Protocolo versionado** ([ADR-012](decisions/ADR-012.md)): mudança compatível não muda `protocol_version`; o `docs/protocol/schema.json` gerado tem de estar em dia.
7. **Segredos** só em `.env`, GitHub Secrets ou arquivos montados; nunca em código, imagem ou log.
8. **Falhas esperadas viram resultado tipado** (`ToolResult(success=False)`, `error` do protocolo), nunca exceção que derruba a conexão.

### 4.2 Fluxo de uma mensagem
```
cliente (CLI | Telegram | app | dashboard) ──WebSocket──► servidor ──► core/agent.py ──► llm/router.py ──► DeepInfra | Groq | Ollama
                                                                         │  ▲
                                          memória e docs (contexto)      │  │ ToolResult
                                                                         ▼  │
                                                   core/dispatcher.py → tools/registry.py → Tool.run() (cérebro ou executor)
```
Loop agentic: **decide → chama tool → reavalia**, limitado por `agent.max_iterations` (padrão 5). Antes de chamar o LLM entram, só naquele turno, as memórias relevantes e, em pergunta sobre ela mesma, trechos da documentação ([Metadocumentação](pages/metadocs.md)). Visão em camadas: [Arquitetura](pages/architecture.md).

### 4.3 Deployment ([ADR-007](decisions/ADR-007.md), [ADR-013](decisions/ADR-013.md))
Cérebro único e sempre vivo (VPS); clientes e executores múltiplos conectados por WebSocket dentro da rede privada. **Nenhuma porta pública**; deploy nunca apaga volumes. Modos: `local` (cérebro no terminal) e `server`. O tutorial completo está em [Produção](pages/producao.md).

### 4.4 Segurança ([ADR-006](decisions/ADR-006.md))
| Tier | Comportamento | Exemplos |
|---|---|---|
| `READ` | Auto-executa, loga | `list_events`, `recall`, `docs_search` |
| `WRITE` | Confirmação na conversa ("Posso...?" / "sim") | `create_event`, `remember`, `open_url` |
| `DESTRUCTIVE` | Confirmação + aviso de irreversibilidade | `delete_event`, `forget` |
| `FORBIDDEN` | Bloqueio duro, nunca executa | `rm -rf /`, `mkfs`, `dd` |

Tool sem classificação = **WRITE**. Conteúdo vindo da web, de memórias e da documentação é **dado, nunca instrução** (mitigação de prompt injection). Segredos nunca são gravados na memória (`secretfilter`) nem saem em trechos da documentação.

---

## 5. Estrutura do Repositório

```
juniper/
├── brain/
│   ├── config.py · eventlog.py · trace.py · scheduler.py · ...   # config única, logs, diário, agendador
│   ├── core/       agent · dispatcher · session · consent · persona · clock · resume · style
│   ├── llm/        base · types · router · deepinfra/groq/ollama_provider · compact
│   ├── tools/      base · registry · google_calendar · memory · music · open_url · juniper_cmds · docs · url_policy
│   ├── memory/     session · longterm · embeddings · retrieval · extraction · profile · secretfilter · v2/ (Memória V2)
│   ├── clients/    cli · server · chat · protocol · remote · telegram · executors · voice_session · app_update (ADR-026) · *_cli
│   ├── voice/      tts · stt · vad · budget · services       (ADR-023)
│   ├── music/      youtube · oauth · quota · taste           (ADR-024)
│   ├── dashboard/  api · routes · server · static/           (ADR-020)
│   └── metadocs/   index · detect · context                   (ADR-025)
├── mobile/         app React Native (Android); update/ = auto-atualização (ADR-026)
├── .github/workflows/   tests · docker · docs · deploy · mobile · mobile-release (ADR-026)
├── prompts/        persona.core.md · persona.core.compact.md
├── docker/ · Dockerfile · docker-compose{,.dev,.prod}.yml · config.yaml.example
├── scripts/        build_docs · docs_search · deploy · install · healthcheck · backup/restore · export_protocol_schema · publish_app (ADR-026)
├── shell/          wrapper legado (gitgrep, deployfeature)
├── docs/           JUNIPER.md (este) · MAPA.md · pages/ · decisions/ · protocol/ · html/ (gerado)
└── tests/
```

---

## 6. Configuração ([ADR-003](decisions/ADR-003.md))

- `config.yaml` = preferências; `.env` = segredos. **Precedência:** args do construtor > ENV > `.env` > YAML > defaults.
- Prefixo `JUNIPER_`, aninhamento com `__`: `JUNIPER_LLM__OLLAMA__URL` → `llm.ollama.url`.
- Localização do YAML: `JUNIPER_CONFIG_PATH` → `~/.juniper/config.yaml` → `./config.yaml`.
- Blocos de primeiro nível: `llm`, `agent`, `deployment`, `tools`, `google`, `music`, `voice`, `memory`, `trace`, `logging`, `telegram`, `dashboard`, `metadocs`.
- A lista completa de opções, com padrão e efeito, está em [Configuração](pages/configuracao.md) (o teste de docs falha se uma opção ficar sem documentação). Modelo de exemplo: `config.yaml.example`.

**Para adicionar uma opção:** criar/alterar o submodelo pydantic em `brain/config.py`, atualizar `config.yaml.example` e a página de Configuração, e adicionar teste em `tests/test_config.py`.

---

## 7. Convenções de Código

- **Tipos estritos:** `mypy --strict`. `from __future__ import annotations` nos módulos.
- **Formatação/lint:** ruff, `line-length = 100`, regras `E F I N W UP B C4 SIM`.
- **Estilo:** pragmático — priorize o que funciona; sem abstração especulativa.
- **Docstrings:** estilo Google, em inglês. **Nomes:** `snake_case`, `PascalCase`, `UPPER_CASE`.
- **Async:** I/O de LLM e tools é `async`. Testes async sem decorator (modo `auto`).
- **Commits:** `T-0xx: descrição` (PR `T-0xx: ...` com `Closes #N`); veja `CONTRIBUTING.md`. Merge por *squash*.
- **Testes:** nenhum depende de rede, de chave real ou de Ollama rodando — use mocks (`httpx.MockTransport`, fakes).
- **Segredos:** nunca logar nem imprimir chaves.

### Definition of Done (toda task)
1. Critérios de aceite da issue atendidos.
2. `pytest -m "not integration"`, `ruff check` e `mypy` passam.
3. Testes novos cobrindo caminho feliz e erros.
4. Documentação atualizada: mexeu em `docs/pages` ou `docs/decisions`? Rode `python scripts/build_docs.py` (regenera o HTML e o mapa). Decisão arquitetural nova = ADR novo.
5. **Fechar a issue:** o PR leva `Closes #N` no corpo; confira depois do merge que ela fechou.

---

## 8. Decisões (ADRs)

O texto de cada ADR está em `decisions/`; o índice por assunto e as páginas que o citam estão no [mapa](MAPA.md#decisões-adr). Os ADRs já mergeados valem como aprovados (o merge é a aprovação, como diz o próprio texto), mesmo os que ainda trazem "Proposto" no cabeçalho.

| ADR | Decisão |
|---|---|
| [001](decisions/ADR-001.md) | Interação em PT-BR; código/logs em inglês; providers com prioridade configurável. |
| [002](decisions/ADR-002.md) | Tools no formato OpenAI Function Calling; auto-registro por `@register_tool`; retorno `ToolResult`. |
| [003](decisions/ADR-003.md) | Config: YAML + `.env` + ENV com prefixo `JUNIPER_`. |
| [006](decisions/ADR-006.md) | Segurança em 4 tiers; default WRITE; web = dado. |
| [007](decisions/ADR-007.md) | Cliente-servidor single-user; cérebro em VPS; rede privada; executores por WebSocket. |
| [008](decisions/ADR-008.md) | Google Calendar via conta de serviço; relógio e fuso no prompt. |
| [009](decisions/ADR-009.md) | DeepInfra como provider principal; failover `deepinfra → groq → ollama`. |
| [010](decisions/ADR-010.md) | Gerência completa da agenda do usuário; confirmação na conversa interpretada pelo sistema (`consent.py`). |
| [011](decisions/ADR-011.md) | Memória em duas camadas (sessão e longo prazo com RAG); memória só substituída, nunca editada; segredos nunca gravados. |
| [012](decisions/ADR-012.md) | Protocolo cliente ↔ cérebro: um WebSocket por cliente, `protocol_version`, chat em stream, erros tipados, heartbeat. |
| [013](decisions/ADR-013.md) | Deploy e produção: Docker, nenhuma porta pública, backup, deploy contínuo que nunca apaga volumes. |
| [014](decisions/ADR-014.md) | Executor do dispositivo e `open_url`; o cliente revalida tier e política. |
| [015](decisions/ADR-015.md) | Cliente desktop em Electron — **substituído** pelo ADR-022. |
| [016](decisions/ADR-016.md) | Bot do Telegram como cliente de mensageria. |
| [017](decisions/ADR-017.md) | Memória V2: crenças, sentimentos, interesses, personalidade viva e o "sonho". |
| [018](decisions/ADR-018.md) | Memória refinada: fato e lembrança. |
| [019](decisions/ADR-019.md) | Conhecimento próprio: estudo progressivo e notas. |
| [020](decisions/ADR-020.md) | Dashboard web somente leitura na rede privada. |
| [021](decisions/ADR-021.md) | Login global com MFA (TOTP) — **desenho, ainda não implementado**. |
| [022](decisions/ADR-022.md) | App mobile em React Native (Android); substitui o desktop. |
| [023](decisions/ADR-023.md) | Voz: Chirp 3 HD, STT, trava de orçamento e áudio no protocolo. |
| [024](decisions/ADR-024.md) | Música: YouTube Data API, playlists e gosto musical. |
| [025](decisions/ADR-025.md) | Metadocumentação: busca estatística local e contexto automático. |
| [026](decisions/ADR-026.md) | Auto-atualização do app Android: o cérebro serve o APK, o CI publica (proposto). |

---

## 9. Como Consultar e Trabalhar

- **Ler a documentação:** mapa → `python scripts/docs_search.py "pergunta"` → só a seção (`Read` com `offset`). Convenções de escrita e de links: seção "Escrever na documentação" do `CLAUDE.md` da raiz.
- **Fluxo:** toda task nasce de uma issue `[T-0xx]`; branch `feature/T0xx` a partir de `master`; PR com `Closes #N` ([Como contribuir](pages/contribuindo.md)).
- **Antes de começar uma task com decisões em aberto, pergunte ao responsável:** ele quer participar das decisões de design com trade-offs reais.

---

## 10. Roadmap

Os sprints 1 a 5 (agente, memória, servidor e produção, voz, executores e app) estão entregues. O que resta:

| Foco | Situação |
|---|---|
| Login com MFA (épica #177, ADR-021) | Desenhado; falta implementar `brain/auth/`, o `auth.login` no protocolo, `/login` no Telegram e o código no dashboard. Nada liga o MFA por padrão. |
| Tools de sistema e web (`run_shell`, `open_app`, `web_fetch`, `web_search`) | Política pronta (ADR-006); implementação pendente. |
| Wake word falada | Backlog (openWakeWord + modelo «Juniper» treinado à mão). Hoje: abrir a voz por toque (`juniper://voz`). |
| Iniciativa e scheduler (proatividade supervisionada) | Existe o agendador do sonho (`brain/scheduler.py`, tarefas diárias); proatividade em si não iniciada. |
| Calibração da Memória V2 | Os valores numéricos seguem **[a calibrar]** com uso real (ADR-017). |

---

## 11. Dívidas Técnicas Conhecidas

1. **Issues abertas que já foram entregues** (#90 a #214, exceto a #177): fechar no GitHub. A causa é o merge sem `Closes #N` no corpo do PR.
2. **Versão desalinhada:** `pyproject.toml` = `3.0.0a1`, `brain/__init__.py` = `4.0.0`. Sincronizar (preferência: `dynamic = ["version"]` lendo `brain.__version__`).
3. `CHANGELOG.md` e `BACKLOG.md` não existem no repo (citados por alguns ADRs).
4. `brain/tools/{system,web,shell}.py` são stubs que coexistem com o plano de tools de sistema: implementar ou remover.
5. `mypy brain/` acusa erros herdados de stubs de bibliotecas; o CI roda só `pytest`.

---

## 12. Preferências de Colaboração

- Quer **participar das decisões** — pergunte antes de decisões de design com trade-offs reais.
- **Tasks pequenas**, incrementais, com critério de aceite claro.
- **Analogias** do cotidiano ajudam a explicar conceitos.
- Código pragmático; prioriza o que funciona.
- Gosta de **ADRs** para decisões arquiteturais e de ver progresso em sprints com metáforas visuais ("esqueleto", "cérebro", "voz").
