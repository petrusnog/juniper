# Base de conhecimento (Postgres + pgvector + MCP)

A documentação da Juniper (`docs/pages` e `docs/decisions`) indexada **por nota** (uma seção
`##`/`###`) num Postgres com busca vetorial, para os agentes do orquestrador acharem uma
informação pontual sem ler documentos inteiros. É **exclusiva do orquestrador**: a Juniper em
produção continua com o BM25 local de `brain/metadocs` (ADR-025).

Os MDs continuam sendo a fonte da verdade; o banco é um índice derivado e idempotente.

## Subir

```bash
cd tools/knowledge
cp .env.example .env            # defina KNOWLEDGE_PG_PASSWORD
docker compose up -d            # Postgres 17 + pgvector, só em 127.0.0.1:5433
ollama pull bge-m3              # embeddings locais (1024 dim, multilíngue)
python3 -m venv .venv && .venv/bin/pip install "psycopg[binary]" pgvector "mcp>=2" httpx pytest
.venv/bin/python -m knowledge index      # incremental; --force reindexa tudo
.venv/bin/python -m knowledge search "como a confirmação é interpretada"
```

O primeiro `index` embute todas as notas (na CPU leva alguns minutos); as próximas só reprocessam
os documentos e as notas que mudaram (hash de conteúdo).

## Modelo de dados

`documents` (arquivo, título, grupo, tags, hash) → `notes` (âncora, texto em Markdown, `tsvector`
em português, `embedding vector(1024)`, `kind`/`file` dos títulos técnicos) e `links` (o grafo de
"Para saber mais" e dos links internos).

## Busca

Híbrida: vizinhos por cosseno (HNSW) + full-text em português (`ts_rank_cd`), fundidos por RRF
(k=60). No máximo 2 notas por documento. O texto devolvido respeita um orçamento de tokens e passa
pelo filtro de segredos da Juniper.

## Servidor MCP (somente leitura, stdio)

Tools: `search_notes(query, limit, max_tokens, kind, tag)`, `get_note(ref)` (sem âncora, lista as
seções), `neighbors(source)` e `list_documents`.

```bash
# Claude Code
claude mcp add juniper-knowledge -- /caminho/tools/knowledge/.venv/bin/python -m knowledge serve
# Codex (~/.codex/config.toml)
# [mcp_servers.juniper-knowledge]
# command = "/caminho/tools/knowledge/.venv/bin/python"
# args = ["-m", "knowledge", "serve"]
```

Rode o `serve` com o diretório de trabalho em `tools/knowledge` (ou com `PYTHONPATH` apontando
para ela).

## Testes

```bash
.venv/bin/python -m pytest tests -q   # os de banco criam e apagam o banco knowledge_test
```
