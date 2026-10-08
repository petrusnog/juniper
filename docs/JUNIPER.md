# Juniper v4 — Documentação de Referência

> **Propósito:** fonte única de verdade para codificar as tasks da Juniper. Antes de implementar qualquer task, leia as seções 1–6 (contexto e regras) e a task correspondente na seção 9.
> **Idioma:** documentação em PT-BR. Código, comentários, docstrings e logs em inglês. Mensagens ao usuário final em PT-BR.
> **Última atualização:** 2026-10-07 · **Branch:** `v4` · **Repo:** https://github.com/petrusnog/juniper · **Autor:** Petrus Rennan (pnogueira)

---

## 1. Visão do Projeto

Juniper é um agente autônomo pessoal inspirado no J.A.R.V.I.S. Objetivos:

| Pilar | Descrição |
|---|---|
| Sempre disponível | Acessível de qualquer dispositivo (PC, celular, futuro IoT) |
| Memória persistente | Lembra conversas, preferências e contexto de longo prazo |
| Voz natural | STT/TTS |
| Age no mundo real | Abre apps, envia mensagens, controla casa, executa tarefas |
| Personalidade própria | Sarcástica, técnica, vibrante (persona em `prompts/juniper.md`) |
| Plugável | Novas capacidades = novas *tools*, sem tocar no core |

---

## 2. Estado Atual

### Pronto
| Componente | Status |
|---|---|
| Estrutura de pastas (`brain/`, `shell/`, `docs/`, `scripts/`, `tests/`) | ✅ |
| `pyproject.toml`, `requirements.txt`, `.gitignore` | ✅ |
| `scripts/dev.sh` (setup idempotente: venv, symlink global, PATH) | ✅ |
| `scripts/uninstall.sh` (`--yes`, `--purge-repo`) | ✅ |
| `brain/config.py` (pydantic-settings) + `tests/test_config.py` (8 testes) | ✅ |
| `brain/clients/cli.py` (placeholder typer) | ✅ funcional |
| Binário `juniper` em `~/.local/bin/` | ✅ |
| ADRs 001, 002, 003, 006, 007 | ✅ |

### Stubs (pendentes)
| Componente | Task |
|---|---|
| `brain/llm/{types,errors,base}.py` | T-011 ← **próxima** |
| `brain/llm/ollama_provider.py` | T-012 |
| `brain/llm/groq_provider.py` | T-013 |
| Streaming | T-014 |
| `brain/llm/router.py` | T-015 |
| `brain/tools/base.py` | T-020 |
| `brain/tools/registry.py` | T-021 |
| `brain/tools/system.py` (`open_app`, `run_shell`) | T-022, T-023 |
| `brain/tools/web.py` (`web_fetch`, `web_search`) | T-024, T-025 |
| `brain/tools/juniper_cmds.py` (bridge gitgrep/deploy) | T-026 |
| `brain/core/agent.py`, `dispatcher.py`, `session.py` | T-030 |
| Logging estruturado `.jsonl` | T-031 |
| `brain/clients/cli.py` completo | T-040 |
| Subcomandos legados | T-041 |
| `juniper tools` | T-042 |

> **Atenção (divergências repo × plano):** o repo hoje contém `brain/tools/shell.py` (stub) e **não** contém `juniper_cmds.py`, `BACKLOG.md`, `CHANGELOG.md` nem `.env.example`; os ADRs estão nomeados `ADR-00X.md` (sem slug). Ao tocar nesses pontos, reconcilie (criar o arquivo faltante ou ajustar a referência) e registre no commit.

---

## 3. Stack

- **Python ≥ 3.11** (testado em 3.12.3), venv em `.venv/`. Ambiente: Linux/WSL, ZSH.
- **Runtime:** `httpx>=0.27`, `pydantic>=2.8`, `pydantic-settings>=2.4`, `pyyaml>=6.0`, `python-dotenv>=1.0`, `rich>=13.7`, `typer>=0.12`, `groq>=0.9`
- **Dev:** `pytest>=8`, `pytest-asyncio>=0.23` (modo `auto`), `ruff>=0.5`, `mypy>=1.10`
- **LLM:**
  - **Groq** (primário): `llama-3.3-70b-versatile`, tier gratuito, tool calling confiável.
  - **Ollama** (fallback): `llama3.1`, local (GPU RX 590 8GB), usado quando Groq falha/offline.
