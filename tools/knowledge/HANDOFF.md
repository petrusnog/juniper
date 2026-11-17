# Handoff para a agente da nuvem

> Arquivo temporário: apague-o quando o Kanbai estiver em andamento e o estado estiver nos docs.
> Escrito em 2026-11-17. Leia na ordem: este arquivo → `docs/decisions/ADR-038.md` (o Kanbai, com o
> pedido do Petrus na íntegra) → `docs/decisions/ADR-037.md` e `docs/pages/base-conhecimento.md`
> (a base de conhecimento).

## Regras de trabalho deste handoff

- Siga o `CLAUDE.md` do repositório (idioma, honestidade sobre o que foi verificado, docs).
  **Exceção pedida pelo Petrus:** neste trabalho **não há issue nem branch `feature/T0xx`**. Trabalhe
  na branch em que você foi aberta, em commits pequenos, e **não faça merge** nem abra PR sem ordem.
- Decisões de design com trade-off real são do Petrus: apresente opções, recomende e pergunte.
  O ADR-038 lista os pontos em aberto do Kanbai com recomendação; **pergunte antes de implementar**.
- Nunca afirme que algo funciona sem ter rodado. Diga o que foi e o que não foi verificado.

## O que está pronto (a base de conhecimento, `tools/knowledge/`)

- `docker-compose.yml` (Postgres 17 + pgvector, só `127.0.0.1:5433`), `knowledge/schema.sql`,
  `parse.py`, `embed.py` (Ollama `bge-m3`), `indexer.py`, `search.py` (híbrida, RRF), `mcp_server.py`,
  `__main__.py` (CLI `migrate | index | search | serve`), `bench.py`, `README.md`.
- Testes em `tests/test_knowledge.py` (9): parser, ranking, e indexação/busca/notas/vizinhos num
  banco descartável com embedder falso.
- Documentação: ADR-037, página `docs/pages/base-conhecimento.md`, `docs/JUNIPER.md` atualizado.

## O que NÃO foi verificado ou falta na base

Veja o fim deste arquivo, "Estado verificado", preenchido pelo autor com o que de fato rodou.

## O que fazer

1. **Rodar e conferir a base** (se o ambiente permitir Postgres com pgvector): os testes de banco
   pulam sozinhos sem Postgres; os puros (parser, RRF, trecho) rodam em qualquer lugar. A nuvem
   **não tem o Ollama nem o Postgres do Petrus**: não tente indexar de verdade lá; o índice real
   vive na máquina dele. Use o embedder falso dos testes.
2. **Kanbai**, em fases (ADR-038): (1) backend e regras do quadro, (2) servidor MCP do quadro,
   (3) interface anime, (4) agentes e integração Git/GitHub. A fase 1 não precisa de GPU, Ollama
   nem rede e é boa para começar na nuvem, **depois de o Petrus responder os pontos em aberto**
   (stack, persistência, como lançar as agentes, tabela de story points, origem das tarefas, quem
   commita).
3. O Kanbai é um **projeto à parte**: crie-o fora de `brain/` (por exemplo `tools/kanbai/` ou um
   repositório próprio, conforme o Petrus decidir). `brain/core` nunca importa dele, e ele não
   importa de `brain/`.

## Restrições que valem para tudo

- Nenhuma porta pública: serviços só em `127.0.0.1` (ADR-013).
- Segredos só em `.env` (fora do Git). Não leia nem escreva `.env`, `config.yaml`, `certs/`.
- Nenhum merge em `master`, nenhum `push --force`, nenhum `down -v` sem ordem do Petrus.
- Não altere o BM25 da Juniper (`brain/metadocs`, ADR-025): a base nova é só do orquestrador.
- Mexeu em `docs/`? Rode `python scripts/build_docs.py && pytest tests/test_docs.py tests/test_metadocs.py -q`
  (precisa das dependências do projeto: `pip install -r requirements.txt`).

## Estado verificado (2026-11-17, na máquina do Petrus)

Rodou de verdade:
- `docker compose up -d`; schema aplicado; **93 arquivos de `docs/` indexados** (1.088 notas com
  embedding `bge-m3` via Ollama), mais as 3 páginas/ADRs novos depois.
- 10 testes em `tools/knowledge/tests` verdes (parser, RRF, indexação idempotente e incremental,
  busca, `get_note`, `neighbors`, exclusão da abertura de ADR do ranking).
- Servidor MCP exercitado de ponta a ponta com o cliente MCP do Python (stdio): `list_tools`,
  `search_notes`, `get_note` (nota, sumário e inexistente), `neighbors`.
- `python -m knowledge.bench`: 73% menos tokens (30.287 → 8.108) em 10 perguntas; ressalvas no ADR-037.
- `build_docs.py` e `pytest tests/test_docs.py tests/test_metadocs.py` verdes (70).

**Não verificado:**
- O servidor MCP **não foi registrado num Claude Code nem num Codex de verdade**; só testado com o
  cliente MCP do Python. Registre e confira o fluxo real (`claude mcp add ...`, `~/.codex/config.toml`).
- A busca exata por termo (ex.: `ADR-012 protocol_version`) devolve notas "Referências" de outros
  ADRs que citam o número, antes da decisão do próprio ADR-012. Possível melhoria: peso extra
  para o título do documento, ou rebaixar notas de "Referências". Não feito.
- `pytest -m "not integration"` da **Juniper** não foi rodado (esta mudança não toca `brain/`).
- Não há gatilho automático de reindexação; rodar `python -m knowledge index` à mão.
