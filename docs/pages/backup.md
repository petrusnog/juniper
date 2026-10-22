---
title: Backup da memória
group: Produção
order: 3
description: Como a memória da Juniper (o banco MongoDB) é copiada, criptografada e versionada num repositório privado do GitHub, e como restaurar.
status: guia
tags: backup restauração memória mongodb mongodump age criptografia git github repositório privado cron deploy amnésia chave
---

> **Nota:** o código está em `scripts/backup_memory.sh` e `scripts/restore_memory.sh`. A configuração (repositório privado, chaves e cron) é feita **uma vez** na VPS; sem ela o backup se limita a uma cópia local, que some junto com o servidor.

## Visão geral {#visao-geral}

Tudo que a Juniper sabe (conversas, memórias, perfil) está no **MongoDB** da VPS. Se o servidor se perder, ela teria uma amnésia total. O backup copia o banco `juniper`, **criptografa** e guarda **um único arquivo** (`juniper-memory.archive.gz.age`) num repositório git privado: cada backup é um commit, então a memória fica **versionada** e dá para voltar a qualquer dia.

```text
 VPS (Mongo) ── mongodump ──► age (chave PÚBLICA) ──► backups/repo/juniper-memory.archive.gz.age
                                                         │  git commit + push
                                                         ▼
                                       github.com/<você>/juniper-memory   (PRIVADO)
```

