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
   CLI remoto                ──┼── Tailscale (WebSocket) ──►│  VPS Contabo, US East
   (futuro: celular, voz)    ──┘                            │   ├─ brain  (Docker)
                                                            │   └─ mongo  (Docker, volume)
   GitHub Actions ── merge no master ── deploy (SSH pela tailnet) ──┘
```

## Hospedagem {#hospedagem}

- **Contabo Cloud VPS 4, US East (Carlstadt, NJ), x86:** 4 vCPU, 8 GB de RAM, 100 GB de SSD, Ubuntu 24.04 ([ADR-013, adendo da T-081](adr-013.html)). O cérebro chama o LLM várias vezes por turno e o cliente só uma, então a VPS fica nos EUA, perto do LLM. A medição deu ~51 ms até a DeepInfra e primeiro token do LLM em ~0,5 s. A Hetzner em Ashburn custava de US$ 20 a 73 por mês e ficou de fora.
- **Tamanho:** ponto de partida de 2 vCPU e 4 GB de RAM (Mongo + embeddings + brain somam 1,5 a 2 GB). O tipo exato é fixado depois da T-081.
- **Custo:** **US$ 8,50 por mês** (US$ 6,60 + US$ 1,90 da região), cobrado por mês, mais o armazenamento de backups *(a confirmar)*. O DeepInfra é cobrado por uso.
- Ubuntu LTS; acesso de emergência pelo console VNC do painel do Contabo.

## Composição (Docker Compose) {#compose}

| Serviço | Imagem | Notas |
|---|---|---|
| `brain` | `ghcr.io/petrusnog/juniper:<sha>` | Python 3.12, usuário sem privilégio, `juniper serve`, reinício automático, *healthcheck* em `GET /health`. |
| `telegram` | a mesma imagem do brain | **Opt-in** (`--profile telegram`). O bot do Telegram, um cliente do brain: não publica porta, não recebe o `.env`, só lê o token dos clientes ([Telegram](telegram.html#vps)). |
| `mongo` | `mongo:7` | Só na rede interna do compose (sem porta publicada), volume nomeado `juniper-mongo-data`, usuário e senha do `.env`. |

A porta `8765` do brain é publicada **apenas no endereço Tailscale** da VPS, nunca em `0.0.0.0`. O `.env` e a chave do Google são montados do servidor (somente leitura); **segredos nunca entram na imagem**. O modelo de embeddings fica no volume `juniper-models` (baixa uma vez). O modo é `deployment.mode: server` ([Configuração](configuracao.html#deployment)).

## Ambiente de desenvolvimento (Docker) {#dev}

> **Passo a passo:** o [tutorial do ambiente de desenvolvimento](dev-docker.html) tem os comandos completos, do `./scripts/dev_setup.sh` ao CLI remoto, e os problemas comuns.

Para desenvolver, o `docker-compose.dev.yml` sobe a **mesma topologia da produção** (brain e Mongo em contêineres na mesma rede, o mesmo `Dockerfile`, a mesma configuração) com o **código montado por volume**: mexeu em `brain/` ou `prompts/`, o servidor reinicia sozinho (watchfiles) e os clientes reconectam. Não há `build` a cada mudança.

```bash
./scripts/dev_setup.sh                                    # cria .juniper-dev/, .env e config.yaml (rode antes do primeiro up)
docker compose -f docker-compose.dev.yml up -d --build   # precisa do .env com JUNIPER_MEMORY__MONGO__USERNAME/PASSWORD
docker compose -f docker-compose.dev.yml logs -f brain   # mostra cada reinício
python scripts/smoke_server.py --token-file .juniper-dev/state/token "oi"
```

| Serviço | O que é |
|---|---|
| `brain` | Alvo `dev` do `Dockerfile`: sem o código na imagem, `watchfiles` reiniciando `juniper serve`. Porta `127.0.0.1:8765`. |
| `mongo` | O mesmo `mongo:7` da produção, com volume próprio. Publicado em `127.0.0.1:27018` (para `mongosh`/Compass). |
| `fake-llm` | LLM de mentira (`scripts/fake_ollama.py`), o **último da fila de failover**: sem chave de LLM o ambiente ainda responde (ecoando). Com chaves no `.env`, DeepInfra e Groq respondem primeiro. |

- **Estado no host:** o token dos clientes e os logs ficam em `.juniper-dev/` (fora do git), com o seu usuário como dono: o CLI remoto lê o token dali.
- **Não reinicia sozinho:** `requirements.txt` (use `up -d --build`), `docker/config.dev.yaml` e `.env` (use `restart brain`).
- **O que a produção tem a mais** (Tailscale, GitHub Actions, backup) não faz sentido numa máquina só e fica de fora. O projeto se chama `juniper-dev`: não encosta nos volumes de produção nem no Mongo do `docker-compose.yml`.

## Produção na sua máquina {#local}

O mesmo `docker-compose.prod.yml` da VPS roda no seu computador, com a porta publicada só em `127.0.0.1`. Serve para testar a arquitetura real (brain e Mongo em contêineres, cliente de fora) antes do deploy. Os arquivos: `Dockerfile`, `.dockerignore`, `docker-compose.prod.yml` e `docker/config.prod.yaml` (a configuração do contêiner, sem segredos).

```bash
# .env com JUNIPER_MEMORY__MONGO__USERNAME e JUNIPER_MEMORY__MONGO__PASSWORD (e as chaves de LLM)
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps        # o brain fica "healthy"

