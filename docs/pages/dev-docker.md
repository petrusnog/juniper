---
title: Ambiente Docker
group: Ambiente Local
order: 3
description: Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar logs e banco, e os problemas comuns.
status: guia
tags: docker compose dev desenvolvimento juniper-dev token brain mongo fake-llm watchfiles portas variáveis logs mongosh problemas
---

O passo a passo para subir o ambiente pela primeira vez está em [Primeiros passos](primeiros-passos.md). Esta página é a referência para o dia a dia.

> **Nota:** o compose de desenvolvimento é o arquivo **`docker-compose.dev.yml`**. Os outros dois (`docker-compose.prod.yml`, da produção, e `docker-compose.yml`, só com o MongoDB para o [modo local](install.md)) servem a outras situações e **não** criam a pasta `.juniper-dev/`. Sempre use `-f docker-compose.dev.yml`.

## Serviços {#servicos}

O `docker-compose.dev.yml` sobe a **mesma topologia da produção** (o mesmo `Dockerfile`, a mesma configuração do cérebro), com o código montado por volume. O projeto se chama `juniper-dev` e não toca nos volumes da produção.

| Contêiner | O que é | Porta na sua máquina |
|---|---|---|
| `juniper-dev-fix-perms` | Roda uma vez e termina (`Exited (0)` é o normal): acerta o dono de `.juniper-dev/`. | nenhuma |
| `juniper-dev-brain` | O servidor (`juniper serve`), alvo `dev` do `Dockerfile`, sem o código na imagem; o `watchfiles` o reinicia quando `brain/` ou `prompts/` mudam. | `127.0.0.1:8765` (WebSocket e `/health`), `127.0.0.1:8766` ([dashboard](dashboard.md)) |
| `juniper-dev-mongo` | MongoDB 7 com as memórias e as conversas. | `127.0.0.1:27018` |
| `juniper-dev-fake-llm` | LLM de mentira (`scripts/fake_ollama.py`), o último da fila de failover: sem chave, o ambiente ainda responde ecoando. | nenhuma |

Tudo é publicado só em `127.0.0.1`: nada do ambiente aparece na rede. A configuração do cérebro é `docker/config.dev.yaml` (modo `server`, memória no MongoDB, `fake-llm` no lugar do Ollama). O seu `config.yaml` entra como **camada base**: completa o que o `config.dev.yaml` não define (agendas do Google, música, voz), mas não muda a infraestrutura.

## Arquivos no host {#arquivos}

| Caminho | O que é |
|---|---|
| `.juniper-dev/state/token` | O token que os clientes usam para conectar. Criado no primeiro boot. |
| `.juniper-dev/logs/` | Os logs JSONL do cérebro ([Observabilidade](observabilidade.md)). |
| `.env` | Segredos: usuário e senha do MongoDB (obrigatórios), chaves de LLM e das integrações (opcionais). |
| `config.yaml` | A sua configuração pessoal (camada base). |
| `certs/` | Credenciais do Google montadas no contêiner, só leitura ([Integrações Google Cloud e Groq](integracoes-producao.md)). |

## Variáveis do compose {#variaveis}

Definidas no `.env` da raiz:

| Variável | Padrão | O que faz |
|---|---|---|
| `JUNIPER_MEMORY__MONGO__USERNAME` / `..._PASSWORD` | obrigatórias | Credenciais com que o banco é criado (e que o cérebro usa). |
| `JUNIPER_DEV_PORT` | `8765` | Porta do cérebro no host. |
| `JUNIPER_DEV_DASHBOARD_PORT` | `8766` | Porta do dashboard no host. |
| `JUNIPER_DEV_MONGO_PORT` | `27018` | Porta do MongoDB no host (para `mongosh` ou Compass). |
| `JUNIPER_BASE_CONFIG_FILE` | `./config.yaml` | O config pessoal usado como base. |
| `JUNIPER_ENV_FILE` | `./.env` | O `.env` que o cérebro lê. |
| `JUNIPER_CERTS_DIR` | `./certs` | A pasta de credenciais. |

Mudou a porta? O CLI precisa da mesma em `JUNIPER_DEPLOYMENT__SERVER__PORT`, e o app na tela de configuração.

## O que recarrega sozinho {#recarga}

| Você mudou | O que fazer |
|---|---|
| `brain/` ou `prompts/` | Nada. O servidor reinicia sozinho e os clientes reconectam. `logs -f brain` mostra cada reinício. |
| `.env` ou `docker/config.dev.yaml` | `docker compose -f docker-compose.dev.yml restart brain` |
| O seu `config.yaml` | `restart brain` |
| `requirements.txt` | `docker compose -f docker-compose.dev.yml up -d --build` |

