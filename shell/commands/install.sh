#!/bin/zsh
################################################################################
# INSTALL Command
# Instala o cérebro da Juniper (ambiente virtual + dependências) sem configuração
# manual, mesmo em máquina sem Python. A lógica está em scripts/install.sh.
################################################################################

install_run() {
    local user_name=$(_juniper_get_user_name)
    local shell_dir="${JUNIPER_SHELL_DIR:-$HOME/.juniper/shell}"
    local script="${shell_dir:h}/scripts/install.sh"

    _juniper_say "Oi, $user_name! Vou deixar tudo pronto por aqui..."

    # Preferências do shell (nome do usuário etc.)
    local conf_example="${shell_dir:h}/.juniper.conf.example"
    if [ ! -f "$JUNIPER_CONFIG_FILE" ] && [ -f "$conf_example" ]; then
        cp "$conf_example" "$JUNIPER_CONFIG_FILE"
    fi

    if [ ! -f "$script" ]; then
        _juniper_say "❌ Não encontrei $script. A Juniper está em ~/.juniper?"
        return 1
    fi
    if ! command -v bash >/dev/null 2>&1; then
        _juniper_say "❌ O bash é necessário para instalar."
        return 1
    fi

    bash "$script" "$@" || return $?

    # Vale já nesta sessão, sem abrir terminal novo.
    case ":$PATH:" in
        *":$HOME/.local/bin:"*) ;;
        *) export PATH="$HOME/.local/bin:$PATH" ;;
    esac
}

install_help() {
    cat << 'EOF'
  install [--dev] [--clean] [--check] [--yes] [--memory=file|mongo]
      Instala a Juniper (ambiente virtual + dependências), mesmo sem Python na máquina.
      Reaproveita o que já está saudável e reinstala só o que estiver com defeito.
      --dev    inclui pytest, ruff e mypy
      --clean  recria o ambiente do zero
      --check  só verifica, sem alterar nada
      --memory=file|mongo  onde guardar a memória; mongo exige o Docker instalado e rodando
      Exemplo: juniper install
EOF
}
