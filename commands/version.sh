#!/bin/zsh
################################################################################
# VERSION Command
# Exibe a versão do Juniper
################################################################################

version_run() {
    local user_name=$(_juniper_get_user_name)
    local version="3.0.0"
    local release_date="27/08/2026"
    local last_update=$(git -C "$HOME/.juniper" log -1 --format=%ad --date=format:'%d/%m/%Y %H:%M:%S' 2>/dev/null)
    [ -z "$last_update" ] && last_update="$release_date"

    cat << EOF
🌿 JUNIPER: Estou na versão $version, $user_name! 
    
    Última atualização: $last_update
    
    Fui criada por Petrus Rennan, no dia $release_date.
   

EOF
}

version_help() {
    cat << 'EOF'
  version, -v, --version
      Exibe a versão e informações do Juniper
      Exemplo: juniper version
EOF
}
