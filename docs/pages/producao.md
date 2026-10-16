---
title: Produção
group: Produção
order: 1
description: Como a Juniper vai rodar numa VPS: topologia, Tailscale, deploy automático com rollback, backup e custos.
status: proposto
tags: deploy vps hetzner tailscale docker compose ci cd github actions rollback backup mongodump ashburn custos servidor
---

> **Nota:** esta página descreve o que está **proposto** (Sprint 3, [ADR-013](adr-013.html) e [ADR-007](adr-007.html)). Nada disto está em produção ainda: o cérebro hoje roda dentro do CLI. Itens marcados *a confirmar* dependem de decisão ou de medição (T-081).

## Visão geral {#visao-geral}

O cérebro roda numa **VPS** sempre ligada e os clientes (CLI remoto, app desktop) falam com ele por um único WebSocket ([Protocolo](protocolo.html)). A VPS **não expõe nenhuma porta pública**: o acesso é pela rede privada do **Tailscale**. O que fica no servidor (conversas, memórias, perfil) é pessoal, então cada passo prioriza segurança e recuperação.

```text
   PC (Electron + executor)  ──┐                            ┌─ DeepInfra / Groq (LLM, EUA)
   CLI remoto                ──┼── Tailscale (WebSocket) ──►│  VPS Hetzner, Ashburn
   (futuro: celular, voz)    ──┘                            │   ├─ brain  (Docker)
                                                            │   └─ mongo  (Docker, volume)
   GitHub Actions ── merge no master ── deploy (SSH pela tailnet) ──┘
```

## Hospedagem {#hospedagem}

- **Hetzner Cloud em Ashburn (EUA), x86.** O cérebro chama o LLM várias vezes por turno e o cliente só uma; como o DeepInfra roda nos EUA, a VPS deve ficar **perto do LLM**, não perto do usuário. A conta em RTTs está no ADR-013; a T-081 mede para confirmar.
- **Tamanho:** ponto de partida de 2 vCPU e 4 GB de RAM (Mongo + embeddings + brain somam 1,5 a 2 GB). O tipo exato é fixado depois da T-081.
- **Custo previsto:** ~US$ 4 a 5 por mês pela VPS, mais o armazenamento de backups *(a confirmar)*. O DeepInfra é cobrado por uso.
- Ubuntu LTS; acesso de emergência pelo console web da Hetzner.

## Composição (Docker Compose) {#compose}

| Serviço | Imagem | Notas |
|---|---|---|
| `brain` | `ghcr.io/petrusnog/juniper:<sha>` | Python 3.12, usuário sem privilégio, `juniper serve`, reinício automático, *healthcheck* em `GET /health`. |
| `mongo` | `mongo:7` | Só em `127.0.0.1`, volume nomeado `juniper-mongo-data`, usuário e senha do `.env`. |

A porta `8765` do brain é publicada **apenas no endereço Tailscale** da VPS, nunca em `0.0.0.0`. O `.env` e a chave do Google são montados do servidor (somente leitura); **segredos nunca entram na imagem**. O modo é `deployment.mode: server` ([Configuração](configuracao.html#deployment)).

## Tailscale {#tailscale kind=conceito}

O Tailscale é uma **rede privada entre os seus dispositivos**: a VPS e o seu PC recebem endereços privados (e nomes, como `juniper-brain.<tailnet>.ts.net`) e o tráfego é cifrado. Para o usuário: instale o Tailscale e entre na mesma conta em cada dispositivo.

- **Etiquetas e ACL:** a VPS é `tag:juniper-server`, os clientes `tag:juniper-client`, o runner do CI `tag:ci`; a política só deixa clientes e CI alcançarem o servidor na porta `8765` e no SSH.
- **Firewall da Hetzner:** sem nenhuma regra de entrada. O SSH só pela tailnet.
- **Autenticação:** o token estático do ADR-007 por cima da rede: duas travas independentes.
- **Limite:** dispositivos que não aceitam instalar o Tailscale (TV, carro) não alcançam a VPS; é um problema de futuro.

## Deploy automático {#deploy}

Cada PR mergeado no `master` vira deploy, sem passo manual (`.github/workflows/deploy.yml`):

```text
PR mergeado no master
  → pytest verde (tests.yml)
  → build da imagem e push para o GHCR (tag = SHA)
  → o runner entra na tailnet com uma chave temporária (nó efêmero, tag:ci)
  → SSH na VPS → mongodump de segurança
  → grava o SHA atual como "anterior" e troca para o novo
  → docker compose pull && docker compose up -d      (nunca down -v)
  → espera o healthcheck (até 90 s)
       ok     → fim
       falhou → volta para a imagem anterior; o job falha e avisa
```

- **Gatilho seguro:** só roda depois que o `pytest` passou no mesmo commit.
- **Um deploy por vez;** um novo merge espera a vez.
- **Rollback automático** para o SHA anterior; **manual** para um SHA específico (`JUNIPER_IMAGE=...:<sha> docker compose up -d`).
- **O banco não é tocado:** o deploy nunca apaga volumes, e mudanças de esquema precisam ser compatíveis para trás (o rollback usa os dados como estão).
- **Conexões abertas:** ao reiniciar, o brain fecha as conexões com o código `1012` e os clientes reconectam sozinhos ([Protocolo](protocolo.html#reconexao)).

### Segredos {#segredos}

| Segredo | Onde fica |
|---|---|
| Chaves de LLM, usuário e senha do Mongo | `.env` no servidor (`600`) |
| Chave da conta de serviço do Google | `certs/gcp-key.json` no servidor (`600`) |
| Token dos clientes | `~/.juniper/token` no servidor; cofre do sistema no cliente |
| Chave SSH de deploy, credenciais OAuth do Tailscale | GitHub Secrets (`DEPLOY_SSH_KEY`, `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`) |

## Dados: backup e restauração {#backup}

- `mongodump` diário no servidor, comprimido, com retenção de 7 diários e 4 semanais *(a confirmar)*.
- **Cópia fora do servidor** (um bucket barato com `rclone`, ou outro destino a definir) e **backup sob demanda antes de cada deploy**.
- **Restauração testada de verdade** num Mongo vazio, conferindo com `juniper memory list`: um backup que nunca foi restaurado não conta.

## Observabilidade {#observabilidade-producao}

`GET /health` distingue *saudável*, *degradado* (ex.: Mongo fora, o brain opera só com a sessão) e *fora*. Os logs JSONL ([Observabilidade](observabilidade.html)) ficam num volume, com rotação e limite de tamanho. Alertas ativos ficam fora da Sprint 3.

## Riscos aceitos {#riscos}

- Servidor único, sem alta disponibilidade (rebuild pelo runbook + restauração do backup).
- Dependência do Tailscale, do DeepInfra e do GitHub (mitigada pelo failover de LLM e pelo deploy manual).
- Dados pessoais numa VPS: nenhuma porta pública, token, usuário sem privilégio, permissões restritas. Criptografia em repouso *(a avaliar)*.
- Deploy sem revisão humana no último passo: `pytest` obrigatório, *healthcheck*, rollback e backup pré-deploy.

## Tasks da Sprint 3 {#tasks}

| Task | O que entrega |
|---|---|
| T-080 | `Dockerfile` do brain e compose de produção |
| T-081 | Teste de capacidade e latência; fixa o tipo da VPS |
| T-082 | Provisionamento, hardening, Tailscale e runbook |
| T-083 | Backup e restauração do Mongo |
| T-084 | `deploy.yml` com rollback |
| T-085 | Saúde e logs em produção |
