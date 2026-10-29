---
title: Operação e manutenção
group: Produção
order: 7
description: O dia a dia do servidor: comandos dentro do contêiner, logs, saúde, comandos administrativos da memória, atualização manual, troca de segredos e como recriar tudo do zero.
status: guia
tags: operação manutenção atalho logs health saúde comandos administrativos memória sonho study atualizar git pull rotacionar token segredos recriar restaurar problemas
---

## Usar a Juniper no servidor {#atalho}

Na VPS o comando `juniper` **não existe** fora do contêiner: a imagem leva o código e tudo roda dentro do serviço `brain` (o *entrypoint* é `python -m brain`). Crie um atalho no `~/.bashrc` do `deploy`:

```bash
echo "alias juniper='docker compose -f ~/juniper/docker-compose.prod.yml exec brain python -m brain'" >> ~/.bashrc
source ~/.bashrc
```

```bash
juniper logs -f               # o log técnico ao vivo
juniper logs --errors         # só falhas
juniper memory list           # a memória do servidor
juniper tools                 # as tools do servidor
juniper voice status          # a voz: credenciais e gasto do mês
juniper music status          # a música: chave, cota e login
```

Sem terminal interativo (um script, o cron, o CI), use `exec -T`. Comandos que mexem no ambiente de um computador (`install`, `docs`, `deploy` do git) não fazem sentido no contêiner. O uso do dia a dia é pelos clientes ([Clientes em produção](clientes-producao.md)).

## Saúde e logs {#saude}

```bash
docker compose -f docker-compose.prod.yml ps                 # brain e mongo "healthy"
docker compose -f docker-compose.prod.yml logs --tail=50 brain
curl -s http://<IP-PRIVADO>:8765/health                      # de um dispositivo na rede privada
```

