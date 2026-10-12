#!/bin/zsh
################################################################################
# search Command
# Busca commits por padrão
################################################################################

search_run() {
    if [ -z "$1" ]; then
        _juniper_say "Ops, $(_juniper_get_user_name)! Uso: juniper search <termo> [termo2 ...]"
        return 1
    fi

    # Cada termo vira um --grep; o git combina múltiplos --grep com OR
    local grep_args=()
    local term
    for term in "$@"; do
        grep_args+=(--grep="$term")
    done

    local results=$(git log --color=always --pretty=format:$'(\033[38;5;218m%ad\033[0m) | HASH: \033[92m%H\033[0m | COMMIT: \033[92m%s\033[0m | AUTHOR: \033[38;5;220m%an\033[0m' \
    --date=format:'%d/%m/%Y %H:%M' "${grep_args[@]}")

    if [ -z "$results" ]; then
        _juniper_say "🔍 Não encontrei nada com \"$*\", $(_juniper_get_user_name). Tenta outro termo?"
        return 1
    fi

    echo "$results"
}

search_help() {
    cat << 'EOF'
  search, grep <termo> [termo2 ...]
      Busca commits que contenham qualquer um dos termos especificados
      Exemplo: juniper search 4911
      Exemplo: juniper search 6531 7432
EOF
}