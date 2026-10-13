#!/bin/zsh
################################################################################
# CORE LOGGER
# Sistema de logs reutilizável do Juniper - grava em ~/.juniper/juniper.log
################################################################################

JUNIPER_LOG_FILE="$HOME/.juniper/juniper.log"

# Uso: _juniper_log <LEVEL> <mensagem>
_juniper_log() {
    local level="${1:-INFO}"
    shift
    local message="$*"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] [$level] $message" >> "$JUNIPER_LOG_FILE"
}

_juniper_log_info()  { _juniper_log "INFO"  "$*"; }
_juniper_log_warn()  { _juniper_log "WARN"  "$*"; }
_juniper_log_error() { _juniper_log "ERROR" "$*"; }
