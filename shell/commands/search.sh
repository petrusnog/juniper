#!/bin/zsh
################################################################################
# search Command
# Busca commits por padrão
################################################################################

search_run() {
    if [ -z "$1" ]; then
        _juniper_say "Ops, $(_juniper_get_user_name)! Uso: juniper search <termo_de_busca>"
        return 1
    fi

    local results=$(git log --color=always --pretty=format:$'(\033[38;5;218m%ad\033[0m) | HASH: \033[92m%H\033[0m | COMMIT: \033[92m%s\033[0m | AUTHOR: \033[38;5;220m%an\033[0m' \
    --date=format:'%d/%m/%Y %H:%M' --grep="$1")

    if [ -z "$results" ]; then
        _juniper_say "🔍 Não encontrei nada com \"$1\", $(_juniper_get_user_name). Tenta outro termo?"
        return 1
    fi

    echo "$results"
}

search_help() {
    cat << 'EOF'
  search, grep <termo>
      Busca commits que contenham o termo especificado
      Exemplo: juniper search 4911
EOF
}