- **Hospedagem futura:** VPS (~US$5/mês) rodando o brain; clientes remotos via Tailscale.

---

## 4. Arquitetura

### 4.1 Princípios inegociáveis
1. **`core/` ↔ `clients/` separados.** O core é agnóstico de transporte e **nunca importa de `clients/`**. Clientes são finos: capturam input, exibem output.
2. **Providers intercambiáveis.** O agente fala com `LLMProvider` (abstrato), nunca com `OllamaProvider`/`GroqProvider` diretamente.
3. **Tools são plugins.** Cada tool declara seu schema e se auto-registra. Tool nova = arquivo novo em `brain/tools/`.
4. **Shell é HMI, Python é cérebro.** `shell/juniper.sh` é wrapper; lógica vive em `brain/`.
5. **Um só ponto de config.** Nenhum módulo lê `os.environ`, YAML ou `.env` diretamente — tudo via `from brain.config import settings`.

### 4.2 Fluxo (Sprint 1, local)
```
usuário → clients/cli.py → core/agent.py ──► llm/router.py ──► Groq | Ollama
                               │  ▲
                 tool_calls    ▼  │ ToolResult
                          core/dispatcher.py → tools/registry.py → Tool.run()
                               │
                          logs/*.jsonl
```
Loop agentic: **decide → chama tool → reavalia**, limitado por `agent.max_iterations` (padrão 5).

### 4.3 Deployment (ADR-007)
Cérebro único e sempre vivo (VPS); "corpos" (executores) múltiplos e efêmeros (PC, celular, Raspberry) conectados por WebSocket + HTTPS via Tailscale (sem portas públicas). Auth por token estático em `~/.juniper/token` (Sprint 2+). Modos: `local` (Sprint 1) e `server` (Sprint 2+). Tools têm *locality* (cloud vs. executor) — ver `Locality` em T-020.

### 4.4 Segurança (ADR-006)
| Tier | Comportamento | Exemplos |
|---|---|---|
| `READ` | Auto-executa, loga | `web_fetch` (GET), `ls`, `cat`, `git log` |
| `WRITE` | Confirmação inline `[y/N]`, timeout 60s | `open_app`, `git commit`, `curl POST` |
| `DESTRUCTIVE` | Confirmação + aviso de irreversibilidade | `rm`, `git reset --hard`, `push --force`, e-mail |
| `FORBIDDEN` | Bloqueio duro, nunca executa | `rm -rf /`, `mkfs`, `dd`, escrita em `/etc` |

Regras: tool sem classificação = **WRITE**. `run_shell` aceita **um único binário** (rejeita `;`, `&&`, `||`, `|`, `$()`, crases, redirecionamentos) e valida contra `tools.security.shell_allowlist`. Conteúdo vindo da web é **dado, nunca instrução** (mitigação de prompt injection): ao devolver ao LLM, encapsule como dado não confiável.

---

## 5. Estrutura do Repositório

```
~/.juniper/                       (branch v4)
├── .env                          # segredos (NÃO versionado)
├── config.yaml                   # config local (NÃO versionado)
├── config.yaml.example
├── pyproject.toml · requirements.txt · README.md
├── brain/
│   ├── __init__.py               # __version__
│   ├── __main__.py               # python -m brain
│   ├── config.py                 # ✅ settings singleton
│   ├── core/    agent.py · dispatcher.py · session.py
│   ├── clients/ cli.py ✅ · server.py (Sprint 2)
│   ├── llm/     types.py* · errors.py* · base.py · ollama_provider.py · groq_provider.py · router.py
│   ├── tools/   base.py · registry.py · system.py · web.py · juniper_cmds.py* (· shell.py stub)
│   └── memory/  session.py · vector.py   (Sprint 3)
├── shell/       juniper.sh · commands/{gitgrep,deployfeature,help,version}.sh · core/{dispatcher,loader}.sh
├── prompts/juniper.md            # persona
├── docs/        JUNIPER.md (este) · decisions/ADR-*.md
├── scripts/     dev.sh · uninstall.sh
└── tests/       test_config.py
```
`*` = ainda não existe, criar na task indicada.

