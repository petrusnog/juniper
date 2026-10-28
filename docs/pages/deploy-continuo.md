---
title: Deploy contínuo
group: Produção
order: 6
description: Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca de versão, healthcheck e rollback automático.
status: guia
tags: deploy contínuo ci cd github actions ghcr imagem workflow deploy.yml deploy.sh rollback healthcheck segredos oauth tailscale ssh known_hosts fork
---

Com o servidor de pé ([Deploy do cérebro numa VPS](deploy-vps.html)), o deploy contínuo leva cada merge em `master` para a produção sem passo manual ([ADR-013](adr-013.html), seção 5). É opcional: sem ele, atualize à mão ([Operação](operacao.html#atualizar)).

## Como funciona {#fluxo}

```text
PR mergeado em master
  → tests.yml verde no mesmo commit
  → deploy.yml: build da imagem (alvo prod) e push para ghcr.io/<owner>/juniper:<sha>
  → o runner entra na rede privada como nó temporário (tag:ci)
  → SSH na VPS: ./scripts/deploy.sh <sha>
       → git: atualiza os arquivos do repositório para o commit (.env, config.yaml e certs/ ficam)
       → backup da memória (se o MongoDB estiver de pé); se o backup falhar, o deploy é cancelado
       → grava JUNIPER_IMAGE=<imagem do sha> no .env (o valor anterior é o destino do rollback)
       → docker compose pull && docker compose up -d        (nunca down -v)
       → espera o brain ficar "healthy" (até 90 s)
            ok     → fim
            falhou → volta para a imagem anterior; o job falha
```

- **Gatilho seguro:** só roda depois que o `pytest` passou no mesmo commit (`workflow_run` do `tests.yml`); também pode ser disparado à mão (*Run workflow*).
- **Um deploy por vez:** o workflow e o próprio script serializam.
- **O banco não é tocado:** volumes nunca são apagados, e mudanças de esquema precisam ser compatíveis para trás (o rollback usa os dados como estão).
- **Conexões abertas:** ao reiniciar, o cérebro fecha as conexões com o código `1012` e os clientes reconectam sozinhos.

Os arquivos: `.github/workflows/deploy.yml` (build e chamada remota) e `scripts/deploy.sh` (o que roda na VPS, testado com `git` e `docker` falsos em `tests/test_deploy_script.py`).

## Credenciais do CI {#credenciais}

São duas credenciais independentes, ambas só em **GitHub Secrets** (*Settings → Secrets and variables → Actions → New repository secret*). Use **repository secrets**, não *environment secrets*: o workflow não declara `environment:`.

### 1. Entrada na rede privada {#ci-rede}

Com o Tailscale, o runner entra como um nó **efêmero** a cada execução, por um cliente OAuth (que não expira, ao contrário de uma chave de autenticação comum):

1. Painel do Tailscale → *Settings → Trust credentials* (em versões antigas, *OAuth clients*) → nova credencial OAuth.
2. Escopos: **Keys → Auth Keys → Write** e **Devices → Core → Write**. Nenhum outro.
3. Etiqueta: `tag:ci` (a política do [passo 5.2](deploy-vps.html#rede) já permite que ela alcance o SSH da VPS).
4. Crie os segredos `TS_OAUTH_CLIENT_ID` e `TS_OAUTH_SECRET`.

Com outra VPN, troque o passo *Entra na tailnet* do `deploy.yml` pelo equivalente da sua VPN; o resto não muda.

### 2. Chave SSH exclusiva {#ci-ssh}

Uma chave só para o CI, separada da sua, para poder revogá-la sem mexer no seu acesso. Na VPS, como `deploy`:

```bash
ssh-keygen -t ed25519 -f ~/juniper-ci -N "" -C "juniper-ci"
cat ~/juniper-ci.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
cat ~/juniper-ci          # copie TUDO (com BEGIN e END) para o segredo DEPLOY_SSH_KEY
shred -u ~/juniper-ci ~/juniper-ci.pub
echo "$(tailscale ip -4) $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"   # vira DEPLOY_KNOWN_HOSTS
```

| Segredo | Valor |
|---|---|
| `DEPLOY_SSH_KEY` | A chave **privada** `juniper-ci`, com uma linha em branco no final. |
| `DEPLOY_HOST` | O `<IP-PRIVADO>` da VPS (um IP é mais robusto que um nome dentro do runner). |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_KNOWN_HOSTS` | A linha `<IP-PRIVADO> ssh-ed25519 AAAA...` impressa acima. Ela precisa começar pelo **mesmo texto** de `DEPLOY_HOST`. |

## Imagem num fork {#fork}

O workflow publica a imagem em `ghcr.io/<owner>/juniper`, onde `<owner>` é o dono do repositório que roda o CI. O `scripts/deploy.sh` monta o nome da imagem a partir da variável `JUNIPER_REPO_IMAGE`; o valor padrão aponta para o repositório original. **Num fork**, ajuste o padrão em `scripts/deploy.sh` para `ghcr.io/<owner>/juniper` (é uma linha), ou exporte `JUNIPER_REPO_IMAGE` no ambiente em que o script roda.

## Primeiro deploy {#primeiro-deploy}

O `workflow_run` só vale para workflows que já estão em `master`, e o CI chama um script que precisa estar na VPS. Por isso a primeira vez é manual:

1. Garanta que `deploy.yml` e `scripts/deploy.sh` estão em `master` (e o ajuste do [fork](#fork), se for o caso).
2. Na VPS, como `deploy`: `cd ~/juniper && git pull origin master`.
3. No GitHub: *Actions → Deploy → Run workflow*, na branch `master`.
4. O pacote do GHCR nasce **privado**: o primeiro `pull` na VPS falha com `unauthorized`. Em *Packages → juniper → Package settings → Change visibility*, torne-o público (a imagem não tem segredos) ou faça `docker login ghcr.io` na VPS com um token de leitura de pacotes. Rode o workflow de novo.
5. Na VPS: `docker compose -f docker-compose.prod.yml ps` (brain `healthy`) e `grep JUNIPER_IMAGE .env` (o SHA novo).
6. Em *Settings → Branches*, exija o check **pytest** em `master`: com deploy automático, um merge com teste vermelho iria direto para a produção.

A partir daí, cada merge em `master` faz o deploy sozinho (a aba *Actions* mostra o andamento).

## Rollback manual {#rollback}

O rollback automático só cobre o que derruba o *healthcheck*. Para voltar a uma versão específica, na VPS:

```bash
cd ~/juniper && ./scripts/deploy.sh <sha-completo-de-40-caracteres>
```

O script faz o mesmo ciclo (backup, troca, *healthcheck*) para o SHA pedido.

## Girar as credenciais {#girar}

Gere um novo cliente OAuth (ou uma nova chave SSH), atualize o segredo no GitHub, rode um deploy manual (*Run workflow*) e só então revogue o antigo (no Tailscale, em *Trust credentials*; na VPS, removendo a linha de `~/.ssh/authorized_keys`).

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| O CI não entra na rede privada | Escopo ou etiqueta do cliente OAuth errados, ou `tag:ci` ausente no `tagOwners`. |
| `DEPLOY_KNOWN_HOSTS não tem uma linha...` | O início da linha não é o mesmo texto de `DEPLOY_HOST`. Use o mesmo IP nos dois. |
| `invalid format` na chave SSH | Falta a linha em branco no final do segredo, ou faltou copiar `BEGIN`/`END`. |
| `unauthorized` no `docker compose pull` | O pacote do GHCR é privado (passo 4), ou o nome da imagem aponta para outro dono ([fork](#fork)). |
| `já existe um deploy em andamento` | Outro deploy (do CI ou manual) está rodando; espere. |
| O deploy voltou para a versão anterior | O cérebro novo não ficou saudável em 90 s: `docker compose -f docker-compose.prod.yml logs brain`. |
