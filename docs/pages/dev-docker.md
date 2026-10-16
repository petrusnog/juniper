---
title: Ambiente de desenvolvimento com Docker
group: Começando
order: 2
description: Passo a passo para subir o brain e o MongoDB em contêineres, conversar pelo CLI remoto e desenvolver com reinício automático.
status: guia
tags: docker compose dev desenvolvimento tutorial juniper-dev token brain mongo fake-llm watchfiles cli remoto passo a passo problemas
---

Este guia leva do zero a uma conversa com o brain rodando em contêiner, com o código da sua máquina valendo na hora. A mesma topologia vai para a produção ([Produção](producao.html)): brain e MongoDB em contêineres, o cliente de fora, falando por WebSocket ([Protocolo](protocolo.html)).

> **Nota:** o compose de desenvolvimento é o arquivo **`docker-compose.dev.yml`**. Os outros dois (`docker-compose.prod.yml` e o `docker-compose.yml` só com o Mongo) são de outras situações e **não** criam a pasta `.juniper-dev/`. Sempre use `-f docker-compose.dev.yml`.

## Pré-requisitos {#pre-requisitos}

- **Docker** com o plugin **Compose v2** (`docker compose version`). Em Linux, seu usuário no grupo `docker`.
- **Python 3.11+** na sua máquina, só para o CLI e os testes ([Instalação](install.html)).
- Cerca de 3 GB livres (imagens, o Mongo e o modelo de embeddings, que baixa uma vez).

## 1. Prepare a pasta {#preparar}

Na raiz do repositório:

```bash
pip install -e ".[dev]"        # o comando `juniper` e os testes, na sua máquina
./scripts/dev_setup.sh         # cria o que o compose espera encontrar
```

O `scripts/dev_setup.sh` é seguro de repetir. Ele cria `.juniper-dev/state`, `.juniper-dev/logs` e `certs/`, copia `.env.example` para `.env` e `config.yaml.example` para `config.yaml` (se ainda não existem) e confere se o usuário e a senha do Mongo estão preenchidos.

Abra o `.env` e preencha **os dois valores do Mongo** (escolha qualquer um; o banco é criado com eles):

```bash
JUNIPER_MEMORY__MONGO__USERNAME=juniper
JUNIPER_MEMORY__MONGO__PASSWORD=uma-senha-qualquer
```

As chaves `JUNIPER_DEEPINFRA_API_KEY` e `JUNIPER_GROQ_API_KEY` são **opcionais**: sem elas o ambiente usa o `fake-llm`, um LLM de mentira que só ecoa a sua mensagem. Dá para testar a arquitetura inteira sem gastar nada.

Se o `dev_setup.sh` avisar que o seu uid não é 1000 (`id -u`), rode `export UID` no terminal antes do próximo passo. Assim os arquivos de `.juniper-dev/` ficam seus.

