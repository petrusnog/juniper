#!/usr/bin/env bash
# Deploy na VPS (ADR-013, seção 5). Chamado pelo CI por SSH: scripts/deploy.sh <sha-completo>
#
#   1. atualiza os arquivos do repositório para o commit (o .env, o config.yaml e certs/ ficam)
#   2. faz um mongodump de segurança (se o Mongo estiver de pé)
#   3. troca JUNIPER_IMAGE no .env para a imagem do SHA, puxa e recria os serviços
#   4. espera o brain ficar "healthy"; se não ficar, volta para a imagem anterior
#
# Nunca roda `down -v`: o deploy não apaga volumes. Também serve para rodar à mão na VPS.
set -euo pipefail

SHA="${1:-}"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "uso: scripts/deploy.sh <sha de 40 caracteres>" >&2; exit 2; }

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "falta o .env em $(pwd)" >&2; exit 2; }

REPO_IMAGE="${JUNIPER_REPO_IMAGE:-ghcr.io/petrusnog/juniper}"
IMAGE="$REPO_IMAGE:$SHA"
COMPOSE=(docker compose -f docker-compose.prod.yml)
KEEP_BACKUPS=5
HEALTH_TRIES="${HEALTH_TRIES:-18}"        # x HEALTH_INTERVAL = 90 s
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"

# Um deploy por vez (o CI já serializa, mas isto protege uma execução manual junto com a do CI).
exec 9>/tmp/juniper-deploy.lock
flock -n 9 || { echo "já existe um deploy em andamento" >&2; exit 3; }

set_image() {  # grava JUNIPER_IMAGE no .env (o compose lê dali), mantendo o arquivo em 600
  local tmp
  tmp="$(mktemp .env.XXXXXX)"
  { grep -v '^JUNIPER_IMAGE=' .env || true; echo "JUNIPER_IMAGE=$1"; } > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" .env
}

wait_healthy() {
  local status
  for _ in $(seq 1 "$HEALTH_TRIES"); do
    status="$(docker inspect -f '{{.State.Health.Status}}' juniper-brain 2>/dev/null || true)"
    [ "$status" = "healthy" ] && return 0
    sleep "$HEALTH_INTERVAL"
  done
  return 1
}

backup_mongo() {
  if ! "${COMPOSE[@]}" ps --status running --services 2>/dev/null | grep -qx mongo; then
    echo "mongo não está rodando: sem backup (primeiro deploy?)"
    return 0
  fi
  install -d -m 700 backups
  local out="backups/mongo-$(date +%Y%m%d-%H%M%S).archive.gz"
  if ! "${COMPOSE[@]}" exec -T mongo sh -c \
    'mongodump --quiet --archive --gzip -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' \
    > "$out"; then
    rm -f "$out"
    echo "backup do Mongo falhou: deploy cancelado (nada foi alterado)" >&2
    return 1
  fi
  chmod 600 "$out"
  echo "backup: $out ($(du -h "$out" | cut -f1))"
  # mantém só os últimos KEEP_BACKUPS
  # shellcheck disable=SC2012
  ls -1t backups/mongo-*.archive.gz | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm --
}

# Tudo dentro de uma função: o `git reset` abaixo troca este arquivo, e o bash não deve ler o resto
# de um script que mudou no meio da execução.
main() {
  PREV="$(grep '^JUNIPER_IMAGE=' .env | tail -n 1 | cut -d= -f2- || true)"
  echo "deploy $SHA (anterior: ${PREV:-nenhuma imagem do registro})"

  git fetch -q origin "$SHA"
  git reset -q --hard "$SHA"        # compose e docker/ no mesmo commit; arquivos não versionados ficam

  backup_mongo

  set_image "$IMAGE"
  "${COMPOSE[@]}" pull brain
  "${COMPOSE[@]}" up -d             # nunca "down -v"

  if wait_healthy; then
    echo "deploy ok: $SHA"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi

  echo "o brain não ficou saudável em $((HEALTH_TRIES * HEALTH_INTERVAL)) s" >&2
  "${COMPOSE[@]}" logs --tail=40 brain >&2 || true
  if [ -n "$PREV" ]; then
    echo "voltando para $PREV" >&2
    set_image "$PREV"
    "${COMPOSE[@]}" up -d
    wait_healthy && echo "rollback ok" >&2 || echo "ATENÇÃO: o rollback também não ficou saudável" >&2
  else
    echo "não há imagem anterior do registro para voltar" >&2
  fi
  exit 1
}

main
