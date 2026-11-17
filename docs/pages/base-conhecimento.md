---
title: Base de conhecimento
group: Ambiente Local
order: 6
description: Notas no Postgres, busca vetorial e MCP para os agentes.
status: pronta
tags: base conhecimento rag postgres pgvector embeddings busca vetorial híbrida mcp notas agentes orquestrador kanbai bge-m3 ollama rrf adr-037
---

> **Nota:** esta página detalha o [ADR-037](../decisions/ADR-037.md). Código em `tools/knowledge/`. Serve ao orquestrador de agentes ([ADR-038](../decisions/ADR-038.md)); a Juniper em produção continua com a [busca local](metadocs.md).

## Visão geral {#visao-geral}

Um agente que precisa de **uma informação pontual** da documentação não deve ler uma página de milhares de tokens. A base de conhecimento guarda cada seção (`##`/`###`) de `docs/pages` e `docs/decisions` como uma **nota** num Postgres e a devolve por busca que entende **significado e palavras exatas**, dentro de um orçamento de tokens.

```text
docs/*.md ──► index (parse → embeddings locais → upsert por hash) ──► Postgres + pgvector
                                                                         ▲
agente (Claude, Codex...) ──MCP stdio──► search_notes / get_note / neighbors
```

Os MDs continuam a fonte da verdade; o banco é um **índice derivado**. Mudou um MD? Rode `index` de novo: só o que mudou é reprocessado.

## Subir e indexar {#subir}

```bash
cd tools/knowledge
cp .env.example .env              # defina KNOWLEDGE_PG_PASSWORD
docker compose up -d              # Postgres 17 + pgvector em 127.0.0.1:5433
ollama pull bge-m3                # embeddings locais
.venv/bin/python -m knowledge index        # incremental; --force reindexa tudo
.venv/bin/python -m knowledge search "como a confirmação é interpretada"
```

A primeira indexação embute todas as notas (minutos, na CPU). Depois, `index` com nada novo leva segundos. O `README.md` da pasta tem a instalação do ambiente Python.

## O modelo de dados {#modelo kind=módulo file=tools/knowledge/knowledge/schema.sql}

| Tabela | O que guarda |
|---|---|
| `documents` | Um por arquivo: `source`, `kind` (`page` ou `adr`), título, grupo, descrição, status, tags e o hash do conteúdo. |
| `notes` | Uma por seção: `anchor`, título da seção, texto em Markdown, `tokens_est`, `tsvector` em português, `embedding vector(1024)` e o `kind`/`file` de títulos técnicos. |
| `links` | O grafo: os links entre notas e a marca `related` para os de "Para saber mais". |

Índices: HNSW (cosseno) nos vetores, GIN no `tsvector` e nas tags. O schema é idempotente (`CREATE ... IF NOT EXISTS`).

## O parser {#parser kind=módulo file=tools/knowledge/knowledge/parse.py}

Divide cada arquivo em notas pelos títulos `##` e `###`, com o texto de abertura como a primeira. Mantém o Markdown (código e tabelas), descarta blocos `html-raw`, lê o cabeçalho (`title`, `group`, `description`, `tags`...) e o `kind=`/`file=` dos títulos técnicos, resolve os links relativos para `.md` e deduplica âncoras repetidas (`x`, `x-2`). Seguir um link simbólico para fora de `docs/` é proibido.

## O indexador {#indexador kind=módulo file=tools/knowledge/knowledge/indexer.py}

- Documento com o mesmo hash: **pulado**.
- Documento alterado: só as notas cujo texto mudou são embutidas de novo; notas que sumiram são apagadas.
- Arquivo que sumiu de `docs/`: o documento sai do banco.
- Link para um documento que não está indexado vira **aviso** no fim da execução.

O texto embutido é `título da página › título da seção`, a descrição e o corpo (o contexto afina o vetor). Os embeddings vêm do Ollama (`bge-m3`, configurável por `KNOWLEDGE_EMBED_MODEL` e `KNOWLEDGE_OLLAMA_URL`) em `knowledge/embed.py`.

