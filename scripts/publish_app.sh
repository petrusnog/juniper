#!/usr/bin/env bash
# Publica um APK do app Android para o cérebro servir (ADR-026). Roda na VPS, chamado pelo CI:
#
#   ssh host 'bash -s -- /tmp/app-release.apk <version_code> <version_name>' < scripts/publish_app.sh
#
# Também serve à mão: scripts/publish_app.sh caminho/do/app-release.apk 42 0.1.42
# Deixa em $JUNIPER_APP_DIR (padrão ~/juniper/app-release, a pasta que o compose monta no
# container) o app-release.apk e o latest.json. O APK entra primeiro e o latest.json por último,
# cada um por rename: o cérebro nunca oferece uma versão pela metade.
set -euo pipefail

APK="${1:-}"
CODE="${2:-}"
NAME="${3:-}"
[ -f "$APK" ] || { echo "uso: publish_app.sh <apk> <version_code> <version_name>" >&2; exit 2; }
[[ "$CODE" =~ ^[0-9]+$ ]] && [ "$CODE" -ge 1 ] || { echo "version_code inválido: $CODE" >&2; exit 2; }
[[ "$NAME" =~ ^[0-9A-Za-z._+-]+$ ]] || { echo "version_name inválido: $NAME" >&2; exit 2; }

DIR="${JUNIPER_APP_DIR:-$HOME/juniper/app-release}"
mkdir -p "$DIR"
chmod 755 "$DIR"   # o container lê com outro usuário (uid 10001)

SIZE="$(stat -c %s "$APK")"
SHA="$(sha256sum "$APK" | cut -d' ' -f1)"
[ "$SIZE" -gt 0 ] && [ "$SIZE" -le $((200 * 1024 * 1024)) ] || { echo "tamanho fora do limite: $SIZE" >&2; exit 2; }

# Um APK com o mesmo código que o publicado, ou com código menor, não substitui nada: o Android
# recusa "voltar" de versão, e o app ficaria preso oferecendo um arquivo que não instala.
if [ -f "$DIR/latest.json" ]; then
  CURRENT="$(sed -n 's/.*"version_code": *\([0-9][0-9]*\).*/\1/p' "$DIR/latest.json" | head -n 1)"
  if [ -n "$CURRENT" ] && [ "$CODE" -le "$CURRENT" ]; then
    echo "já publicado: versão $CURRENT >= $CODE, nada a fazer"
    exit 0
  fi
fi

TMP_APK="$(mktemp "$DIR/.apk.XXXXXX")"
TMP_META="$(mktemp "$DIR/.meta.XXXXXX")"
trap 'rm -f "$TMP_APK" "$TMP_META"' EXIT
cp "$APK" "$TMP_APK"
printf '{"version_code": %s, "version_name": "%s", "sha256": "%s", "size": %s, "released_at": "%s"}\n' \
  "$CODE" "$NAME" "$SHA" "$SIZE" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$TMP_META"
chmod 644 "$TMP_APK" "$TMP_META"
mv "$TMP_APK" "$DIR/app-release.apk"
mv "$TMP_META" "$DIR/latest.json"
trap - EXIT
echo "app publicado: versão $NAME (código $CODE, $SIZE bytes, sha256 ${SHA:0:12}…)"
