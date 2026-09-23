#!/bin/zsh
################################################################################
# TEMPLATE Command
# [Descrição do seu comando aqui]
################################################################################

# Função principal do comando
# Esta função é executada quando o usuário chama: juniper seucomando [args]
seucomando_run() {
    # Validação de argumentos (opcional)
    if [ -z "$1" ]; then
        echo "❌ Uso: juniper seucomando <argumento>"
        return 1
    fi
    
    local argumento="$1"
    
    # Sua lógica aqui
    echo "🌿 Executando comando com: $argumento"
    
    # Exemplo: executar comando git
    # git status
    
    # Exemplo: variáveis locais
    # local resultado=$(git rev-parse --abbrev-ref HEAD)
    # echo "Branch atual: $resultado"
}

# Função de ajuda do comando
# Esta é exibida quando o usuário executa: juniper help
seucomando_help() {
    cat << 'EOF'
  seucomando, alias1, alias2 <argumento>
      [Descrição detalhada do que o comando faz]
      Exemplo: juniper seucomando valor
EOF
}

# ============================================================================ #
# INSTRUÇÕES DE USO:
# ============================================================================ #
# 
# 1. Copie este arquivo para ~/.juniper/commands/ com o nome do seu comando:
#    cp template.sh ~/.juniper/commands/meucomando.sh
#
# 2. Substitua todas as ocorrências de "seucomando" pelo nome do seu comando
#
# 3. Implemente a lógica em seucomando_run()
#
# 4. Atualize a documentação em seucomando_help()
#
# 5. (Opcional) Adicione aliases em ~/.juniper/core/dispatcher.sh:
#    JUNIPER_ALIASES=(
#        ...
#        ["alias1"]="meucomando"
#        ["alias2"]="meucomando"
#    )
#
# 6. Recarregue o shell:
#    source ~/.juniper.sh
#
# 7. Teste seu comando:
#    juniper meucomando teste
#    juniper help
#
# ============================================================================ #