## Acompanhar o que acontece {#acompanhar}

```bash
docker compose -f docker-compose.dev.yml logs -f brain     # o servidor
tail -f .juniper-dev/logs/*.jsonl                           # os eventos estruturados
docker compose -f docker-compose.dev.yml exec brain python -m brain logs -f   # o mesmo, formatado
```

Dentro do contêiner, qualquer comando administrativo roda com `docker compose -f docker-compose.dev.yml exec brain python -m brain <comando>` (por exemplo, `memory list` ou `logs -f`).

**Diário de mensagens (`message.log`).** Conta, camada por camada, o caminho de cada mensagem; vem desligado porque guarda o texto das conversas. Para ligar: `export JUNIPER_TRACE__ENABLED=true`, `restart brain` e use o CLI no mesmo terminal ([Observabilidade](observabilidade.md#message-log)).

**Olhar o banco:**

```bash
docker compose -f docker-compose.dev.yml exec mongo mongosh -u juniper -p uma-senha-qualquer --authenticationDatabase admin juniper
```

(use o usuário e a senha do seu `.env`; o Compass também conecta em `127.0.0.1:27018`).

**LLM de verdade:** ponha `JUNIPER_GROQ_API_KEY` ou `JUNIPER_DEEPINFRA_API_KEY` no `.env` e rode `restart brain`. Os provedores reais respondem primeiro; o `fake-llm` só entra se eles falharem.

## Parar e limpar {#limpar}

```bash
docker compose -f docker-compose.dev.yml down        # desliga; os dados ficam nos volumes
docker compose -f docker-compose.dev.yml down -v     # desliga e APAGA o banco e o modelo baixado
rm -rf .juniper-dev                                  # zera o token e os logs
```

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| **A pasta `.juniper-dev` não existe.** | Você subiu outro compose. Só o `docker-compose.dev.yml` a cria. Confira com `docker ps`: os contêineres devem se chamar `juniper-dev-*`. |
| **`Permission denied` ao gravar o token; `state/` vazio; o brain reinicia.** | `.juniper-dev/` pertence ao root. `down`, `sudo chown -R "$(id -u):$(id -g)" .juniper-dev` e `up -d`. O serviço `fix-perms` corrige isso sozinho nas versões atuais do compose. |
| **`.env` ou `config.yaml` viraram pastas.** | O Docker as criou porque não existiam na hora do `up`. `down`, `rm -rf .env config.yaml` (são pastas vazias), `./scripts/dev_setup.sh` e suba de novo. |
| **`defina JUNIPER_MEMORY__MONGO__USERNAME no .env`** | Falta o usuário ou a senha do MongoDB no `.env`. |
| **`unhealthy`, ou o brain não conecta ao Mongo.** | `logs brain` mostra o motivo. Quase sempre o usuário e a senha mudaram depois que o banco foi criado (o Mongo só os lê na criação do volume): `down -v` e suba de novo. |
| **Porta 8765, 8766 ou 27018 ocupada.** | Mude `JUNIPER_DEV_PORT`, `JUNIPER_DEV_DASHBOARD_PORT` ou `JUNIPER_DEV_MONGO_PORT` no `.env`. |
| **A resposta é só o eco da mensagem.** | É o `fake-llm`: nenhuma chave de LLM válida no `.env`, ou o provedor real falhou. |
| **Os arquivos de `.juniper-dev` são de outro usuário.** | O seu uid não é 1000. `export UID` antes de `up -d --build` (precisa reconstruir a imagem). |

## Para saber mais {#para-saber-mais}

- [Como contribuir](contribuindo.md) (Ambiente Local): O fluxo de trabalho (issue, branch, PR), as regras de arquitetura, os testes exigidos, as convenções de código e como manter a documentação em dia.
- [Primeiros passos](primeiros-passos.md) (Ambiente Local): Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
- [Ambiente mobile](mobile-dev.md) (Ambiente Local): Como preparar o Android SDK, rodar o app num emulador ou celular com recarga ao vivo, testar, regenerar os tipos do protocolo e gerar um APK.
- [Instalação sem Docker (modo local)](install.md) (Ambiente Local): O instalador da Juniper: o cérebro dentro do próprio terminal (modo local), sem contêineres, a partir de uma máquina limpa.
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
- [Visão geral da produção](producao.md) (Produção): Como a Juniper roda em produção num servidor qualquer: topologia, requisitos, modelo de segurança, configuração, dados e o roteiro das páginas seguintes.
- [Deploy contínuo](deploy-continuo.md) (Produção): Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca de versão, healthcheck e rollback automático.
