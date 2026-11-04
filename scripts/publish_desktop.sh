#!/usr/bin/env bash
# Publica o instalador do app desktop para o cérebro servir (ADR-033, no molde de publish_app.sh).
#
#   ssh host 'bash -s -- /tmp/Juniper-Setup.exe <version_code> <version_name>' < scripts/publish_desktop.sh
#
# Deixa em $JUNIPER_APP_DIR (a mesma pasta do APK) o Juniper-Setup.exe e o desktop-latest.json. O
# instalador entra primeiro e o json por último, cada um por rename: o cérebro nunca oferece uma
# versão pela metade. O app confere o SHA-256 e a assinatura antes de executar qualquer coisa.
set -euo pipefail

EXE="${1:-}"
CODE="${2:-}"
NAME="${3:-}"
[ -f "$EXE" ] || { echo "uso: publish_desktop.sh <instalador> <version_code> <version_name>" >&2; exit 2; }
[[ "$CODE" =~ ^[0-9]+$ ]] && [ "$CODE" -ge 1 ] || { echo "version_code inválido: $CODE" >&2; exit 2; }
[[ "$NAME" =~ ^[0-9A-Za-z._+-]+$ ]] || { echo "version_name inválido: $NAME" >&2; exit 2; }

DIR="${JUNIPER_APP_DIR:-$HOME/juniper/app-release}"
mkdir -p "$DIR"
chmod 755 "$DIR"   # o container lê com outro usuário (uid 10001)

SIZE="$(stat -c %s "$EXE")"
SHA="$(sha256sum "$EXE" | cut -d' ' -f1)"
[ "$SIZE" -gt 0 ] && [ "$SIZE" -le $((300 * 1024 * 1024)) ] || { echo "tamanho fora do limite: $SIZE" >&2; exit 2; }

if [ -f "$DIR/desktop-latest.json" ]; then
  CURRENT="$(sed -n 's/.*"version_code": *\([0-9][0-9]*\).*/\1/p' "$DIR/desktop-latest.json" | head -n 1)"
  [ -n "$CURRENT" ] || { echo "$DIR/desktop-latest.json está ilegível; apague-o e publique de novo" >&2; exit 1; }
  if [ "$CODE" -eq "$CURRENT" ]; then
    echo "já publicado: versão $CURRENT, nada a fazer"
    exit 0
  fi
  if [ "$CODE" -lt "$CURRENT" ]; then
    echo "recusado: a versão $CODE é menor que a publicada ($CURRENT)" >&2
    exit 1
  fi
fi

TMP_EXE="$(mktemp "$DIR/.exe.XXXXXX")"
TMP_META="$(mktemp "$DIR/.meta.XXXXXX")"
trap 'rm -f "$TMP_EXE" "$TMP_META"' EXIT
cp "$EXE" "$TMP_EXE"
printf '{"version_code": %s, "version_name": "%s", "sha256": "%s", "size": %s, "released_at": "%s"}\n' \
  "$CODE" "$NAME" "$SHA" "$SIZE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$TMP_META"
chmod 644 "$TMP_EXE" "$TMP_META"
mv "$TMP_EXE" "$DIR/Juniper-Setup.exe"
mv "$TMP_META" "$DIR/desktop-latest.json"
trap - EXIT
echo "desktop publicado: versão $NAME (código $CODE, $SIZE bytes, sha256 ${SHA:0:12}…)"