> **Atenção:** se você pular este passo e o Docker não achar `.juniper-dev/`, `.env` ou `config.yaml`, ele **cria sozinho**, como root (e `.env` e `config.yaml` viram *pastas*). Para `.juniper-dev/` o compose já se protege (o serviço `fix-perms` acerta o dono antes do brain subir), mas `.env` e `config.yaml` continuam dependendo deste passo. Veja [Problemas comuns](#problemas).

## 2. Suba o ambiente {#subir}

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml ps
```

Na primeira vez, o `--build` baixa as imagens e instala as dependências (alguns minutos). Espere `juniper-dev-brain` aparecer como **healthy** (até ~1 minuto) e `juniper-dev-mongo` como **healthy**. Os serviços:

| Contêiner | O que é | Porta no seu computador |
|---|---|---|
| `juniper-dev-fix-perms` | Roda uma vez e termina (`Exited (0)` é o normal): acerta o dono de `.juniper-dev/`. | nenhuma |
| `juniper-dev-brain` | O servidor (`juniper serve`), com o código montado por volume. | `127.0.0.1:8765` |
| `juniper-dev-mongo` | MongoDB com as memórias e conversas. | `127.0.0.1:27018` |
| `juniper-dev-fake-llm` | LLM de mentira, o último da fila de failover. | nenhuma |

Se o `ps` mostrar `unhealthy` ou o brain reiniciando, veja os logs: `docker compose -f docker-compose.dev.yml logs brain`.

## 3. Confira que está de pé {#conferir}

```bash
curl -s http://127.0.0.1:8765/health
ls .juniper-dev/state          # deve ter o arquivo `token`
python scripts/smoke_server.py --token-file .juniper-dev/state/token "oi, tudo bem?"
```

- O `/health` devolve `{"status": ..., "memory": ...}`. Enquanto o modelo de embeddings baixa na primeira vez, aparece `"status": "degraded", "memory": "warming_up"` (é normal, a conversa já funciona, sem consultar memória). Quando termina, vira `ok` / `ready`.
- A pasta `.juniper-dev/` **só existe depois** deste `up` com o `docker-compose.dev.yml`. Dentro dela, `state/token` é o token que os clientes usam, e `logs/` tem os eventos do brain.
- O `smoke_server.py` conecta, conversa e imprime a resposta chegando em pedaços. Com o `fake-llm`, a resposta é o eco da sua mensagem.

## 4. Converse pelo CLI {#cli}

O CLI da sua máquina vira cliente remoto quando está em `deployment.mode: server`. Para **não mexer no seu `config.yaml`**, passe isto só no comando (rode da raiz do repositório):

```bash
export JUNIPER_DEPLOYMENT__MODE=server
export JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE="$PWD/.juniper-dev/state/token"

juniper "oi"                # uma mensagem
juniper                     # conversa interativa
juniper --resume            # retoma a última conversa
juniper memory list         # as memórias, lidas do servidor
juniper tools               # as tools do servidor
```

Para voltar ao brain local (sem Docker), é só `unset JUNIPER_DEPLOYMENT__MODE JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE`. O erro *"Não achei o token do servidor em ~/.juniper/token"* significa que o CLI está em modo `server` mas a variável do token não foi definida (ou o ambiente não foi iniciado): veja [Problemas comuns](#problemas).

## 5. Desenvolva {#desenvolver}

| Você mudou | O que fazer |
|---|---|
| Algo em `brain/` ou `prompts/` | Nada. O servidor reinicia sozinho (watchfiles) e o CLI reconecta. `logs -f brain` mostra cada reinício. |
| `.env` ou `docker/config.dev.yaml` | `docker compose -f docker-compose.dev.yml restart brain` |
| `requirements.txt` | `docker compose -f docker-compose.dev.yml up -d --build` |
| O seu `config.yaml` pessoal | `restart brain` (ele entra como base: agenda do Google, persona etc.) |

Os testes rodam na sua máquina, sem Docker: `pytest -m "not integration"`.

**Acompanhar o que acontece**

```bash
docker compose -f docker-compose.dev.yml logs -f brain     # servidor
tail -f .juniper-dev/logs/*.jsonl                           # eventos estruturados
```

**Diário de mensagens (`message.log`):** conta, camada por camada, o caminho de cada mensagem. Desligado por padrão. Para ligar neste ambiente: `export JUNIPER_TRACE__ENABLED=true`, rode `restart brain` e use o CLI no mesmo terminal. O arquivo fica em `logs/message.log` (veja [Observabilidade](observabilidade.html)).

**Olhar o banco**

```bash
docker compose -f docker-compose.dev.yml exec mongo mongosh -u juniper -p uma-senha-qualquer --authenticationDatabase admin
```

(use o usuário e a senha do seu `.env`; também dá para apontar o Compass para `127.0.0.1:27018`).

**LLM de verdade:** ponha `JUNIPER_DEEPINFRA_API_KEY` (e/ou a do Groq) no `.env` e rode `restart brain`. Os provedores reais passam a responder primeiro; o `fake-llm` só entra se eles falharem.

## 6. Parar e limpar {#limpar}

```bash
docker compose -f docker-compose.dev.yml down        # desliga; os dados ficam nos volumes
docker compose -f docker-compose.dev.yml down -v     # desliga e APAGA o banco e o modelo baixado
rm -rf .juniper-dev                                  # zera o token e os logs (um novo token é criado no próximo up)
```

Depois de apagar o token, o CLI e o `smoke_server.py` precisam ler o novo arquivo.

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| **A pasta `.juniper-dev` não existe.** | Você subiu outro compose. Só o `docker-compose.dev.yml` a cria. Rode `docker compose -f docker-compose.dev.yml up -d --build`. Confira com `docker ps`: os contêineres devem se chamar `juniper-dev-*`. |
| **"Não achei o token do servidor em ~/.juniper/token"** | O CLI está em modo `server` e procura o token no caminho padrão. Defina `JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE="$PWD/.juniper-dev/state/token"` ([passo 4](#cli)) ou volte ao modo local. |
| **`Permission denied` ao gravar o token; `state/` vazio; o brain reinicia.** | `.juniper-dev/` é do root (`ls -ln .juniper-dev` mostra `0 0`), porque o Docker a criou numa versão antiga do compose. `down`, `sudo chown -R "$(id -u):$(id -g)" .juniper-dev` e `up -d`. Com o serviço `fix-perms` atual isso se corrige sozinho no `up`. |
| **`.env` ou `config.yaml` viraram pastas.** | O Docker as criou porque não existiam. `down`, `rm -rf .env config.yaml` (são pastas vazias), `./scripts/dev_setup.sh` e suba de novo. |
| **`defina JUNIPER_MEMORY__MONGO__USERNAME no .env`** | Falta o usuário ou a senha do Mongo no `.env` (passo 1). Se o banco já tinha sido criado com outros valores, use `down -v`. |
| **Porta 8765 ou 27018 ocupada.** | Defina `JUNIPER_DEV_PORT` / `JUNIPER_DEV_MONGO_PORT` no `.env`. O CLI precisa da mesma porta em `JUNIPER_DEPLOYMENT__SERVER__PORT`. |
| **`unhealthy`, ou o brain não conecta ao Mongo.** | `logs brain` mostra o motivo. Quase sempre é usuário/senha diferentes dos do banco já criado: `down -v` e suba de novo. |
| **A resposta é só o eco da mensagem.** | É o `fake-llm`: nenhuma chave de LLM válida no `.env`, ou o provedor real falhou. Preencha as chaves e faça `restart brain`. |
| **Os arquivos de `.juniper-dev` são de outro usuário.** | O seu uid não é 1000. `export UID` antes de `up -d --build` (precisa reconstruir a imagem). |