- **Quando roda:** antes de cada deploy (`scripts/deploy.sh`) e todo dia por um cron da VPS ([Agendar](#agendar)).
- **Onde fica o repositório:** dentro da pasta do projeto, em `~/juniper/backups/repo/`, mas é um repositório **separado** (o git do projeto o ignora). O repositório do projeto é público; o de backup nunca pode ser.
- **Criptografia:** com a chave pública do `age`, a VPS só consegue **cifrar**. A chave privada, que decifra, fica com você, fora do servidor: quem invadir a VPS ou o GitHub vê só bytes ilegíveis.
- **Sem criptografia, nada vai ao GitHub.** Sem `BACKUP_AGE_RECIPIENT`, o arquivo fica só numa cópia local (`backups/local/`, as 5 últimas, permissão `600`). Mandar em claro exige pedir: `BACKUP_ALLOW_PLAINTEXT=1`.

## Configurar (uma vez) {#configurar}

### 1. O repositório privado {#repo}

No GitHub, crie `juniper-memory` como **Private**, vazio (sem README). Em *Settings → Branches*, proteja `main` bloqueando **force push** e **exclusão**: assim, mesmo uma VPS comprometida não consegue apagar o histórico dos backups.

### 2. A chave de criptografia {#age}

No **seu computador** (não na VPS):

```bash
sudo apt install age            # ou: brew install age
age-keygen -o juniper-backup.key
# a saída mostra: Public key: age1...   (é essa linha que vai para a VPS)
```

> **Guarde o arquivo `juniper-backup.key` num gerenciador de senhas e numa cópia offline. Sem ele os backups não abrem, para ninguém, nunca.** Perder a chave é o mesmo que não ter backup. Ela nunca vai para a VPS nem para o repositório.

### 3. A chave de deploy do repositório de backup {#deploy-key}

Na VPS, **como `deploy`**, uma chave só para esse repositório (separada da chave do CI):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/juniper_backup -N "" -C "juniper-backup"
cat ~/.ssh/juniper_backup.pub          # copie
cat >> ~/.ssh/config <<'EOC'
Host github-juniper-memory
  HostName github.com
  User git
  IdentityFile ~/.ssh/juniper_backup
  IdentitiesOnly yes
EOC
chmod 600 ~/.ssh/config
ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts
```

No GitHub: repositório `juniper-memory` → *Settings → Deploy keys → Add deploy key*, cole a chave pública e marque **Allow write access**. Compare a impressão digital de `ssh-keygen -lf ~/.ssh/known_hosts` com a que o GitHub publica na [documentação de chaves SSH](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).

### 4. O `backup.env` na VPS {#backup-env}

```bash
sudo apt install age
cd ~/juniper
cat > backup.env <<'EOE'
BACKUP_REMOTE=git@github-juniper-memory:SEU-USUARIO/juniper-memory.git
BACKUP_AGE_RECIPIENT=age1...cole-a-chave-publica...
EOE
chmod 600 backup.env
```

O `backup.env` fica fora do git do projeto e fora da imagem Docker. Só `CHAVE=valor`: o script o carrega como shell.

### 5. Primeiro backup {#primeiro}

```bash
cd ~/juniper && ./scripts/backup_memory.sh manual
git -C backups/repo log --oneline        # um commit "backup (manual) ..."
```

No GitHub, o repositório `juniper-memory` deve mostrar o arquivo `juniper-memory.archive.gz.age` (ilegível, como deve ser).

### 6. Agendar {#agendar}

O `deploy.sh` já faz backup antes de cada deploy. Para o diário, `crontab -e` como `deploy`:

```cron
PATH=/usr/local/bin:/usr/bin:/bin
30 2 * * * cd $HOME/juniper && ./scripts/backup_memory.sh daily >> $HOME/juniper/backups/backup.log 2>&1
```

Às 02:30 no relógio da VPS (confira com `date`: ela costuma estar em UTC, não no fuso do agente), depois da faxina de logs das 00:10 e antes de uma eventual passada noturna da [Memória V2](adr-017.html), prevista para as 03:00. Confira no dia seguinte: `tail ~/juniper/backups/backup.log` e o último commit no GitHub.

## Códigos de saída {#saida}

| Código | Significa | No deploy |
|---|---|---|
| 0 | Backup feito (ou nada a copiar porque o Mongo não está rodando). | segue |
| 1 | O dump ou a criptografia falhou. | **cancela** |
| 4 | A cópia local existe, mas o push para o GitHub falhou. | segue, com aviso; o commit vai no próximo backup |

Sempre que o push falha, a cópia local (`backups/local/`) está lá. Sem `BACKUP_REMOTE` ou sem `BACKUP_AGE_RECIPIENT`, o script avisa em voz alta e fica só na cópia local.

## Restaurar {#restaurar}

> **Restaurar substitui o banco `juniper`** pelo que está no backup. Por isso o script exige `--yes`, e a Juniper (o `brain`) é parada durante o processo.

Com a VPS de pé e o repositório de backup em `~/juniper/backups/repo` (numa VPS nova: `git clone git@github-juniper-memory:SEU-USUARIO/juniper-memory.git ~/juniper/backups/repo`, depois de refazer a chave de deploy):

```bash
cd ~/juniper
# traga a chave privada por um canal seu, use e apague (nunca deixe na VPS):
scp seu-pc:juniper-backup.key /dev/shm/juniper-backup.key
./scripts/restore_memory.sh --identity /dev/shm/juniper-backup.key --yes
shred -u /dev/shm/juniper-backup.key
juniper memory list                      # confira
```

- **Uma versão específica:** `--commit <sha>` (veja `git -C backups/repo log`). Sem isso, restaura o mais recente.
- **Sem `--yes` nada é feito**; sem `--identity` (e o backup criptografado) o script recusa.

### Teste a restauração {#testar}

Um backup que nunca foi restaurado não conta. O CI faz uma **ida e volta** a cada mudança nesses scripts (grava um dado, faz o backup, apaga o dado, restaura e confere com Mongo e `age` de verdade). Faça também uma vez na prática, num Mongo de teste ou no ambiente de desenvolvimento, antes de precisar.

## O que o backup não leva {#fora}

- **Segredos:** `.env`, chave do Google e o `backup.env`. Ficam só na VPS (e no seu gerenciador de senhas); refaça-os num servidor novo pelo [runbook](runbook-vps.html).
- **O token dos clientes** (`juniper-state`): é recriado no primeiro boot, e os clientes precisam do novo.
- **Logs** e o **modelo de embeddings** (`juniper-logs`, `juniper-models`): o modelo baixa de novo e os logs vivem só 3 dias.
- Só o banco `juniper`: os usuários do Mongo vêm do `.env`.

## Privacidade e limites {#limites}

- **O que o GitHub vê:** bytes cifrados e os metadados dos commits (data, motivo, a imagem em uso). Nada do conteúdo.
- **Se a chave privada vazar,** todo o histórico de backups fica legível: o repositório precisa continuar privado, e a chave privada guardada como senha.
- **O histórico cresce.** Cada backup é um arquivo novo e cifrado (o git não consegue comprimir a diferença entre versões), então o tamanho do repositório é, em ordem de grandeza, o do banco vezes o número de backups. Para uma memória pessoal pequena isso dura anos; se passar de algumas centenas de MB, o procedimento é recomeçar o histórico (um repositório novo com o último backup) e arquivar o antigo.
- **Retenção:** o próprio histórico do git. Nada é apagado automaticamente.
- **A chave de deploy tem escrita no repositório de backup,** então uma VPS comprometida pode enviar lixo, mas, com a proteção de `main` (force push e exclusão bloqueados), não consegue apagar o que já existe.
