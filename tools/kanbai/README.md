# Kanbai (V1, fase 1: regras do quadro)

Orquestrador de agentes em kanban, **generalista** (a Juniper é o primeiro projeto). Especificação
e decisões: [ADR-038](../../docs/decisions/ADR-038.md). Projeto à parte: **não importa de `brain/`**
e `brain/` não importa dele.

## O que existe

- `kanbai/domain.py`: colunas, papéis, tarefa, KAI, agente, evento, `Result` (falha esperada é
  resultado tipado, nunca exceção).
- `kanbai/board.py`: as regras. Quem move o quê, limite de 3 executoras (`DOING` + `CORRECTION`,
  checado ao iniciar), um KAI por executora, a mesma executora corrige, só o humano decide a
  revisão humana, commit `feature(T0xx): ...` injetado e feito só ao ir para Feito, PR injetado
  e aberto só quando todos os KAIs da tarefa estão Feitos, `review_packet` sem o relatório da
  executora (QA isolada), `create_task` idempotente por (projeto, chave).
- `kanbai/modelmap.py` + `models.default.toml`: pontos Fibonacci para modelo, editável.
- `kanbai/store.py`: contrato `Store` e `MemoryStore`.

- `kanbai/pgstore.py` + `schema.sql`: `Store` em Postgres (banco próprio; compose em `docker-compose.yml`, só
  `127.0.0.1:5434`). As mesmas regras rodam nos dois armazenamentos.
- `kanbai/mcp_server.py`: servidor MCP por **HTTP** em loopback (`/mcp`), sem estado de sessão. Cada
  agente tem **um token** (cabeçalho `Authorization: Bearer`); só o hash é guardado. O token define
  o cargo; o quadro recusa o que o cargo não permite. Tools: `whoami`, `list_board`, `list_tasks`,
  `get_kai`, `create_task` (idempotente; é a que a agente importadora de issues chama), `create_kai`,
  `start_kai`, `submit`, `review_packet`, `review`, `set_human_summary`. A decisão humana não é tool de
  agente (vai pela interface).
- `python -m kanbai migrate | project | agent | serve`: o `agent` imprime o token uma vez.

## Decisões de arquitetura (aprovadas)

Serviço (API, MCP, UI, Postgres) em container; um **worker no host** observa o quadro e lança as CLIs
(`claude -p`, `codex exec`) em **git worktree** por tarefa, com `git push` e `merge` bloqueados; o
container não recebe credenciais nem o repositório. Gatilhos: tarefa nova dispara a delegadora; KAI em
A fazer, uma executora (máx. 3); KAI em Revisando, a QA; Correção retoma a sessão da executora
(`--resume`). Teto de 3 idas e voltas QA/executora, depois vai à Revisão Humana com "QA não convergiu".
PR com `gh` do host, sem merge. Interface: FastAPI + HTMX, sem Node; imagem da Nekos API baixada pelo
servidor e guardada localmente, com reserva.

## Ainda não existe

Worker no host, teto de correções, worktrees, commit e PR reais, interface, imagem das agentes,
Dockerfile do serviço (o compose só tem o Postgres), importação das issues (a agente Claude faz, via
`create_task`).

## Testes

```bash
cd tools/kanbai && pip install pytest "psycopg[binary]" uvicorn httpx && python -m pytest -q
# contra um Postgres (rode duas vezes: memória e banco):
KANBAI_TEST_DATABASE_URL=postgresql://kanbai:SENHA@127.0.0.1:5434/kanbai_test python -m pytest -q
```
