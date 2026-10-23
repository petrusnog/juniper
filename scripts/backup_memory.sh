#!/usr/bin/env bash
# Backup da memória da Juniper (ADR-013, seção 8; ADR-017, seção 12). Roda na VPS, como `deploy`.
#
#   scripts/backup_memory.sh [motivo]      ex.: pre-deploy, daily, manual
#
# O que faz:
#   1. mongodump do banco `juniper` (conversas, memórias, perfil...) dentro do contêiner do Mongo
#   2. criptografa com `age` para a chave PÚBLICA de backup.env (a chave privada nunca fica na VPS)
#   3. guarda uma cópia local (backups/local/, as 5 últimas)
#   4. grava o arquivo único `juniper-memory.archive.gz.age` num repositório git (backups/repo/),
#      faz commit e push para o repositório PRIVADO do GitHub (BACKUP_REMOTE)
#
# Sem BACKUP_AGE_RECIPIENT o arquivo NUNCA vai ao GitHub: só a cópia local fica (a memória tem o
# texto das suas conversas). Para mandar sem criptografia é preciso pedir: BACKUP_ALLOW_PLAINTEXT=1.
#
# Configuração em ./backup.env (permissão 600, fora do git):
#   BACKUP_REMOTE=git@github-juniper-memory:petrusnog/juniper-memory.git
#   BACKUP_AGE_RECIPIENT=age1...
#
# Códigos de saída: 0 ok; 1 o dump ou a criptografia falhou (o deploy NÃO deve seguir);
#                   4 a cópia local existe mas o push falhou (o deploy segue, com aviso).
set -euo pipefail

REASON="${1:-manual}"
cd "$(dirname "$0")/.."

if [ -f backup.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./backup.env
  set +a
fi
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
BACKUP_AGE_RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
BACKUP_ALLOW_PLAINTEXT="${BACKUP_ALLOW_PLAINTEXT:-}"
BACKUP_DB="${BACKUP_DB:-juniper}"

# COMPOSE_EXTRA: argumentos a mais para o compose (o CI usa `--env-file`); vazio na VPS.
# shellcheck disable=SC2206
COMPOSE=(docker compose -f docker-compose.prod.yml ${COMPOSE_EXTRA:-})
BACKUPS=backups
LOCAL="$BACKUPS/local"
REPO="$BACKUPS/repo"
KEEP_LOCAL=5
ENC_NAME=juniper-memory.archive.gz.age
PLAIN_NAME=juniper-memory.archive.gz

say() { echo "backup: $*"; }
die() { echo "backup: $*" >&2; exit 1; }

if ! "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx mongo; then
  say "mongo não está rodando: nada a copiar"
  exit 0
fi

install -d -m 700 "$BACKUPS" "$LOCAL"
tmp="$(mktemp "$BACKUPS/.dump.XXXXXX")"
trap 'rm -f "$tmp" "$tmp.enc"' EXIT

# 1. dump (só o banco da Juniper: não leva os usuários do Mongo)
if ! "${COMPOSE[@]}" exec -T mongo sh -c \
  "mongodump --quiet --archive --gzip --db '$BACKUP_DB' -u \"\$MONGO_INITDB_ROOT_USERNAME\" -p \"\$MONGO_INITDB_ROOT_PASSWORD\" --authenticationDatabase admin" \
  > "$tmp"; then
  die "o mongodump falhou"
fi
[ -s "$tmp" ] || die "o mongodump não devolveu nada"

# 2. criptografia
payload="$tmp"
name="$PLAIN_NAME"
sendable=0
if [ -n "$BACKUP_AGE_RECIPIENT" ]; then
  command -v age >/dev/null || die "o 'age' não está instalado (apt install age)"
  age -r "$BACKUP_AGE_RECIPIENT" < "$tmp" > "$tmp.enc" || die "a criptografia falhou"
  [ -s "$tmp.enc" ] || die "a criptografia não devolveu nada"
  payload="$tmp.enc"
  name="$ENC_NAME"
  sendable=1
elif [ "$BACKUP_ALLOW_PLAINTEXT" = "1" ]; then
  sendable=1
fi

# 3. cópia local (sempre: se o push falhar, a cópia está aqui)
stamp="$(date -u +%Y%m%d-%H%M%S)-$$"   # o pid evita que dois backups no mesmo segundo se sobrescrevam
local_copy="$LOCAL/memory-$stamp.${name#juniper-memory.}"
cp "$payload" "$local_copy"
chmod 600 "$local_copy"
# shellcheck disable=SC2012
ls -1t "$LOCAL"/memory-* | tail -n +$((KEEP_LOCAL + 1)) | xargs -r rm --
say "cópia local: $local_copy ($(du -h "$local_copy" | cut -f1))"

if [ -z "$BACKUP_REMOTE" ] || [ "$sendable" != "1" ]; then
  if [ -z "$BACKUP_REMOTE" ]; then
    say "AVISO: BACKUP_REMOTE não está configurado (backup.env): só existe a cópia local, que some junto com a VPS" >&2
  else
    say "AVISO: sem BACKUP_AGE_RECIPIENT o arquivo não vai ao GitHub (a memória tem as suas conversas): só a cópia local" >&2
  fi
  exit 0
fi

# 4. repositório git (backups/repo) e push
if [ ! -d "$REPO/.git" ]; then
  git clone -q "$BACKUP_REMOTE" "$REPO" 2>/dev/null || {
    echo "backup: não consegui clonar o repositório de backup (confira BACKUP_REMOTE e a chave de deploy)" >&2
    exit 4
  }
fi
git -C "$REPO" rev-parse -q --verify HEAD >/dev/null 2>&1 || git -C "$REPO" symbolic-ref HEAD refs/heads/main

cp "$payload" "$REPO/$name"
chmod 600 "$REPO/$name"
for other in "$ENC_NAME" "$PLAIN_NAME"; do   # trocar de formato não deixa o outro arquivo para trás
  [ "$other" = "$name" ] || rm -f "$REPO/$other"
done
git -C "$REPO" add -A
image="$(grep '^JUNIPER_IMAGE=' .env 2>/dev/null | tail -n 1 | cut -d: -f2- || true)"
git -C "$REPO" -c user.name="Juniper Backup" -c user.email="backup@juniper.invalid" \
  commit -q -m "backup ($REASON) $(date -u +%Y-%m-%dT%H:%M:%SZ)${image:+ imagem $image}" \
  || say "nada novo para registrar"
if git -C "$REPO" push -q origin HEAD:refs/heads/main 2>/dev/null; then
  say "enviado ao repositório de backup ($(git -C "$REPO" rev-parse --short HEAD))"
else
  echo "backup: AVISO: o push falhou; o commit fica no repositório local e vai junto no próximo backup" >&2
  exit 4
fi