# o token dos clientes é criado no primeiro boot, dentro do volume juniper-state
docker exec juniper-brain cat /home/juniper/.juniper/state/token > /tmp/token
python scripts/smoke_server.py --health
python scripts/smoke_server.py --token-file /tmp/token "oi, tudo bem?"
python scripts/smoke_server.py --token-file /tmp/token --resume "do que falávamos?"
```

- **Sem chave de LLM:** `python scripts/fake_ollama.py` sobe um Ollama de mentira (ecoa a mensagem). No `.env`, `JUNIPER_LLM__PRIORITY=["ollama"]` e `JUNIPER_LLM__OLLAMA__URL=http://host.docker.internal:11999` (no Linux, o serviço `brain` precisa de `extra_hosts: host.docker.internal:host-gateway`).
- **Variáveis do compose:** `JUNIPER_IMAGE` (imagem do brain; padrão `juniper-brain:local`, construída ali), `TAILSCALE_IP` (onde publicar a porta; padrão `127.0.0.1`), `JUNIPER_CONFIG_FILE`, `JUNIPER_ENV_FILE` e `JUNIPER_CERTS_DIR` (de onde montar a configuração, o `.env` e a chave do Google, sempre somente leitura).
- **Os dados ficam nos volumes** (`juniper-mongo-data`, `juniper-state`, `juniper-logs`, `juniper-models`): reiniciar ou reconstruir o brain não perde conversas nem memórias. `down -v` apaga tudo.
- O projeto se chama `juniper-prod`, então não mexe no banco do `docker-compose.yml` de desenvolvimento.

## Agenda do Google na VPS {#google-vps}

As tools do Google Calendar rodam **no cérebro**, então qualquer cliente (CLI, Telegram, desktop) as usa. Na VPS faltam dois arquivos seus, que **nunca** vão para o repositório nem para a imagem: a **chave da conta de serviço** ([Google Calendar](google-calendar.html)) e a **configuração pessoal** com os IDs das agendas.

O compose monta o seu `config.yaml` como uma **camada base**: ele só completa o que o `config.prod.yaml` não define. O que a produção define (`deployment`, `memory`, `llm.priority`) sempre vence, mesmo que o seu arquivo diga outra coisa.

No seu computador (WSL), com o IP da VPS no Tailscale:

```bash
scp ~/.juniper/certs/gcp-key.json root@100.x.y.z:~/juniper/certs/gcp-key.json
scp ~/.juniper/config.yaml root@100.x.y.z:~/juniper/config.yaml
```

Na VPS, dentro de `~/juniper`:

```bash
# o contêiner roda como o uid 10001: sem isto ele não consegue LER os arquivos (mesmo com modo 600)
chown 10001:10001 certs/gcp-key.json config.yaml
chmod 600 certs/gcp-key.json config.yaml
echo "JUNIPER_BASE_CONFIG_FILE=./config.yaml" >> .env
docker compose -f docker-compose.prod.yml up -d      # recria o brain com a nova camada
docker compose -f docker-compose.prod.yml logs --tail=20 brain
```

Teste pelo CLI remoto (no seu computador): `juniper "o que tenho amanhã na agenda?"`.

| Sintoma | Causa e solução |
|---|---|
| `Permission denied` ao ler a chave ou o `.env` | O dono do arquivo não é o uid 10001. Rode o `chown 10001:10001` acima. |
| A Juniper diz que não tem agenda configurada | O `config.yaml` não chegou: confira `JUNIPER_BASE_CONFIG_FILE` no `.env` e se o arquivo é um **arquivo** (se o Docker criou uma pasta, apague-a e copie de novo). |
| Erro de acesso à agenda | A agenda não está compartilhada com a conta de serviço (veja [Google Calendar](google-calendar.html)). |
| Horário errado nos eventos | `agent.timezone` vem do `config.prod.yaml` (`America/Sao_Paulo`). |

## Teste de capacidade (T-081) {#capacidade}

Antes de fixar a VPS, a T-081 mede se a Juniper cabe nela e quão rápida ela fica. O alvo é o menor tamanho que roda brain, MongoDB e o modelo de embeddings com folga. O script é o `scripts/bench_server.py` (só biblioteca padrão) e roda **na VPS**.

> **Nota:** o provedor escolhido (Contabo, US East) e os números medidos estão no adendo da T-081 do [ADR-013](adr-013.html). Este roteiro vale para qualquer VPS Ubuntu.

### Preparar a VPS {#preparar-vps}

No seu computador, com o IP da VPS (o Contabo envia a senha de root por e-mail):

```bash
ssh-copy-id root@<IP>          # copia a sua chave pública
ssh root@<IP>
```

Na VPS, nesta ordem (**não feche a primeira sessão antes de testar a chave em outra**):

