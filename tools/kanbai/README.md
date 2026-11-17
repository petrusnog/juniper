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

## Ainda não existe

Persistência em Postgres, servidor MCP do quadro (fase 2), interface e agentes com imagem da
Nekos API (fase 3), delegadora/executora/QA reais, git worktree, commit e PR reais (fase 4).

## Testes

```bash
cd tools/kanbai && pip install pytest && python -m pytest -q
```
