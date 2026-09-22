#!/bin/zsh
################################################################################
# DEPLOYFEATURE Command (DEPRECATED)
# Mantido apenas por compatibilidade. Use `juniper deploy` a partir de agora.
################################################################################

deployfeature_run() {
    echo "⚠️  'deployfeature' está depreciado. Use 'juniper deploy' a partir de agora."
    deploy_run "$@"
}

deployfeature_help() {
    cat << 'EOF'
  deployfeature (DEPRECATED)
      Comando depreciado, substituído por 'deploy'. Use: juniper deploy <id-feature> ...
EOF
}
