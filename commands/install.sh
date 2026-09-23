#!/bin/zsh
# ~/.juniper/commands/install.sh

install_run() {
    local user_name=$(_juniper_get_user_name)

    _juniper_say "Oi, $user_name! Estou verificando o ambiente..."
    echo "-------------------------------------------------------"

    local conf_example="$HOME/.juniper/.juniper.conf.example"
    if [ ! -f "$JUNIPER_CONFIG_FILE" ]; then
        if [ -f "$conf_example" ]; then
            cp "$conf_example" "$JUNIPER_CONFIG_FILE"
            _juniper_say "Arquivo de configuração criado em $JUNIPER_CONFIG_FILE."
        else
            _juniper_say "⚠️ Arquivo de exemplo $conf_example não encontrado."
        fi
    else
        _juniper_say "Arquivo de configuração já existe, mantendo o atual."
    fi

    # Validação simples de dependências básicas do sistema
    local deps=("git" "grep" "zsh")
    local missing=0

    for dep in "${deps[@]}"; do
        if ! command -v $dep &> /dev/null; then
            _juniper_say "❌ $dep não encontrado. Por favor, instale-o manualmente."
            missing=1
        else
            _juniper_say "$dep está instalado."
        fi
    done

    echo "\n-------------------------------------------------------"
    if [ $missing -eq 0 ]; then
        _juniper_say "Tudo pronto, $user_name! Estou operacional."
        _juniper_say "Tente: juniper search 'termo de busca'"
    else
        _juniper_say "⚠️ Algumas dependências básicas estão faltando, $user_name."
    fi
}

install_help() {
    cat << 'EOF'
install / setup
    Verifica se as dependências básicas do sistema (git, zsh) estão instaladas.
    Exemplo: juniper install
EOF
}