- O `/health` distingue `ok` e `degraded` (o modelo de embeddings carregando) ([API HTTP](api-http.md#health)).
- Os logs JSONL ficam no volume `juniper-logs`, um arquivo por dia, com segredos mascarados; o próprio servidor compacta os de ontem e apaga os com mais de `logging.retention_days` (3) às `logging.rotate_at` (00:10) ([Observabilidade](observabilidade.md)).
- Os logs dos contêineres usam o driver `json-file` com limite de tamanho.
- Não há alertas ativos: acompanhe a aba *Actions* (se usa o [deploy contínuo](deploy-continuo.md)) e o `ps`.

## Administração da memória {#memoria}

| Tarefa | Comando |
|---|---|
| Ver, buscar, exportar, esquecer | `juniper memory list`, `search`, `export -o arquivo.json`, `forget <id>` |
| Refazer os vetores (depois de trocar o modelo de embeddings) | `juniper memory reindex` |
| Testar o sonho da Memória V2 sem gravar | `juniper memory dream --dry-run` |
| Rodar o sonho à mão (recuperar uma noite) | `juniper memory dream` |
| Ensinar um texto à Juniper | `docker compose -f docker-compose.prod.yml exec -T brain python -m brain study add - --topic "Tema" < texto.txt` |
| Auditar a Memória V2 | `juniper memory beliefs`, `memory affect`, `memory journal`, `persona show` |
| Ver o que a V2 acrescenta ao prompt | `juniper memory prompt "<mensagem>"` |
| Investigar vazamentos na fala | `juniper vazamentos`, `--resumo`, `--ver <id>` ([Auditoria de vazamentos](vazamentos.md)) |

O sonho roda sozinho às `memory.v2.night.at` (03:00) quando a V2 está ligada, depois do backup diário ([Memória V2](memoria-v2.md), [Backup da memória](backup.md)).

## Atualizar {#atualizar}

Com o [deploy contínuo](deploy-continuo.md), um merge em `master` basta. À mão, com a mesma segurança (backup, *healthcheck*, rollback), na VPS:

```bash
cd ~/juniper && git fetch origin master
./scripts/deploy.sh "$(git rev-parse origin/master)"    # exige a imagem desse SHA no registro
```

Sem registro de imagens (construindo na própria VPS):

```bash
cd ~/juniper && git pull
./scripts/backup_memory.sh manual
docker compose -f docker-compose.prod.yml up -d --build
```

Mudou só o `.env`, o `config.yaml` ou `certs/`? Basta `docker compose -f docker-compose.prod.yml up -d`.

## Trocar segredos {#segredos}

| Segredo | Como trocar |
|---|---|
| Token dos clientes | Apague `/home/juniper/.juniper/state/token` no contêiner e reinicie o `brain`; atualize todos os clientes ([Clientes em produção](clientes-producao.md)). |
| Chaves de LLM, YouTube, Groq | Gere a nova no painel do provedor, troque no `.env`, `up -d` e revogue a antiga. |
| Contas de serviço do Google | Crie uma chave nova na conta, substitua o JSON em `certs/`, `up -d` e exclua a antiga no Google Cloud. |
| Senha do MongoDB | O MongoDB só lê a senha ao criar o volume. Faça backup, restaure num volume novo com a senha nova (ou altere o usuário pelo `mongosh` e depois o `.env`). |
| Token do bot do Telegram | `/revoke` no `@BotFather`, troque no `.env`, `up -d telegram`. |
| Credenciais do CI | [Deploy contínuo → Girar as credenciais](deploy-continuo.md#girar). |

## Recomeçar do zero {#recriar}

O servidor é descartável: o que importa (a memória) está no backup fora dele. Para recriar:

1. Reinstale a imagem do sistema no painel do provedor (ou crie uma VPS nova).
2. Siga [Deploy do cérebro numa VPS](deploy-vps.md) de cima para baixo e refaça os segredos (eles não estão no backup).
3. Restaure a memória: [Backup da memória → Restaurar](backup.md#restaurar).
4. Remova o nó antigo da rede privada (no Tailscale, em *Machines*), para o novo reaproveitar o nome.
5. Se usa deploy contínuo, atualize `DEPLOY_HOST` e `DEPLOY_KNOWN_HOSTS` com o IP e a chave do servidor novo.

> **Atenção:** nunca rode `docker compose ... down -v` num servidor cujos dados não tenham backup: o `-v` apaga os volumes, inclusive a memória.

## Problemas comuns {#problemas}

| Sintoma | Onde olhar |
|---|---|
| Os clientes não conectam | `ps` (o `brain` está `healthy`?), a VPN do dispositivo e a [varredura](deploy-vps.md#varredura) de dentro da rede privada. |
| O cérebro reinicia sem parar | `logs brain`: credenciais do MongoDB, `.env` ilegível (uid) ou um arquivo montado que virou pasta. |
| Respostas lentas | Os tempos por etapa no log (`timing`) e o failover de LLM (`failover`) ([Observabilidade](observabilidade.md#tempos)). |
| A memória não lembra | `juniper memory list` e `juniper memory search "..." --threshold -1` ([Memória](memoria.md#diagnostico)). |
| Disco cheio | `docker system df`; imagens antigas: `docker image prune`. Nunca apague volumes sem backup. |

## Para saber mais {#para-saber-mais}

- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
- [Visão geral da produção](producao.md) (Produção): Como a Juniper roda em produção num servidor qualquer: topologia, requisitos, modelo de segurança, configuração, dados e o roteiro das páginas seguintes.
- [Deploy do cérebro numa VPS](deploy-vps.md) (Produção): Tutorial completo para levar o cérebro a uma VPS de qualquer provedor: acesso e SSH, sistema, Docker, rede privada, firewall, segredos, subida, verificação e varredura de portas.
- [Backup da memória](backup.md) (Produção): Como a memória da Juniper (o banco MongoDB) é copiada, criptografada e versionada num repositório privado do GitHub, e como restaurar.
- [Deploy contínuo](deploy-continuo.md) (Produção): Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca de versão, healthcheck e rollback automático.
