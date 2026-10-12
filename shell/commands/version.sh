#!/bin/zsh
################################################################################
# VERSION Command
# Exibe a versão do Juniper
################################################################################

version_run() {
    local user_name=$(_juniper_get_user_name)
    local root="${JUNIPER_SHELL_DIR:-$HOME/.juniper/shell}"
    root="${root:h}"
    local version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/brain/__init__.py" 2>/dev/null | head -1)
    [ -z "$version" ] && version="desconhecida"
    local release_date="27/08/2026"
    local last_update=$(git -C "$root" log -1 --format=%ad --date=format:'%d/%m/%Y %H:%M:%S' 2>/dev/null)
    [ -z "$last_update" ] && last_update="$release_date"

    _juniper_say "Estou na versão $version, $user_name!"
    echo ""
    echo "    Última atualização: $last_update"
    echo "    Fui criada por Petrus Rennan, no dia $release_date."
    echo ""
}

version_help() {
    cat << 'EOF'
  version, -v, --version
      Exibe a versão e informações do Juniper
      Exemplo: juniper version
EOF
}
