---
title: Memória de sessão
group: Documentação Técnica
section: Memória
order: 11
description: A conversa atual, o orçamento de tokens, retomar com --resume e onde as conversas ficam (arquivos ou MongoDB).
status: pronta
tags: sessão resume sessionstore mongodb docker compose arquivos json conversas retomar
---

## Visão geral {#visao-geral}

A memória de curto prazo: a Juniper lembra da conversa que está acontecendo, não estoura o limite do LLM e, se você fechar o terminal, pode retomar de onde parou. Arquivos: `brain/core/session.py` ([Agente](agente.html#session)) e `brain/memory/session.py`. Decisões de fundo no ADR-011.

## Uso {#uso}

```bash
juniper                         # conversa nova (a anterior fica guardada)
juniper --resume                # retoma a conversa mais recente (atalho: -r)
juniper -r "e o prazo?"         # retoma e já manda uma mensagem
```

Toda conversa é salva sozinha ao fim de cada turno. Sem conversa anterior, `--resume` avisa e abre uma nova. Retomar mostra "Retomando a conversa de 13/10 às 19:15 (4 mensagens suas)".

## Como funciona {#funciona}

| Peça | O que faz |
|---|---|
| `Session` | Guarda o histórico, um id (ordenável por data) e os horários de criação e atualização. |
| Orçamento de tokens | Antes de cada pedido ao LLM, `compact()` mantém o histórico dentro de `memory.session.max_tokens`. Os tokens são estimados (≈ 4 caracteres cada), sem tokenizer. |
| `SessionStore` | A interface que o `Agent` usa para salvar. |
| `FileSessionStore` | Um arquivo JSON por conversa. |
| `MongoSessionStore` | Um documento por conversa no MongoDB. |
| `Agent` | Salva depois de cada turno, inclusive quando o turno falha (já desfeito pelo rollback). |

### O que sai quando passa do limite {#limite}

Primeiro vale o teto de turnos (`max_turns`). Depois, se o histórico ainda passa de `max_tokens`, saem os turnos mais antigos, **inteiros**: a sua mensagem e tudo que veio depois dela, tool calls incluídas. Assim nunca sobra uma resposta de tool sem a chamada que a originou, que o LLM recusaria. O turno em andamento nunca é descartado. Não há resumo por LLM: o que importa de uma conversa antiga vira [memória de longo prazo](memoria-longo-prazo.html).

### SessionStore {#sessionstore kind=interface file=brain/memory/session.py}

A interface de persistência (`typing.Protocol`): `save`, `load`, `latest` e `list_sessions` (e não `list`, que conflitaria com o tipo `list[...]` no mypy estrito). O resto da Juniper só conhece ela, então trocar arquivos por MongoDB não mexe em mais nada.

### FileSessionStore {#filesessionstore kind=classe file=brain/memory/session.py}

Um arquivo JSON por conversa em `~/.juniper/sessions/`. A pasta é só sua (permissão `700`, arquivos `600`) e a escrita é **atômica**: uma queda no meio deixa a versão anterior, nunca um arquivo pela metade. O id vira nome de arquivo, então é validado (`[A-Za-z0-9_-]`) e um id como `../algo` não escapa da pasta. Um arquivo corrompido é ignorado ao buscar a conversa mais recente.

### Ao retomar {#retomar}

Dois cuidados: **a persona não é salva** (vale a atual) e **uma confirmação pendente não volta**. Se a Juniper tinha perguntado "posso apagar?", depois de retomar nada é executado sem um novo "sim" (ADR-006).

## MongoDB {#mongodb}

Com `memory.session.store: mongo`, as conversas ficam no MongoDB em vez de arquivos. É também onde ficam as [memórias de longo prazo](memoria-longo-prazo.html).

> **Atenção:** o MongoDB roda num container, então é preciso ter o **Docker** (com o plugin `docker compose`) instalado e rodando. Sem Docker, use `store: file` (o padrão): as conversas ficam em JSON e nada mais é necessário.

### Como ligar {#ligar}

O jeito fácil: o instalador pergunta onde guardar a memória e, se você escolher o MongoDB, confere o Docker, sobe o banco, cria usuário e senha no `.env` e liga o `store: mongo` sozinho. Para fazer depois: `juniper install --memory=mongo`. À mão:

```bash
# 1. .env (nunca é versionado): escolha usuário e senha do banco
JUNIPER_MEMORY__MONGO__USERNAME=juniper
JUNIPER_MEMORY__MONGO__PASSWORD=uma-senha-forte

# 2. sobe o banco
docker compose up -d

# 3. config.yaml
memory:
  session:
    store: mongo
```

Para voltar aos arquivos, basta `store: file`.

> **Atenção:** não troque usuário e senha depois de subir: o Mongo só lê essas variáveis quando cria o volume. Para recomeçar com outras credenciais: `docker compose down -v` (**apaga os dados**) e `docker compose up -d`. As conversas antigas em arquivos **não migram**: com `store: mongo`, o `--resume` só enxerga o banco.

### MongoConnection {#mongoconnection kind=classe file=brain/memory/mongo.py}

Cria o cliente só quando preciso, faz o teste de saúde (`check()`) e traduz qualquer falha do driver em `MemoryUnavailableError`. `brain/memory/mongo.py` é o **único** arquivo que importa o driver (`pymongo`), e só é carregado com `store: mongo`.

### MongoSessionStore {#mongosessionstore kind=classe file=brain/memory/mongo.py}

Implementa o `SessionStore` (`save`, `load`, `latest`, `list_sessions`); o `Agent` não sabe que mudou. Banco `juniper`, coleção `sessions`, um documento por conversa:

```json
{
  "_id": "20261014-034312-b94e9d",
  "version": 1,
  "created_at": "2026-10-14T03:43:12+00:00",
  "updated_at": "2026-10-14T03:44:02+00:00",
  "history": [ {"role": "user", "content": "..."}, {"role": "assistant", "content": "..."} ],
  "turns": 1,
  "preview": "[...] meu projeto X usa Laravel 11",
  "saved_at": "2026-10-14T03:44:02.123456+00:00"
}
```

| Quando | O que acontece |
|---|---|
| Fim de cada turno | `replace_one` com upsert: o documento inteiro é substituído. |
| Sessão vazia | Não grava nada. |
| Gravação falha | Vai para o log (evento `error`) e a conversa continua. |
| `--resume` | Pega a conversa de maior `saved_at` (microssegundos, para não empatar). |

Como em arquivos, só o histórico é guardado. Há dois índices (`saved_at` e `updated_at + saved_at`), criados na primeira gravação. Um documento corrompido é ignorado em `latest` e `list_sessions`.

### open_session_store {#open-session-store kind=função file=brain/memory/stores.py}

Escolhe o store por `memory.session.store`. Com `mongo`, já faz o teste de saúde: é o *healthcheck* de inicialização.

### MemoryUnavailableError {#memoryunavailableerror kind=classe file=brain/memory/errors.py}

Erro com mensagem escrita para você, em português, dizendo o que fazer. O CLI a imprime e sai com código 1, **sem stacktrace**, antes de falar com o LLM.

| Situação | O que você vê |
|---|---|
| Banco fora do ar | "Não consegui conectar ao MongoDB em mongodb://localhost:27017. Suba o banco com `docker compose up -d`, ou guarde as conversas em arquivos com `memory.session.store: file`." |
| Usuário ou senha errados | "O MongoDB recusou o usuário ou a senha." com os nomes das variáveis a conferir. |
| Driver não instalado | Pede `pip install -e .`. |

Banco fora do ar = **avisa e sai**, sem cair para arquivos em silêncio (um fallback silencioso gravaria conversas num lugar que você não espera). Se o banco cair no meio da conversa, só a gravação falha (vai para o log) e a conversa segue.

### docker-compose.yml {#compose kind=módulo file=docker-compose.yml}

Sobe `mongo:7` só em `127.0.0.1:27017` (a rede nunca vê o banco), com o volume `juniper-mongo-data` e *healthcheck*. Usuário e senha vêm do `.env`; sem eles o compose recusa subir. Para olhar os dados:

```bash
docker exec -it juniper-mongo mongosh -u <usuario> -p --authenticationDatabase admin juniper
> db.sessions.find({}, {history: 0})
```

Atenção ao banco: sem o `juniper` no fim, o `mongosh` abre o banco `test`, que está vazio.

## Configuração {#configuracao}

```yaml
memory:
  session:
    max_turns: 8          # turnos seus mantidos
    max_tokens: 6000      # orçamento estimado do histórico
    dir: ~/.juniper/sessions
    store: file           # file (padrão) ou mongo
  mongo:
    uri: mongodb://localhost:27017
    database: juniper
    timeout_ms: 3000      # quanto esperar pelo servidor antes de avisar que caiu
```

Usuário e senha ficam só no `.env` (ADR-003); o mesmo par é lido pelo compose. A senha é entregue ao driver à parte, sem ir na URI: `@`, `:` e `/` não precisam de escape. Referência completa em [Configuração](configuracao.html#memory).

## Limitações conhecidas {#limitacoes}

- Retoma só a mais recente; escolher outra pela lista (`list_sessions` já existe) fica para depois.
- Sem expiração e sem migração dos JSONs antigos: as conversas ficam até você apagar.
- A estimativa de tokens é aproximada (código e emoji podem pesar mais); o orçamento padrão tem folga.
- As chamadas ao Mongo são síncronas: com o banco fora do ar, cada gravação pode esperar até `timeout_ms` (3 s).
- O documento é substituído a cada turno: o que saiu do histórico sai também do banco.

## Testes {#testes}

```bash
pytest -m "not integration"     # sem banco: o que o CI roda
pytest -m integration           # contra o MongoDB local (docker compose up -d)
```

Os de integração rodam o mesmo contrato do `FileSessionStore` contra o `MongoSessionStore` (`tests/test_session_store_contract.py`), mais os casos próprios do Mongo (`tests/test_mongo_store.py`). Cada teste usa um banco descartável e o apaga no fim.