## A busca {#busca kind=módulo file=tools/knowledge/knowledge/search.py}

1. Os 30 vizinhos mais próximos por cosseno e as 30 melhores notas por full-text em português (consulta com OR entre as palavras).
2. **RRF** (`1 / (60 + posição)`) funde as duas listas: uma nota que as duas acham sobe. Não é preciso calibrar pesos entre pontuações de natureza diferente.
3. No máximo 2 notas por documento; filtros opcionais por `kind` (`page` ou `adr`), `tag` e grupo.
4. Cada nota volta como **trecho**: os parágrafos que mais casam com a pergunta, dentro de `max_tokens` dividido entre as notas, sempre pelo filtro de segredos da Juniper.

## O servidor MCP {#mcp kind=módulo file=tools/knowledge/knowledge/mcp_server.py}

Somente leitura, por **stdio** (nenhuma porta). Serve Claude, Codex ou qualquer cliente MCP.

| Tool | Para quê |
|---|---|
| `search_notes(query, limit, max_tokens, kind, tag)` | A busca acima; devolve trechos curtos com `ref` (`docs/pages/x.md#ancora`) e o custo da nota inteira. |
| `get_note(ref, max_tokens)` | A nota inteira, com o arquivo de código quando o título é técnico. Sem âncora, lista as seções do documento (o sumário, barato). |
| `neighbors(source)` | Para onde o documento aponta (as de "Para saber mais" primeiro) e quem o cita. |
| `list_documents(kind, tag, group)` | Arquivo, título e descrição de cada documento. |

Registrar no Claude Code: `claude mcp add juniper-knowledge -- <repo>/tools/knowledge/.venv/bin/python -m knowledge serve`; no Codex, uma entrada `[mcp_servers.juniper-knowledge]` em `~/.codex/config.toml` com o mesmo comando. Rode com o diretório de trabalho em `tools/knowledge`.

Falhas esperadas (banco fora, Ollama fora, nota inexistente) voltam como **texto** para o agente, nunca como exceção.

## Segurança {#seguranca}

| Risco | Defesa |
|---|---|
| Segredo escrito por engano na documentação | Todo texto devolvido passa por `mask_secrets` (o mesmo filtro da Juniper). |
| Ler arquivos fora da documentação | O indexador só lê `docs/pages` e `docs/decisions` (links simbólicos para fora são ignorados) e o servidor MCP não tem parâmetro de caminho. |
| Escrita pelo agente | O servidor MCP só lê. Quem escreve no banco é o `index`, rodado por você. |
| Exposição de rede | O Postgres escuta só em `127.0.0.1`; a senha vem do `.env` (fora do Git). |

## Testes {#testes}

Os testes ficam na pasta `tests/` da própria ferramenta (dentro de `tools/knowledge/`, fora do `pytest` da Juniper) e cobrem o parser (seções, âncoras duplicadas, títulos técnicos, `html-raw`, links), o RRF, a consulta full-text, o recorte de trechos com máscara de segredos e, num banco descartável (`knowledge_test`, com um embedder falso), a indexação idempotente e incremental, a busca híbrida, `get_note` e `neighbors`. Rode `cd tools/knowledge && .venv/bin/python -m pytest tests -q`. O `python -m knowledge.bench` mede a economia de tokens.

## Para saber mais {#para-saber-mais}

- [Metadocumentação](metadocs.md) (Documentação Técnica): A busca BM25 local com que a Juniper responde sobre si mesma; continua como está.
- [Como contribuir](contribuindo.md) (Ambiente Local): O fluxo de trabalho, os testes exigidos e como manter a documentação.
- [Ambiente Docker](dev-docker.md) (Ambiente Local): A mesma topologia da produção, com o código montado por volume.
- [Tools](tools.md) (Documentação Técnica): O formato das tools, para comparar com as tools do servidor MCP.
