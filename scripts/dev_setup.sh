#!/usr/bin/env bash
# Prepara a pasta para o ambiente Docker de desenvolvimento (docker-compose.dev.yml).
#
#   ./scripts/dev_setup.sh
#
# Cria o que o compose espera encontrar. Se algo disso não existir, o Docker o cria sozinho,
# como root (e, no caso de .env e config.yaml, como PASTA), e o brain não consegue escrever o
# token. Seguro de rodar de novo: não sobrescreve nada que já existe.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .juniper-dev/state .juniper-dev/logs certs

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Criei o .env a partir do .env.example."
fi

if [ ! -f config.yaml ]; then
  cp config.yaml.example config.yaml
  echo "Criei o config.yaml a partir do config.yaml.example (é a sua configuração pessoal)."
fi

missing=0
for key in JUNIPER_MEMORY__MONGO__USERNAME JUNIPER_MEMORY__MONGO__PASSWORD; do
  if ! grep -Eq "^${key}=.+" .env; then
    echo "Falta definir ${key} no .env (o Mongo usa esse valor para criar o usuário do banco)."
    missing=1
  fi
done

if [ "$(id -u)" != "1000" ]; then
  echo "Seu uid é $(id -u), não 1000: rode 'export UID' antes do 'docker compose up --build'."
fi

if [ "$missing" = 1 ]; then
  exit 1
fi
echo "Pronto. Suba com: docker compose -f docker-compose.dev.yml up -d --build"
