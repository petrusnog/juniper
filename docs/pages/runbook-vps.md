---
title: Runbook da VPS
group: Produção
order: 2
description: Passo a passo para reconstruir a VPS de produção do zero: hardening, Tailscale, credencial do CI, diretórios, subida da Juniper e varredura de portas.
status: guia
tags: runbook vps contabo ubuntu ssh ufw tailscale acl oauth ci github actions deploy diretorios permissoes nmap varredura portas swap unattended-upgrades t-082
---

> **Nota:** este é o runbook da T-082. Ele vale para a VPS atual (**Contabo Cloud VPS 4, US East**, Ubuntu 24.04; veja o adendo da T-081 no [ADR-013](adr-013.html)) e serve para recriá-la sem consultar mais nada. A visão geral da produção está em [Produção](producao.html); este texto é a parte de **operar**.

## Visão geral {#visao-geral}

Meta: uma VPS **sem nenhuma porta da Juniper aberta para a internet**. Tudo passa pelo Tailscale; o isolamento é feito no próprio servidor (`ufw`), porque este plano do Contabo não tem firewall de borda.

| Etapa | Resultado |
|---|---|
| [Acesso e usuário de deploy](#usuario) | Só chave SSH, sem senha, sem root remoto |
| [Sistema](#sistema) | Atualizações de segurança automáticas, NTP e swap |
| [Docker e Tailscale](#docker-tailscale) | VPS dentro da tailnet com a etiqueta `tag:juniper-server` |
| [ACL](#acl) | Só clientes e CI alcançam a VPS, só nas portas 22 e 8765 |
| [Firewall](#firewall) | `default deny incoming`; só entra o que vem pelo `tailscale0` |
| [Credencial do CI](#ci) | Chave OAuth temporária com `tag:ci` e chave SSH exclusiva de deploy |
| [Diretórios](#diretorios) | `~/juniper/` com `.env` e chave do Google em `600` |
| [Subir](#subir) | `brain` e `mongo` saudáveis, porta 8765 só no IP Tailscale |
| [Varredura](#varredura) | Prova de que nenhuma porta pública responde |

Convenções: `<IP-PUBLICO>` é o IP da VPS no painel do Contabo; `deploy` é o usuário sem privilégio; `100.x.y.z` é o IP da VPS no Tailscale; `juniper-brain` é o nome dela na tailnet.

## Usuário de deploy e SSH {#usuario}

Como `root`, na primeira entrada (o Contabo envia a senha por e-mail). **Não feche a primeira sessão antes de testar a chave em outra.**

```bash
apt update && apt -y upgrade

# Usuário de deploy. O uid 10001 é de propósito: é o mesmo uid do usuário `juniper` dentro do contêiner,
# então os arquivos montados (.env, chave do Google) ficam em 600 e legíveis pelos DOIS (veja "Diretórios").
adduser --disabled-password --gecos "" --uid 10001 deploy
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys
```

Só chave, sem senha e sem root remoto. O arquivo se chama `00-`: o `sshd` vale o **primeiro** valor que lê, e imagens de nuvem trazem um `50-cloud-init.conf` com `PasswordAuthentication yes`.

```bash
cat > /etc/ssh/sshd_config.d/00-juniper.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sshd -t && systemctl restart ssh
sshd -T | grep -Ei 'passwordauthentication|permitrootlogin|kbdinteractive'   # tudo `no`
```

Em **outro terminal**: `ssh deploy@<IP-PUBLICO>` (deve entrar) e `ssh root@<IP-PUBLICO>` (deve ser recusado). Só então feche a sessão de root. O `deploy` ganha `sudo` com senha própria, ou com `NOPASSWD` restrito, conforme você preferir; **o deploy automático não precisa de `sudo`**, só do grupo `docker`.

## Sistema: atualizações, NTP e swap {#sistema}

```bash
sudo apt -y install unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades                  # responda "Sim"
# reinício automático fora do horário de uso, se um kernel novo pedir
echo 'Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "05:00";' | sudo tee /etc/apt/apt.conf.d/52juniper-reboot

timedatectl set-ntp true && timedatectl                          # "System clock synchronized: yes"

# Swap de 2 GB (a VPS não vem com swap; adendo da T-081)
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h                                                           # Swap: 2.0Gi
```

## Docker e Tailscale {#docker-tailscale}

```bash
curl -fsSL https://get.docker.com | sudo sh
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up --hostname=juniper-brain --advertise-tags=tag:juniper-server
sudo usermod -aG docker deploy      # o grupo docker equivale a root na máquina: só o usuário de deploy
```

O comando imprime uma URL: abra-a, entre na sua conta e aprove o dispositivo. O `--advertise-tags` só funciona depois de a etiqueta existir no `tagOwners` da ACL ([abaixo](#acl)); se der erro, faça a ACL primeiro e repita. Depois:

```bash
tailscale ip -4          # o 100.x.y.z da VPS
tailscale status
```

No Docker, a porta publicada só no IP do Tailscale depende de a interface `tailscale0` existir quando o Docker sobe. Para garantir a ordem no boot:

```bash
sudo systemctl edit docker      # adicione:
# [Unit]
# After=tailscaled.service
# Wants=tailscaled.service
```

## ACL do Tailscale {#acl}

No painel de administração (**Access controls**), a política precisa dos donos das etiquetas e de uma regra que deixe só clientes e CI alcançarem a VPS. Mescle com a sua política atual, em vez de substituí-la.

```json
{
  "tagOwners": {
    "tag:juniper-server": ["autogroup:admin"],
    "tag:juniper-client": ["autogroup:admin"],
    "tag:ci":             ["autogroup:admin"]
  },
  "grants": [
    { "src": ["tag:juniper-client"], "dst": ["tag:juniper-server"], "ip": ["tcp:22", "tcp:8765"] },
    { "src": ["tag:ci"],             "dst": ["tag:juniper-server"], "ip": ["tcp:22"] }
  ]
}
```

- O CI só precisa do SSH (22): o *healthcheck* do deploy roda **dentro** da VPS.
- Os seus dispositivos (PC, celular) precisam da etiqueta `tag:juniper-client` para entrar nessa regra. Se preferir não etiquetá-los, troque o `src` por `autogroup:member`.
- Teste na aba **Preview rules** do painel antes de salvar.

## Firewall no servidor (ufw) {#firewall}

```bash
sudo apt -y install ufw
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow in on tailscale0
sudo ufw allow 22/tcp            # TEMPORÁRIO, até confirmar o SSH pela tailnet
sudo ufw --force enable
```

Do seu computador (com o Tailscale ligado): `ssh deploy@juniper-brain`. Funcionou? Feche o SSH público:

```bash
sudo ufw delete allow 22/tcp
sudo ufw status verbose          # só "Anywhere on tailscale0 ALLOW IN"
```

> **Atenção:** o Docker publica portas por fora do `ufw` (ele escreve as próprias regras de `iptables`). Por isso o compose publica a 8765 **só no IP do Tailscale** (`TAILSCALE_IP`) e o Mongo não publica porta nenhuma. A [varredura](#varredura) é a prova de que isso está certo.

## Credencial do CI {#ci}

O runner do GitHub Actions entra na tailnet como um nó **temporário** (`tag:ci`) e faz SSH na VPS. São duas credenciais independentes, ambas só em **GitHub Secrets**.

### 1. Cliente OAuth do Tailscale {#ci-oauth}

Por que OAuth e não uma chave de autenticação comum: a chave comum expira em até 90 dias e precisa ser trocada à mão; o cliente OAuth não expira e o runner gera uma chave efêmera a cada execução.

1. Painel do Tailscale → **Settings → Trust credentials → Credential → OAuth** (nas versões antigas do painel o menu se chamava *OAuth clients*; o rótulo do botão também varia entre *Credential* e *+ Credential*).
2. Escopos: **Keys → Auth Keys → Write** e **Devices → Core → Write** (a [documentação do Tailscale para runners do GitHub](https://tailscale.com/kb/1586/secure-github-runners) pede os dois; o primeiro cria a chave, o segundo deixa o nó entrar). Nenhum outro.
3. Etiqueta: `tag:ci`. O cliente só consegue criar chaves para nós com essa etiqueta.
4. Copie o *Client ID* e o *Client secret* (o secret só aparece uma vez).
5. No GitHub: **Settings → Secrets and variables → Actions** do repositório, crie `TS_OAUTH_CLIENT_ID` e `TS_OAUTH_SECRET`.

No workflow (T-084) o passo fica assim; fixe a versão da ação (de preferência por SHA):

```yaml
- uses: tailscale/github-action@v3
  with:
    oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
    oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
    tags: tag:ci
```

### 2. Chave SSH exclusiva do deploy {#ci-ssh}

Uma chave só para o CI, separada da sua chave pessoal, para poder revogá-la sem mexer no seu acesso.

```bash
# no seu computador
ssh-keygen -t ed25519 -f ./juniper-ci -N "" -C "juniper-ci"

# na VPS, como deploy: autoriza a chave PÚBLICA
echo "<conteúdo de juniper-ci.pub>" >> ~/.ssh/authorized_keys

# a identidade da VPS, para o CI não aceitar um servidor falso (rode com o Tailscale ligado)
ssh-keyscan -t ed25519 juniper-brain
```

No GitHub, crie estes segredos e **apague** os arquivos locais `juniper-ci` e `juniper-ci.pub` depois:

| Segredo | Valor |
|---|---|
| `DEPLOY_SSH_KEY` | conteúdo de `juniper-ci` (a chave **privada**) |
| `DEPLOY_HOST` | `juniper-brain` (o nome na tailnet) |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_KNOWN_HOSTS` | a linha que o `ssh-keyscan` imprimiu |

**Girar as credenciais:** gere um novo cliente OAuth (ou nova chave SSH), atualize o segredo no GitHub, rode um deploy manual (`workflow_dispatch`) e só então revogue o antigo (no Tailscale: **Settings → Trust credentials**, revogue a credencial antiga; na VPS: remova a linha de `~/.ssh/authorized_keys`).

## Diretórios e permissões {#diretorios}

Tudo mora em `/home/deploy/juniper/`. O compose monta o `.env`, o `config.yaml` e a pasta `certs/` do servidor **somente leitura**; segredos nunca vão para a imagem nem para o git.

```text
~/juniper/                  deploy:deploy  755   (o repositório clonado: compose, docker/, scripts/)
├── .env                    10001:10001    600   senha do Mongo, chaves de LLM, TAILSCALE_IP
├── config.yaml             10001:10001    600   sua configuração pessoal (agendas do Google)
└── certs/                  10001:10001    700
    └── gcp-key.json        10001:10001    600   chave da conta de serviço do Google
```

Como o usuário `deploy` tem **o mesmo uid (10001)** do usuário do contêiner, um só dono resolve os dois lados: o contêiner lê os arquivos e o `docker compose` (que lê o `.env` como o `deploy`) também.

```bash
# como deploy
git clone https://github.com/petrusnog/juniper.git ~/juniper && cd ~/juniper
install -d -m 700 certs

# do seu computador (Tailscale ligado):
#   scp ~/.juniper/certs/gcp-key.json deploy@juniper-brain:~/juniper/certs/gcp-key.json
#   scp ~/.juniper/config.yaml        deploy@juniper-brain:~/juniper/config.yaml

cp .env.example .env
chmod 600 .env certs/gcp-key.json config.yaml
echo "TAILSCALE_IP=$(tailscale ip -4)"           >> .env
echo "JUNIPER_BASE_CONFIG_FILE=./config.yaml"    >> .env
# edite o .env: JUNIPER_MEMORY__MONGO__USERNAME/PASSWORD e as chaves de LLM
```

Conferência (nada deve sobrar com permissão frouxa):

```bash
stat -c '%a %U:%G %n' .env config.yaml certs certs/gcp-key.json     # 600/700, dono deploy (uid 10001)
git status --short                                                    # .env, config.yaml e certs/ NÃO aparecem
```

> **Se o `deploy` não tem uid 10001:** `id deploy` mostra o uid. O caminho mais simples é recriar o usuário com `--uid 10001`. Alternativa: deixar os arquivos com dono `10001:10001` e `600`, e o `docker compose` rodar com `sudo` (o `.env` não seria legível pelo `deploy`). Não afrouxe para `644`: o `.env` tem a senha do Mongo.

## Subir a Juniper {#subir}

```bash
cd ~/juniper
docker compose -f docker-compose.prod.yml up -d --build    # o deploy da T-084 usa a imagem do GHCR em vez do build
docker compose -f docker-compose.prod.yml ps               # brain e mongo "healthy"
docker compose -f docker-compose.prod.yml logs --tail=20 brain
```

Do seu computador, com o Tailscale ligado:

```bash
curl -s http://juniper-brain:8765/health        # {"status": "ok", ...}
```

## Varredura de portas {#varredura}

A prova de que a VPS **não tem nenhuma porta da Juniper aberta na internet**. A regra de ouro: a varredura tem de partir de **fora da tailnet**, senão você está testando a rede privada, não a pública.

**1. De fora (o teste que vale).** Use uma máquina que não esteja no Tailscale: o seu computador com o Tailscale **desligado**, o celular em 4G/5G (Wi-Fi desligado) ou outra VPS descartável.

```bash
# todas as 65535 portas TCP; -Pn não depende de ping, -T4 acelera
nmap -Pn -p- -T4 <IP-PUBLICO>

# as 100 portas UDP mais comuns (lento: UDP não responde quando está fechado)
sudo nmap -Pn -sU --top-ports 100 <IP-PUBLICO>
```

Resultado esperado: **todas** as portas `filtered` (o `ufw` descarta sem responder) ou `closed`, e nenhuma `open`. A 22 em particular tem de aparecer `filtered`. Uma linha `open` é falha: anote a porta e vá ao passo 3.

Sem `nmap`: `nc -zv -w 3 <IP-PUBLICO> 22 8765 27017` (cada uma deve dar *timeout*), ou um scanner on-line de portas apontado para o IP.

**2. De dentro da tailnet (o que deve funcionar).** Com o Tailscale ligado:

```bash
nmap -Pn -p 22,8765,27017 juniper-brain
# 22/tcp    open
# 8765/tcp  open
# 27017/tcp closed|filtered   <- o Mongo nunca é publicado
```

**3. Na VPS, o que está escutando.** Confirma a causa, não só o sintoma:

```bash
sudo ss -tulpn                     # veja QUEM escuta e em QUE endereço
docker ps --format '{{.Names}}\t{{.Ports}}'
sudo ufw status verbose
```

| Esperado | Alerta |
|---|---|
| `8765` em `100.x.y.z:8765` (só o IP Tailscale) | `0.0.0.0:8765` ou `[::]:8765`: o `TAILSCALE_IP` está vazio no `.env` |
| `22` do `sshd` (o `ufw` barra o acesso público) | qualquer porta de contêiner em `0.0.0.0` |
| `mongo` sem nenhuma porta publicada | `27017` publicada: alguém adicionou `ports:` ao serviço |
| `tailscaled` em UDP (saída; não precisa estar aberto para fora) | serviços inesperados escutando |

Repita a varredura **depois de cada mudança** no compose, no `ufw` ou na ACL, e guarde a saída do `nmap` no relatório da T-082.

## Verificação final (critérios de aceite) {#aceite}

- [ ] **Sem portas públicas:** a varredura de fora não mostra nenhuma porta `open`.
- [ ] **Alcance pela tailnet:** `ssh deploy@juniper-brain` e `curl http://juniper-brain:8765/health` funcionam.
- [ ] **SSH endurecido:** `sshd -T` mostra `passwordauthentication no` e `permitrootlogin no`; `ssh root@<IP-PUBLICO>` é recusado.
- [ ] **Segredos:** `stat` mostra `600` em `.env`, `config.yaml` e `certs/gcp-key.json`, e `git status` não os lista.
- [ ] **Runbook:** a VPS foi recriada (ou os passos conferidos) só com este texto.

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| Fiquei sem acesso SSH | Console VNC do painel do Contabo (não depende de rede). Confira `sudo ufw status` e `tailscale status`. |
| `tailscale up` recusa `--advertise-tags` | A etiqueta não está no `tagOwners` da ACL. Corrija a ACL e repita. |
| A porta 8765 não responde pela tailnet | `TAILSCALE_IP` errado ou vazio no `.env`; o Docker subiu antes do `tailscaled` (veja o `After=` acima); a ACL não inclui o seu dispositivo. |
| Erro `cannot assign requested address` ao subir o brain | O Docker tentou publicar no IP Tailscale antes de a interface existir. `sudo systemctl restart docker` e suba de novo. |
| `Permission denied` ao ler `.env` ou `gcp-key.json` | O dono não é o uid 10001. `sudo chown 10001:10001 <arquivo>`. |
| O CI não entra na tailnet | Escopo ou etiqueta do cliente OAuth errados (faltando *Devices → Core → Write*, por exemplo), ou `tag:ci` ausente no `tagOwners`. |
| Docker não acha o arquivo e criou uma **pasta** | O arquivo montado não existia na hora do `up`. Apague a pasta e copie o arquivo de novo. |

## Recomeçar do zero {#recriar}

A VPS é descartável: os dados importantes estão no backup fora do servidor (T-083). Para recriar: reinstale a imagem Ubuntu 24.04 no painel do Contabo, siga este runbook de cima para baixo, restaure o backup do Mongo e remova o nó antigo no painel do Tailscale (**Machines**), para o novo reaproveitar o nome `juniper-brain`. Nunca use `down -v` num servidor com dados que não tenham backup.
