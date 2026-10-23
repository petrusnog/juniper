#!/usr/bin/env bash
# Restaura a memória da Juniper a partir do repositório de backup (backups/repo).
#
#   scripts/restore_memory.sh --identity <chave-privada-age> [--commit <ref>] --yes
#
# SUBSTITUI as coleções do banco `juniper` pelo que está no backup (mongorestore --drop). Por isso
# exige --yes. A chave privada age é a que você guardou fora da VPS: ela só é usada aqui, para ler.
# Para restaurar numa VPS nova: clone o repositório de backup em ./backups/repo antes.
set -euo pipefail

cd "$(dirname "$0")/.."
IDENTITY=""
REF=""
YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --identity) IDENTITY="${2:?falta o arquivo da chave}"; shift 2 ;;
    --commit) REF="${2:?falta o commit}"; shift 2 ;;
    --yes) YES=1; shift ;;
    *) echo "uso: scripts/restore_memory.sh --identity <chave-age> [--commit <ref>] --yes" >&2; exit 2 ;;
  esac
done

if [ -f backup.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./backup.env
  set +a
fi
BACKUP_DB="${BACKUP_DB:-juniper}"
# shellcheck disable=SC2206
COMPOSE=(docker compose -f docker-compose.prod.yml ${COMPOSE_EXTRA:-})
REPO=backups/repo
[ -d "$REPO/.git" ] || { echo "não há repositório de backup em $REPO" >&2; exit 2; }

git -C "$REPO" fetch -q origin 2>/dev/null || echo "restore: sem acesso ao GitHub, usando o que há no disco" >&2
if [ -z "$REF" ]; then
  REF="origin/main"
  git -C "$REPO" rev-parse -q --verify "$REF" >/dev/null 2>&1 || REF="HEAD"
fi
file="$(git -C "$REPO" ls-tree --name-only "$REF" | grep -E '^juniper-memory\.archive\.gz(\.age)?$' | head -n 1 || true)"
[ -n "$file" ] || { echo "restore: não há arquivo de backup em $REF" >&2; exit 2; }
when="$(git -C "$REPO" log -1 --format='%h %cI %s' "$REF")"

echo "restore: vai SUBSTITUIR o banco '$BACKUP_DB' pelo backup $when ($file)"
if [ "$YES" != "1" ]; then
  echo "restore: nada foi feito. Rode de novo com --yes para confirmar." >&2
  exit 2
fi

case "$file" in
  *.age)
    [ -n "$IDENTITY" ] && [ -r "$IDENTITY" ] || { echo "restore: o backup é criptografado: passe --identity <chave-privada-age>" >&2; exit 2; }
    command -v age >/dev/null || { echo "restore: o 'age' não está instalado" >&2; exit 2; }
    reader=(age -d -i "$IDENTITY") ;;
  *) reader=(cat) ;;
esac

"${COMPOSE[@]}" stop brain
git -C "$REPO" show "$REF:$file" | "${reader[@]}" | "${COMPOSE[@]}" exec -T mongo sh -c \
  "mongorestore --quiet --archive --gzip --drop --nsInclude '$BACKUP_DB.*' -u \"\$MONGO_INITDB_ROOT_USERNAME\" -p \"\$MONGO_INITDB_ROOT_PASSWORD\" --authenticationDatabase admin"
"${COMPOSE[@]}" up -d brain
echo "restore: concluído. Confira com: juniper memory list"
