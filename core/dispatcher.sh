#!/bin/zsh
################################################################################
# CORE DISPATCHER
# Sistema de roteamento de comandos
################################################################################

# Mapeamento de aliases para comandos reais (global)
typeset -gA JUNIPER_ALIASES
JUNIPER_ALIASES=(
    ["grep"]="search"
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
        _juniper_say "❌ Comando desconhecido: $command"
        echo ""
        help_run
        return 1
    fi
}

# Saudação inicial
_juniper_greeting() {
    local user_name=$(_juniper_config_get "user_name")
    [ -z "$user_name" ] && _juniper_ask_user_name && user_name=$(_juniper_config_get "user_name")
    [ -z "$user_name" ] && user_name="usuário"

    echo ""
    _juniper_say "Olá, $user_name! Estou pronta pra te ajudar."
    _juniper_say "Digite 'juniper help' para ver os comandos disponíveis."
    echo ""
}

# Pergunta o nome do usuário na primeira execução e salva a resposta
_juniper_ask_user_name() {
    local suggested_name=$(git config --global user.name 2>/dev/null)
    local resp

    _juniper_say "Oi! Ainda não sei seu nome."
    if [ -n "$suggested_name" ]; then
        read "resp?🌿 JUNIPER: Posso te chamar de ${suggested_name}? (enter para confirmar, ou digite outro nome): "
        [ -z "$resp" ] && resp="$suggested_name"
    else
        read "resp?🌿 JUNIPER: Como posso te chamar? "
    fi

    if [ -n "$resp" ]; then
        _juniper_config_set "user_name" "$resp"
        _juniper_say "Prazer, $resp!"
        echo ""
        return 0
    fi
    return 1
}