```bash
apt update && apt -y upgrade

# 1. Só chave, sem senha. O arquivo se chama 00-: o sshd vale o PRIMEIRO valor que lê, e imagens de nuvem
#    trazem um 50-cloud-init.conf com `PasswordAuthentication yes` (um 99- perderia para ele)
printf 'PasswordAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/00-juniper.conf
sshd -t && systemctl restart ssh      # teste `ssh root@<IP>` em OUTRO terminal antes de seguir
sshd -T | grep -Ei 'passwordauthentication|permitrootlogin'   # deve mostrar `no` e `without-password`

# 2. Docker e Tailscale (instaladores oficiais)
curl -fsSL https://get.docker.com | sh
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --hostname=juniper-brain      # abre uma URL: entre na sua conta Tailscale

# 3. Firewall: nada de entrada pública; só o que vem pelo Tailscale
apt -y install ufw
ufw default deny incoming && ufw default allow outgoing
ufw allow in on tailscale0
ufw allow 22/tcp               # temporário
ufw --force enable
```

Com o Tailscale também no seu computador, confira `ssh root@juniper-brain` (ou o IP `100.x.y.z`). Só então feche o SSH público: `ufw delete allow 22/tcp`. O Docker publica portas por fora do `ufw`: por isso o compose publica a 8765 **só no IP do Tailscale** (`TAILSCALE_IP`) e o Mongo não publica porta nenhuma.

### Subir a Juniper {#subir-vps}

```bash
git clone https://github.com/petrusnog/juniper.git && cd juniper    # repositório privado? use scp ou um token só de leitura
cp .env.example .env
# edite o .env: JUNIPER_MEMORY__MONGO__USERNAME/PASSWORD e a chave do DeepInfra (temporária)
echo "TAILSCALE_IP=$(tailscale ip -4)" >> .env
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps        # o brain fica "healthy"
```

### Medir {#medir}

```bash
JUNIPER_DEEPINFRA_API_KEY=... python3 scripts/bench_server.py --rounds 5 --llm --stack --out ~/bench-1.json
```

Faça **uma vez com a pilha parada** (sem `--stack`), **outra com a pilha de pé e uma conversa em andamento**, e repita em horários diferentes (manhã, tarde, noite). O que o script mede:

| Item | O que responde | Sinal de alerta |
|---|---|---|
| CPU: variação entre rodadas e *steal* | O desempenho é previsível numa VPS compartilhada? | variação acima de ~20% ou *steal* acima de ~5% |
| Disco: escrita e `fsync` | O Mongo grava sem engasgar? | `fsync` com mediana acima de ~10 ms |
| Memória livre com a pilha de pé | Cabe com folga? | menos de ~1 GB livre |
| Rede até DeepInfra e Groq | O trajeto até o LLM é curto? | RTT acima de ~100 ms |
| LLM: primeiro token e tempo total | Quanto a localização custa numa resposta real? | primeiro token bem acima do que o DeepInfra entrega de perto |

A chave do LLM só vai pelo ambiente (nunca por argumento) e não é gravada no resultado. Ao terminar, mande o conteúdo dos `bench-*.json`: o veredito (tamanho e provedor) sai deles.

## Tailscale {#tailscale kind=conceito}

O Tailscale é uma **rede privada entre os seus dispositivos**: a VPS e o seu PC recebem endereços privados (e nomes, como `juniper-brain.<tailnet>.ts.net`) e o tráfego é cifrado. Para o usuário: instale o Tailscale e entre na mesma conta em cada dispositivo.

- **Etiquetas e ACL:** a VPS é `tag:juniper-server`, os clientes `tag:juniper-client`, o runner do CI `tag:ci`; a política só deixa clientes e CI alcançarem o servidor na porta `8765` e no SSH.
- **Firewall no servidor (`ufw`):** `default deny incoming` e `allow in on tailscale0`; a porta 22 pública fechada. O SSH só pela tailnet.
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

`GET /health` distingue *saudável*, *degradado* (ex.: Mongo fora, o brain opera só com a sessão) e *fora*. Desde o T-086 ele já informa a memória: `{"status": "ok", "memory": "ready"}` e, enquanto o modelo de embeddings baixa, `{"status": "degraded", "memory": "warming_up"}` (ainda com HTTP 200, para o *healthcheck* não reiniciar o contêiner). Os logs JSONL ([Observabilidade](observabilidade.html#log-tecnico)) ficam num volume, um arquivo por dia com todas as sessões, incluindo as requisições HTTP aos provedores e à agenda. O próprio servidor compacta os de ontem e apaga os com mais de 3 dias, todo dia às 00:10 ([rotação](observabilidade.html#rotacao)). Para ver: `docker compose -f docker-compose.prod.yml exec brain juniper logs -f`. Alertas ativos ficam fora da Sprint 3.

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
| T-082 | Provisionamento, hardening, Tailscale e [runbook](runbook-vps.html) |
| T-083 | Backup e restauração do Mongo |
| T-084 | `deploy.yml` com rollback |
| T-085 | Saúde e logs em produção |
