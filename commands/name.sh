#!/bin/zsh
################################################################################
# NAME Command
# Define ou exibe como o Juniper deve chamar o usuário
################################################################################

name_run() {
    if [ -z "$1" ]; then
        local current_name=$(_juniper_get_user_name)
        _juniper_say "Hoje eu te chamo de: $current_name"
        _juniper_say "Para mudar, use: juniper name \"Seu Nome\""
        return 0
    fi

    _juniper_config_set "user_name" "$*"
    _juniper_say "Combinado! De agora em diante vou te chamar de $*."
}

name_help() {
    cat << 'EOF'
  name <nome>
      Define como o Juniper deve te chamar. Sem argumentos, exibe o nome atual.
      Exemplo: juniper name "Petrus"
EOF
}
