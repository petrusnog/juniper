#!/bin/zsh
################################################################################
# CORE DISPATCHER
# Sistema de roteamento de comandos
################################################################################

# Mapeamento de aliases para comandos reais (global)
typeset -gA JUNIPER_ALIASES
JUNIPER_ALIASES=(
    ["grep"]="gitgrep"
    ["-h"]="help"
    ["--help"]="help"
    ["-v"]="version"
    ["--version"]="version"
)

# Carrega todos os comandos disponíveis
_juniper_load_commands() {
    for cmd_file in ~/.juniper/commands/*.sh; do
        [ -f "$cmd_file" ] && source "$cmd_file"
    done
}

# Função principal de dispatch
_juniper_dispatch() {
    local command="$1"
    
    # Caso especial: comando vazio mostra saudação
    if [ -z "$command" ]; then
        _juniper_greeting
        return 0
    fi
    
    shift
    
    # Resolve alias
    if [ -n "${JUNIPER_ALIASES[$command]}" ]; then
        command="${JUNIPER_ALIASES[$command]}"
    fi
    
    # Tenta executar o comando
    local run_func="${command}_run"
    if declare -f "$run_func" > /dev/null; then
        _juniper_log_info "Comando executado: $command $*"
        $run_func "$@"
    else
        _juniper_log_error "Comando desconhecido: $command $*"
        echo "❌ Comando desconhecido: $command"
        echo ""
        help_run
        return 1
    fi
}

# Saudação inicial
_juniper_greeting() {
    cat << 'EOF'
    
    🌿 Olá, Rennan! Estou pronta pra te ajudar.
    
    Digite 'juniper help' para ver os comandos disponíveis.
    
EOF
}
