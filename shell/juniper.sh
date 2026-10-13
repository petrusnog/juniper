#!/bin/zsh
################################################################################
# JUNIPER - Ponto de entrada
# Carrega o núcleo do sistema e expõe o comando `juniper` no shell
################################################################################

[ -f "$HOME/.juniper/core/loader.sh" ] && source "$HOME/.juniper/core/loader.sh"

juniper() {
    _juniper_dispatch "$@"
}
