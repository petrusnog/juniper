#!/bin/zsh
################################################################################
# HELP Command
# Exibe ajuda dos comandos
################################################################################

help_run() {
    cat << 'EOF'
🌿 JUNIPER: Olá, sou Juniper, sua assistente de automações favorita!

Uso: juniper <comando> [argumentos]

Comandos disponíveis:

EOF
    
    # Lista ajuda de cada comando dinamicamente
    for cmd_file in ~/.juniper/commands/*.sh; do
        if [ -f "$cmd_file" ] && [ "$(basename "$cmd_file")" != "help.sh" ]; then
            source "$cmd_file"
            local cmd_name=$(basename "$cmd_file" .sh)
            local help_func="${cmd_name}_help"
            if declare -f "$help_func" > /dev/null; then
                $help_func
                echo ""
            fi
        fi
    done
    
    # Adiciona a própria ajuda do help por último
    help_help
}

help_help() {
    cat << 'EOF'
  help, --help, -h
      Exibe esta mensagem de ajuda
EOF
}
