#!/bin/zsh
################################################################################
# CORE CONFIG
# Persistência de preferências do usuário (~/.juniper/.juniper.conf)
################################################################################

JUNIPER_CONFIG_FILE="$HOME/.juniper/.juniper.conf"

# Uso: _juniper_config_get <chave>
_juniper_config_get() {
    local key="$1"
    [ -f "$JUNIPER_CONFIG_FILE" ] || return 0
    grep "^${key}=" "$JUNIPER_CONFIG_FILE" 2>/dev/null | tail -n1 | cut -d'=' -f2-
}

# Uso: _juniper_config_set <chave> <valor>
_juniper_config_set() {
    local key="$1"
    local value="$2"
    touch "$JUNIPER_CONFIG_FILE"
    if grep -q "^${key}=" "$JUNIPER_CONFIG_FILE" 2>/dev/null; then
        sed -i.bak "s/^${key}=.*/${key}=${value}/" "$JUNIPER_CONFIG_FILE" && rm -f "${JUNIPER_CONFIG_FILE}.bak"
    else
        echo "${key}=${value}" >> "$JUNIPER_CONFIG_FILE"
    fi
}

# Retorna o nome salvo do usuário, com fallback para git config e "usuário"
_juniper_get_user_name() {
    local saved_name=$(_juniper_config_get "user_name")
    if [ -n "$saved_name" ]; then
        echo "$saved_name"
        return 0
    fi

    local git_name=$(git config --global user.name 2>/dev/null)
    if [ -n "$git_name" ]; then
        echo "$git_name"
        return 0
    fi

    echo "usuário"
}
