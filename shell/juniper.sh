#!/bin/zsh
################################################################################
# JUNIPER - Ponto de entrada
# Carrega o núcleo do sistema e expõe o comando `juniper` no shell
################################################################################

JUNIPER_SHELL_DIR="${JUNIPER_SHELL_DIR:-${${(%):-%x}:A:h}}"
export JUNIPER_SHELL_DIR
[ -f "$JUNIPER_SHELL_DIR/core/loader.sh" ] && source "$JUNIPER_SHELL_DIR/core/loader.sh"

juniper() {
    _juniper_dispatch "$@"
}
