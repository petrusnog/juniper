---
title: Deploy do cérebro numa VPS
group: Produção
order: 2
description: Tutorial completo para levar o cérebro a uma VPS de qualquer provedor: acesso e SSH, sistema, Docker, rede privada, firewall, segredos, subida, verificação e varredura de portas.
status: guia
tags: deploy vps tutorial ubuntu ssh hardening usuário deploy ufw firewall tailscale wireguard acl docker compose env certs permissões uid 10001 health nmap varredura swap unattended-upgrades dimensionar bench
---

Este tutorial parte de uma VPS recém-criada e termina com o cérebro saudável, alcançável pelos seus dispositivos e **sem nenhuma porta da Juniper aberta para a internet** ([Visão geral da produção](producao.md), [ADR-013](../decisions/ADR-013.md)). Os comandos assumem Ubuntu LTS; em outra distribuição, troque o gerenciador de pacotes. A rede privada dos exemplos é o Tailscale; com outra VPN, veja a [nota](#outra-vpn).

Convenções usadas abaixo:

| Marcador | O que é |
|---|---|
| `<IP-PUBLICO>` | O IP público da VPS, do painel do provedor. |
| `deploy` | O usuário sem privilégio que vai operar a Juniper. |
| `<IP-PRIVADO>` | O IP da VPS dentro da rede privada (no Tailscale, `100.x.y.z`). |
| `juniper-brain` | O nome da VPS na rede privada. |
| `<owner>` | O dono do repositório (o seu fork, ou o original). |

> **Atenção:** em cada passo que mexe no SSH ou no firewall, **não feche a sessão atual antes de testar o acesso numa segunda**. Se ficar trancado de fora, use o console web do provedor (VNC, serial ou modo de recuperação), que não depende de rede.

## 1. Criar a VPS {#criar}

No provedor de sua escolha, crie uma VPS x86_64 com Ubuntu LTS, de preferência com **4 vCPU e 8 GB** (o mínimo é 2 vCPU e 4 GB; veja os [requisitos](producao.md#requisitos)). Cadastre a sua chave SSH pública na criação, se o provedor permitir. Prefira cobrança mensal até validar o servidor ([Dimensionar](#dimensionar)).

## 2. Usuário de deploy e SSH {#usuario}

Entre como `root` (`ssh root@<IP-PUBLICO>`) e crie o usuário de operação:

```bash
apt update && apt -y upgrade

# O uid 10001 é de propósito: é o mesmo uid do usuário dentro do contêiner, então os arquivos
# montados (.env, credenciais do Google) ficam em 600 e legíveis pelos dois.
adduser --disabled-password --gecos "" --uid 10001 deploy

install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys     # copie; não mova ainda
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys
```

Se o uid 10001 já estiver em uso, `getent passwd 10001` mostra por quem; veja a [nota sobre permissões](#diretorios).

Agora, **só chave, sem senha e sem root remoto**. O arquivo começa com `00-`: o `sshd` usa o **primeiro** valor que lê, e imagens de nuvem costumam trazer um `50-cloud-init.conf` com `PasswordAuthentication yes`.

```bash
cat > /etc/ssh/sshd_config.d/00-juniper.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t && systemctl restart ssh
sshd -T | grep -Ei 'passwordauthentication|permitrootlogin|kbdinteractive'   # tudo `no`
```

Num **segundo terminal**: `ssh deploy@<IP-PUBLICO>` deve entrar e `ssh root@<IP-PUBLICO>` deve ser recusado. Só então feche a sessão de root. O usuário `deploy` não precisa de `sudo` para o deploy (só do grupo `docker`); dê `sudo` com senha se for administrar o servidor por ele.

## 3. Sistema: atualizações, relógio e swap {#sistema}

```bash
sudo apt -y install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades                  # responda "Sim"
echo 'Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";' | sudo tee /etc/apt/apt.conf.d/52juniper-reboot

sudo timedatectl set-ntp true && timedatectl                     # "System clock synchronized: yes"

# 2 GB de swap, se a VPS não vem com nenhum
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

## 4. Docker {#docker}

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy      # o grupo docker equivale a root: só o usuário de operação
```

Saia e entre de novo como `deploy` e confira com `docker ps`.

## 5. Rede privada {#rede}

**5.1 Instale e entre na rede** (Tailscale):

```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up --hostname=juniper-brain --advertise-tags=tag:juniper-server
tailscale ip -4          # o <IP-PRIVADO>
```

O comando imprime uma URL: abra-a, entre na sua conta e aprove o dispositivo. O `--advertise-tags` só funciona depois que a etiqueta existir na política de acesso (5.2); se der erro, faça a política e repita.

**5.2 Política de acesso.** No painel do Tailscale (*Access controls*), deixe só os seus dispositivos (e, se for usar, o CI) alcançarem a VPS, e só nas portas necessárias. Mescle com a política que já existe:

```json
{
  "tagOwners": {
    "tag:juniper-server": ["autogroup:admin"],
    "tag:juniper-client": ["autogroup:admin"],
    "tag:ci":             ["autogroup:admin"]
  },
  "grants": [
    { "src": ["tag:juniper-client"], "dst": ["tag:juniper-server"], "ip": ["tcp:22", "tcp:8765", "tcp:8766"] },
    { "src": ["tag:ci"],             "dst": ["tag:juniper-server"], "ip": ["tcp:22"] }
  ]
}
```

Os seus dispositivos precisam da etiqueta `tag:juniper-client`; se preferir não etiquetá-los, troque o `src` por `autogroup:member`. A 8766 é o [dashboard](dashboard.md); tire-a se não for usá-lo. Teste em *Preview rules* antes de salvar.

**5.3 Ordem no boot.** O Docker só consegue publicar uma porta no IP da VPN se a interface já existir quando ele sobe:

```bash
sudo systemctl edit docker      # adicione:
# [Unit]
# After=tailscaled.service
# Wants=tailscaled.service
```

### Outra VPN {#outra-vpn}

Com WireGuard (ou outra VPN), o resto do tutorial é igual: use o IP do servidor na VPN como `<IP-PRIVADO>`, troque `tailscale0` pelo nome da interface da VPN (por exemplo, `wg0`) no firewall e garanta que a VPN suba antes do Docker. Se os clientes usarem um nome DNS que não seja `*.ts.net`, inclua-o em `dashboard.allowed_hosts`.

## 6. Firewall {#firewall}

```bash
sudo apt -y install ufw
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow in on tailscale0
sudo ufw allow 22/tcp            # TEMPORÁRIO, até confirmar o SSH pela rede privada
sudo ufw --force enable
```

Do seu computador, com a VPN ligada: `ssh deploy@juniper-brain` (ou `ssh deploy@<IP-PRIVADO>`). Funcionou? Feche o SSH público:

```bash
sudo ufw delete allow 22/tcp
sudo ufw status verbose          # só "Anywhere on tailscale0 ALLOW IN"
```

Se o provedor oferece um firewall de borda, deixe-o **sem nenhuma regra de entrada**: a VPN conversa de dentro para fora.

> **Atenção:** o Docker publica portas por fora do `ufw` (escreve as próprias regras de `iptables`). Por isso o compose publica as portas **só no IP da VPN** (`TAILSCALE_IP`) e o MongoDB não publica porta nenhuma. A [varredura](#varredura) é a prova.

## 7. Código, segredos e permissões {#diretorios}

Como `deploy`:

```bash
git clone https://github.com/<owner>/juniper.git ~/juniper && cd ~/juniper
install -d -m 700 certs
cp .env.example .env
chmod 600 .env
echo "TAILSCALE_IP=$(tailscale ip -4)" >> .env
```

Edite o `.env` (`nano .env`) e preencha pelo menos:

```bash
JUNIPER_MEMORY__MONGO__USERNAME=juniper
JUNIPER_MEMORY__MONGO__PASSWORD=<uma senha longa e aleatória>     # openssl rand -base64 32
JUNIPER_DEEPINFRA_API_KEY=...        # e/ou
JUNIPER_GROQ_API_KEY=...
```

Opcional: a sua configuração pessoal (agendas do Google, música, voz, Memória V2) como camada base. Copie-a do seu computador e aponte no `.env`:

```bash
# no seu computador, com a VPN ligada
scp ~/.juniper/config.yaml deploy@juniper-brain:~/juniper/config.yaml
# na VPS
chmod 600 config.yaml && echo "JUNIPER_BASE_CONFIG_FILE=./config.yaml" >> .env
```

As credenciais do Google entram em `certs/` ([Integrações Google Cloud e Groq](integracoes-producao.md)). O resultado esperado:

```text
~/juniper/                  deploy:deploy  755   (o repositório: compose, docker/, scripts/)
├── .env                    deploy (10001) 600   senha do MongoDB, chaves, TAILSCALE_IP
├── config.yaml             deploy (10001) 600   configuração pessoal (opcional)
└── certs/                  deploy (10001) 700
    ├── gcp-key.json        deploy (10001) 600   agenda do Google (opcional)
    └── gcp-voice.json      deploy (10001) 600   voz do Google (opcional)
```

```bash
stat -c '%a %u %n' .env config.yaml certs certs/* 2>/dev/null    # 600/700 e uid 10001
git status --short                                               # .env, config.yaml e certs/ NÃO aparecem
```

> **Nota:** como o `deploy` tem o mesmo uid (10001) do usuário do contêiner, um só dono resolve os dois lados. Se o seu usuário tiver outro uid, deixe os arquivos com dono `10001:10001` e `600` e rode o `docker compose` com `sudo`. Nunca afrouxe para `644`: o `.env` tem a senha do banco.

## 8. Subir {#subir}

```bash
cd ~/juniper
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps               # brain e mongo "healthy"
docker compose -f docker-compose.prod.yml logs --tail=30 brain
```

O primeiro `up` constrói a imagem na própria VPS (com o [deploy contínuo](deploy-continuo.md), a imagem passa a vir pronta de um registro). Na primeira subida o cérebro baixa o modelo de embeddings: até terminar, o `/health` diz `degraded` / `warming_up`.

Do seu computador, com a VPN ligada:

```bash
curl -s http://juniper-brain:8765/health        # {"status": "ok", "memory": "ready", ...}
```

O token dos clientes nasce no primeiro boot, dentro do volume `juniper-state`:

```bash
docker compose -f docker-compose.prod.yml exec -T brain cat /home/juniper/.juniper/state/token
```

Guarde-o num gerenciador de senhas: todos os clientes precisam dele ([Clientes em produção](clientes-producao.md)).

## 9. Varredura de portas {#varredura}

A prova de que **nenhuma porta da Juniper está aberta na internet**. A varredura tem de partir de **fora da rede privada** (o seu computador com a VPN desligada, o celular em dados móveis, ou outra máquina):

```bash
nmap -Pn -p- -T4 <IP-PUBLICO>                     # todas as portas TCP
sudo nmap -Pn -sU --top-ports 100 <IP-PUBLICO>    # as UDP mais comuns (lento)
```

Esperado: **todas** `filtered` ou `closed`, nenhuma `open` (a 22 também `filtered`). Sem `nmap`: `nc -zv -w 3 <IP-PUBLICO> 22 8765 8766 27017`, cada uma deve dar *timeout*.

De dentro da rede privada, o esperado é o contrário:

```bash
nmap -Pn -p 22,8765,8766,27017 juniper-brain
# 22/tcp open · 8765/tcp open · 8766/tcp open · 27017/tcp closed|filtered (o MongoDB nunca é publicado)
```

Na VPS, confirme a causa:

```bash
sudo ss -tulpn
docker ps --format '{{.Names}}\t{{.Ports}}'
```

| Esperado | Alerta |
|---|---|
| `8765` e `8766` em `<IP-PRIVADO>` | `0.0.0.0:8765` ou `[::]:8765`: o `TAILSCALE_IP` está vazio no `.env`. |
| `mongo` sem porta publicada | `27017` publicada: alguém adicionou `ports:` ao serviço. |
| `22` do `sshd` (barrada pelo firewall) | Qualquer porta de contêiner em `0.0.0.0`. |

Repita a varredura depois de cada mudança no compose, no firewall ou na política da VPN.

## Verificação final {#aceite}

- [ ] A varredura de fora não mostra nenhuma porta `open`.
- [ ] `ssh deploy@juniper-brain` e `curl http://juniper-brain:8765/health` funcionam pela rede privada.
- [ ] `sshd -T` mostra `passwordauthentication no` e `permitrootlogin no`; `ssh root@<IP-PUBLICO>` é recusado.
- [ ] `.env`, `config.yaml` e `certs/*` estão em `600` (pasta `700`) e fora do `git status`.
- [ ] O token está guardado num gerenciador de senhas.

Próximos passos: [Integrações Google Cloud e Groq](integracoes-producao.md), [Clientes em produção](clientes-producao.md) e [Backup da memória](backup.md).

## Dimensionar a VPS {#dimensionar}

Para medir se uma VPS candidata comporta a Juniper (e quão rápida ela fica), rode `scripts/bench_server.py` **na VPS** (usa só a biblioteca padrão do Python):

```bash
JUNIPER_DEEPINFRA_API_KEY=... python3 scripts/bench_server.py --rounds 5 --llm --stack --out ~/bench-1.json
```

Rode uma vez com a pilha parada (sem `--stack`) e outra com a pilha de pé e uma conversa em andamento, em horários diferentes. A chave só vai pelo ambiente e não é gravada no resultado.

| Item | Sinal de alerta |
|---|---|
| CPU: variação entre rodadas e *steal* | variação acima de ~20% ou *steal* acima de ~5% |
| Disco: `fsync` | mediana acima de ~10 ms |
| Memória livre com a pilha de pé | menos de ~1 GB |
| Rede até o provedor de LLM | RTT acima de ~100 ms |
| LLM: primeiro token | bem acima do que o provedor entrega de perto |

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| Fiquei sem acesso SSH | Use o console web do provedor. Confira `sudo ufw status` e `tailscale status`. |
| `tailscale up` recusa `--advertise-tags` | A etiqueta não está no `tagOwners` da política. Corrija e repita. |
| A 8765 não responde pela rede privada | `TAILSCALE_IP` errado ou vazio; o Docker subiu antes da VPN (veja 5.3); a política não inclui o seu dispositivo. |
| `cannot assign requested address` ao subir | O Docker tentou publicar no IP da VPN antes de a interface existir: `sudo systemctl restart docker` e suba de novo. |
| `Permission denied` ao ler `.env` ou uma credencial | O dono do arquivo não é o uid 10001: `sudo chown 10001:10001 <arquivo>`. |
| O Docker criou uma **pasta** no lugar de um arquivo | O arquivo montado não existia na hora do `up`. Apague a pasta, crie o arquivo e suba de novo. |
| `Temporary failure in name resolution` com `juniper-brain` | O nó tem outro nome na VPN: `sudo tailscale set --hostname=juniper-brain`, ou use o IP. |
| `unhealthy`, ou o cérebro não conecta ao MongoDB | `logs brain` mostra o motivo; quase sempre o usuário ou a senha do MongoDB mudaram depois de o volume ser criado. |

## Para saber mais {#para-saber-mais}

- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
- [Visão geral da produção](producao.md) (Produção): Como a Juniper roda em produção num servidor qualquer: topologia, requisitos, modelo de segurança, configuração, dados e o roteiro das páginas seguintes.
- [Backup da memória](backup.md) (Produção): Como a memória da Juniper (o banco MongoDB) é copiada, criptografada e versionada num repositório privado do GitHub, e como restaurar.
- [Deploy contínuo](deploy-continuo.md) (Produção): Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca de versão, healthcheck e rollback automático.
- [Operação e manutenção](operacao.md) (Produção): O dia a dia do servidor: comandos dentro do contêiner, logs, saúde, comandos administrativos da memória, atualização manual, troca de segredos e como recriar tudo do zero.