---

## 6. Configuração (ADR-003)

- `config.yaml` = preferências; `.env` = segredos. **Precedência:** args do construtor > ENV > `.env` > YAML > defaults.
- Prefixo `JUNIPER_`, aninhamento com `__`: `JUNIPER_LLM__OLLAMA__URL` → `llm.ollama.url`.
- Segredo: `JUNIPER_GROQ_API_KEY` (obter em https://console.groq.com/keys). Acesso: `settings.groq_api_key.get_secret_value()`.
- Localização do YAML: `JUNIPER_CONFIG_PATH` → `~/.juniper/config.yaml` → `./config.yaml`.
- `settings.paths.{home,logs,config}` são calculados, não configuráveis.

```yaml
llm:
  priority: [groq, ollama]        # só "groq" e "ollama" são válidos
  groq:   {model: llama-3.3-70b-versatile, timeout: 30}
  ollama: {url: "http://localhost:11434", model: llama3.1, timeout: 120}
agent:
  max_iterations: 5
  language: pt-BR
  timezone: America/Sao_Paulo
deployment:
  mode: local                     # local | server
  server: {host: 127.0.0.1, port: 8765}
  auth:   {token_file: ~/.juniper/token}
tools:
  security:
    shell_allowlist: [git, ls, cat, grep, rg, fd, jq, find, wc, head, tail]
    shell_require_confirmation: true
    confirmation_timeout: 60
```

**Para adicionar uma opção:** criar/alterar o submodelo pydantic em `brain/config.py`, atualizar `config.yaml.example` e adicionar teste em `tests/test_config.py`.

---

## 7. Convenções de Código

- **Tipos estritos:** `mypy --strict` deve passar. `from __future__ import annotations` nos módulos.
- **Formatação/lint:** ruff, `line-length = 100`, regras `E F I N W UP B C4 SIM`.
- **Estilo:** pragmático — priorize o que funciona; sem abstração especulativa.
- **Docstrings:** estilo Google, em inglês.
- **Nomes:** `snake_case` funções/variáveis, `PascalCase` classes, `UPPER_CASE` constantes.
- **Async:** I/O de LLM e tools é `async`. Testes async sem decorator (modo `auto`).
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`), prefixados pela task quando aplicável (ex.: `feat(T-011): ...`).
- **Testes:** nenhum teste pode depender de rede, de chave real da Groq ou de Ollama rodando — use mocks (`httpx.MockTransport`, fakes).
- **Segredos:** nunca logar nem imprimir `groq_api_key`.

### Definition of Done (toda task)
1. Critérios de aceite da task atendidos.
2. `pytest tests/ -v`, `ruff check brain/ tests/`, `mypy brain/` passam.
3. Testes novos cobrindo caminho feliz e erros.
4. Docs/ADR atualizados se houve decisão arquitetural (criar `docs/decisions/ADR-0XX.md`).
5. Commit atômico por task.

Comandos:
```bash
source .venv/bin/activate
pytest tests/ -v && ruff check brain/ tests/ && mypy brain/
./scripts/uninstall.sh --yes && ./scripts/dev.sh      # reinstalação limpa
```

---

## 8. Decisões Resumidas (ADRs)

| ADR | Decisão |
|---|---|
| 001 | Interação em PT-BR; código/logs em inglês. Providers: Groq + Ollama com prioridade configurável. `commands/chat.sh` legado removido — conversa via `python -m brain chat`. |
| 002 | Tools no formato **OpenAI Function Calling**; classe Python com `name`, `description` (PT-BR, diz *o que faz e quando usar*), `parameters` (JSON Schema), `tier`, `run()`; auto-registro por `@register_tool`; retorno `ToolResult`. |
| 003 | Config: YAML + `.env` + ENV com prefixo `JUNIPER_`. |
| 006 | Segurança em 4 tiers; default WRITE; `run_shell` binário único + allowlist; web = dado. |
| 007 | Cliente-servidor single-user; brain em VPS; Tailscale; executores por WebSocket; modos `local`/`server`. |

---

## 9. Backlog Sprint 1 — Especificação das Tasks

Ordem de dependência: **T-011 → T-012/T-013 → T-014 → T-015 → T-020 → T-021 → T-022…T-026 → T-030 → T-031 → T-040 → T-041 → T-042.**
Prefira tasks pequenas e verificáveis. Antes de começar uma task com decisões em aberto, **pergunte ao usuário** (ele quer participar das decisões).

### T-011 — Interface `LLMProvider` *(próxima)*
**Decisões em aberto** (recomendações entre parênteses): sync vs async (**só async**); streaming por `AsyncIterator` vs callback (**AsyncIterator**); tipos em `base.py` vs `types.py` (**`types.py` separado**); `TokenUsage` com campos opcionais (**sim**).

**Arquivos:** `brain/llm/types.py`, `brain/llm/errors.py`, `brain/llm/base.py`, `tests/test_llm_base.py`.

**Contrato sugerido:**
```python
# types.py  (dataclasses ou pydantic; frozen quando possível)
Role = Literal["system", "user", "assistant", "tool"]
Message:      role, content: str | None, tool_calls: list[ToolCall] = [], tool_call_id: str | None, name: str | None
ToolCall:     id: str, name: str, arguments: dict[str, Any]     # arguments já parseado (não string JSON)
TokenUsage:   prompt_tokens: int | None, completion_tokens: int | None, total_tokens: int | None
LLMResponse:  message: Message, usage: TokenUsage | None, model: str, provider: str, finish_reason: str | None
StreamChunk:  delta: str = "", tool_calls: list[ToolCall] = [], usage: TokenUsage | None, finish_reason: str | None

# errors.py
LLMError(Exception)
 ├─ LLMTimeoutError
 ├─ LLMAuthError          # 401/403 — NÃO faz failover útil, mas é tipado
 ├─ LLMRateLimitError     # 429 — failover
 └─ LLMUnavailableError   # conexão recusada, 5xx — failover

# base.py
class LLMProvider(ABC):
    name: ClassVar[str]
    async def chat(self, messages: list[Message], *, tools: list[dict[str, Any]] | None = None,
                   temperature: float = 0.7, max_tokens: int | None = None) -> LLMResponse: ...
    def stream(self, messages, *, tools=None, temperature=0.7, max_tokens=None) -> AsyncIterator[StreamChunk]: ...
    async def health_check(self) -> bool: ...
```
**Aceite:** `LLMProvider` não instanciável; hierarquia de erros testada (`issubclass`); um `FakeProvider` nos testes implementa a interface; tipos serializam para o formato OpenAI (método `to_openai()` em `Message`/`ToolCall`); mypy strict limpo.

### T-012 — `OllamaProvider`
`POST {url}/api/chat` via `httpx.AsyncClient` com `settings.llm.ollama.{url,model,timeout}`; enviar `tools` no formato ADR-002; converter `tool_calls` da resposta para `ToolCall` (Ollama retorna `arguments` já como dict e pode não trazer `id` → gerar `uuid`). Mapear erros: `httpx.ConnectError`→`LLMUnavailableError`, `TimeoutException`→`LLMTimeoutError`, 5xx→`LLMUnavailableError`. `health_check` via `GET /api/tags`.
**Aceite:** testes com `httpx.MockTransport` para texto, tool call e cada erro.

### T-013 — `GroqProvider`
Usar o SDK `groq` (`AsyncGroq`) com chave de `settings.groq_api_key` (erro `LLMAuthError` claro se ausente) e `settings.llm.groq.{model,timeout}`. Em Groq `tool_calls[].function.arguments` é **string JSON** → `json.loads` (tratar JSON inválido como `LLMError`). Mapear `AuthenticationError`→`LLMAuthError`, `RateLimitError`→`LLMRateLimitError`, `APITimeoutError`→`LLMTimeoutError`, `APIConnectionError`/`InternalServerError`→`LLMUnavailableError`.
**Aceite:** testes com cliente SDK mockado; chave nunca aparece em logs/exceções.

### T-014 — Streaming
Implementar `stream()` em ambos providers. Ollama: NDJSON linha a linha. Groq: `stream=True`. Acumular `tool_calls` fragmentados e emiti-los completos no chunk final. Último chunk traz `finish_reason` e `usage` quando disponível.
**Aceite:** concatenar `delta`s == conteúdo de `chat()` equivalente; testes de tool call em stream.

### T-015 — `LLMRouter`
Itera `settings.llm.priority`; instancia providers por nome (registro `{"groq": GroqProvider, "ollama": OllamaProvider}`). Failover para o próximo em `LLMUnavailableError`, `LLMTimeoutError`, `LLMRateLimitError` **e** `LLMAuthError` (provider inutilizável) — mas registra warning. Se todos falham, lança `LLMError` agregando causas. Expõe a mesma interface de `LLMProvider` (`chat`/`stream`). Em `stream`, só faz failover **antes** do primeiro chunk emitido.
**Aceite:** testes com providers fake cobrindo: 1º ok; 1º falha→2º ok; todos falham; falha no meio do stream não duplica saída.

### T-020 — Base de Tools
```python
class Tier(StrEnum): READ, WRITE, DESTRUCTIVE, FORBIDDEN
class Locality(StrEnum): CLOUD, EXECUTOR      # onde roda (ADR-007); Sprint 1: tudo local
@dataclass class ToolResult: success: bool; output: str|None=None; error: str|None=None; metadata: dict|None=None
class Tool(ABC):
    name: ClassVar[str]; description: ClassVar[str]; parameters: ClassVar[dict[str, Any]]
    tier: ClassVar[Tier] = Tier.WRITE; locality: ClassVar[Locality] = Locality.EXECUTOR
    @abstractmethod async def run(self, **kwargs: Any) -> ToolResult
    @classmethod def to_schema(cls) -> dict   # formato ADR-002
```
Validar no `__init_subclass__` (ou no registro) que `name` é snake_case e `parameters` é objeto JSON Schema.
**Aceite:** `to_schema()` bate com o formato canônico do ADR-002; tier default WRITE testado.

### T-021 — Registry
`@register_tool` registra a classe; `registry.get(name)`, `registry.all()`, `registry.schemas()`. Nome duplicado → erro. Auto-descoberta: importar todos os módulos de `brain/tools/` via `pkgutil.iter_modules` (pular `base`, `registry`).
**Aceite:** adicionar um arquivo de tool novo em teste (tmp package) a torna visível sem editar lista central.

### T-022 — `open_app`
Tier WRITE. Parâmetro `target` (URL ou nome do app). WSL: `wslview`/`explorer.exe`/`cmd.exe /c start`; Linux: `xdg-open`. Sem `shell=True` (usar `asyncio.create_subprocess_exec`). Validar que URL tem esquema `http(s)`; nomes de app só `[A-Za-z0-9._-]`.
**Aceite:** subprocess mockado; entrada com metacaracteres rejeitada.

### T-023 — `run_shell`
Tier dinâmico: classificar o comando (leitura da allowlist → READ; `git commit`/`add` → WRITE; `rm`, `git reset --hard`, `push --force` → DESTRUCTIVE; `rm -rf /`, `mkfs`, `dd`, escrita em `/etc` → FORBIDDEN; binário fora da allowlist → rejeitado). Parse com `shlex.split`; rejeitar operadores compostos; executar com `create_subprocess_exec`, timeout, truncamento de saída (ex.: 20 KB).
**Aceite:** tabela de testes de classificação; injeções (`ls; rm -rf ~`, `` `x` ``, `$(x)`, `a | b`) rejeitadas.

### T-024 — `web_fetch`
Tier READ, `Locality.CLOUD`. GET via httpx, timeout, limite de tamanho, só `http/https`; converter HTML→texto simples; **bloquear IPs privados/loopback/link-local (SSRF)**. Saída envolta em marcação de conteúdo não confiável.

### T-025 — `web_search`
Tier READ, `Locality.CLOUD`. Provider de busca a decidir com o usuário (opções: DuckDuckGo HTML/lib, Tavily, Brave API). Retornar título, URL, snippet (top N). Registrar decisão em ADR se escolher serviço pago/com chave.

### T-026 — Bridge de comandos legados
Tool(s) que invocam `shell/juniper.sh <cmd>` para `gitgrep` e `deployfeature` (só argumentos validados, sem `shell=True`). `deployfeature` = DESTRUCTIVE/WRITE conforme o que o script faz — **ler o script antes de classificar**.

### T-030 — Agent loop + Dispatcher + Session
- `Session`: lista de `Message`, system prompt carregado de `prompts/juniper.md` (+ data/hora no timezone configurado).
- `Dispatcher`: dado `ToolCall` → busca no registry → checa tier → pede confirmação via **callback injetado** (o core não faz `input()`; o cliente fornece `confirm(tool, args, tier) -> bool` com timeout `confirmation_timeout`) → executa → retorna `Message(role="tool")`. FORBIDDEN nunca executa; tool desconhecida / argumentos inválidos → `ToolResult(success=False)` devolvido ao LLM (não levanta).
- `Agent.run(user_input)`: loop até o LLM responder sem `tool_calls` ou atingir `max_iterations` (então responde avisando o limite). Suporta streaming de tokens para o cliente via callback/iterator.
**Aceite:** testes com `FakeProvider` roteirizado: resposta direta; 1 tool call; encadeadas; limite de iterações; confirmação negada; tool FORBIDDEN.

### T-031 — Logging estruturado
JSONL em `settings.paths.logs/` (um arquivo por dia). Eventos: `llm_request/response` (provider, modelo, latência, usage), `tool_call` (nome, tier, args, decisão, resultado), `error`, `failover`. Redigir segredos. Sem `print` em `brain/` fora de `clients/`.

### T-040 — CLI completo
`juniper chat "msg"` (one-shot) e `juniper chat` (REPL) com `rich`, streaming de tokens, prompt de confirmação `[y/N]` com timeout, `--provider` opcional, saída de erros amigável em PT-BR (ex.: chave Groq ausente → instrução do `.env`).

### T-041 — Subcomandos legados
`juniper gitgrep ...` e `juniper deployfeature ...` delegando ao `shell/` (comportamento idêntico ao v2).

### T-042 — `juniper tools`
Lista tools registradas: nome, tier, locality, descrição (tabela `rich`).

---

## 10. Roadmap

| Sprint | Foco | Camada J.A.R.V.I.S. |
|---|---|---|
| 1 | Hello agent (local, CLI) | Cognição básica + primeiro reflexo |
| 2 | VPS + multi-dispositivo (`clients/server.py`, WS, auth) | Sempre disponível + olhos (web) |
| 3 | Memória (MongoDB + RAG; `memory/`) | Memória de longo prazo |
| 4 | Voz (STT + TTS + wake word) | HMI natural |
| 5 | Executores (PC, celular, IoT) | Corpos múltiplos |
| 6 | Iniciativa + scheduler | Proatividade supervisionada |

---

## 11. Dívidas Técnicas Conhecidas

1. **Versão desalinhada:** `pyproject.toml` = `3.0.0a1`, `brain/__init__.py` = `4.0.0`. Sincronizar (preferência: `dynamic = ["version"]` lendo `brain.__version__`).
2. `CHANGELOG.md` e `BACKLOG.md` não existem no repo (citados pelos ADRs).
3. `brain/clients/cli.py`: `chat()` ainda é placeholder (T-040).
4. Sem CI — rodar pytest/ruff/mypy manualmente (sugestão: GitHub Actions).
5. `.env.example` ausente.
6. `brain/tools/shell.py` stub coexiste com o plano `system.py` — decidir e remover um.

---

## 12. Preferências de Colaboração

- Quer **participar das decisões** — pergunte antes de decisões de design com trade-offs reais.
- **Tasks pequenas**, incrementais, com critério de aceite claro.
- **Analogias** do cotidiano ajudam a explicar conceitos.
- Código pragmático; prioriza o que funciona.
- Gosta de **ADRs** para decisões arquiteturais e de ver progresso em sprints com metáforas visuais ("esqueleto", "cérebro", "voz").
