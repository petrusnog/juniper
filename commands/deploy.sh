#!/bin/zsh
################################################################################
# DEPLOY Command
# Deploy automatizado para branches de feature (develop/stage)
################################################################################

# Função auxiliar: Garante que a branch existe, perguntando ao usuário antes de criar
_deploy_ensure_branch_exists() {
    local target_branch="$1"
    local base_branch="$2"

    # Se a branch já existe (local ou remota), apenas faz checkout e usa a existente
    if git show-ref --verify --quiet "refs/remotes/origin/${target_branch}" || \
       git show-ref --verify --quiet "refs/heads/${target_branch}"; then
        echo "🔀 Branch ${target_branch} já existe, utilizando a branch existente..."
        git checkout "$target_branch" 2>/dev/null && git pull origin "$target_branch" 2>/dev/null
        return 0
    fi

    if ! git show-ref --verify --quiet "refs/remotes/origin/${base_branch}" && \
       ! git show-ref --verify --quiet "refs/heads/${base_branch}"; then
        echo "⚠️  Branch ${base_branch} não encontrada, pulando ${target_branch}..."
        return 1
    fi

    local resp
    read "resp?❓ Branch ${target_branch} não existe. Deseja criá-la a partir de ${base_branch}? (s/n): "
    case "$resp" in
        s|S|y|Y|sim)
            echo "✨ Criando branch ${target_branch} a partir de ${base_branch}..."
            git checkout "$base_branch" && \
            git pull origin "$base_branch" 2>/dev/null && \
            git checkout -b "$target_branch" && \
            git push -u origin "$target_branch"
            return $?
            ;;
        *)
            echo "🚫 Criação de ${target_branch} recusada pelo usuário, pulando..."
            return 1
            ;;
    esac
}

# Função auxiliar: Aplica commit em uma branch, pausando em caso de conflito
_deploy_apply_commit_to_branch() {
    local branch_name="$1"
    local commit_hash="$2"

    if ! git rev-parse --verify "$branch_name" >/dev/null 2>&1; then
        return 1
    fi

    git checkout "$branch_name" || return 1

    if ! git cherry-pick "$commit_hash" 2>/dev/null; then
        if git status --porcelain | grep -qE '^(UU|AA|DD)'; then
            echo "⚠️  Conflito de cherry-pick em ${branch_name} para o commit ${commit_hash}"
            echo "   Arquivos em conflito:"
            git diff --name-only --diff-filter=U | sed 's/^/     - /'
            echo ""
            while true; do
                local resp
                read "resp?   Resolva os conflitos, faça 'git add' nos arquivos e digite (continuar/abortar): "
                case "$resp" in
                    continuar|c)
                        if git cherry-pick --continue; then
                            break
                        else
                            echo "❌ Ainda há conflitos pendentes ou erro ao continuar."
                        fi
                        ;;
                    abortar|a)
                        git cherry-pick --abort 2>/dev/null
                        echo "🚫 Cherry-pick abortado para ${branch_name}."
                        return 1
                        ;;
                    *)
                        echo "   Resposta inválida. Digite 'continuar' ou 'abortar'."
                        ;;
                esac
            done
        else
            echo "❌ Erro no cherry-pick para ${branch_name}"
            git cherry-pick --abort 2>/dev/null
            return 1
        fi
    fi

    echo "⬆️  Push para ${branch_name}..."
    git push origin "$branch_name" || return 1

    return 0
}

# Função auxiliar: Busca hashes de commits feature(<id>) ou hotfix(<id>), do mais antigo ao mais novo
_deploy_get_batch_hashes() {
    local feature_id="$1"
    # Parênteses precisam ser escapados: em regex estendida "()" é um grupo, não texto literal
    # "%H %s" traz hash e mensagem juntos para exibição amigável ao usuário
    git log --extended-regexp --reverse \
        --grep="feature\(${feature_id}\)" \
        --grep="hotfix\(${feature_id}\)" \
        --pretty=format:"%H %s"
}

deploy_run() {
    if [ -z "$1" ]; then
        deploy_help
        return 1
    fi

    local feature_id="$1"
    local second_arg="$2"
    local use_hash_mode=false
    local commit_hashes=()

    if [ "$3" = "--hash" ]; then
        use_hash_mode=true
    fi

    local current_branch=$(git branch --show-current)
    local has_errors=false

    echo "🔍 Buscando branches remotas..."
    git fetch origin

    if [ -z "$second_arg" ]; then
        # Modo em lote: busca commits feature(<id>)/hotfix(<id>) no histórico
        echo "🔎 Buscando commits com padrão feature(${feature_id}) ou hotfix(${feature_id})..."
        local hashes_output=$(_deploy_get_batch_hashes "$feature_id")
        if [ -z "$hashes_output" ]; then
            echo "❌ Nenhum commit encontrado com padrão feature(${feature_id}) ou hotfix(${feature_id})"
            return 1
        fi
        local commit_lines=("${(@f)hashes_output}")
        commit_hashes=()
        echo "✅ ${#commit_lines[@]} commit(s) encontrado(s):"
        for line in "${commit_lines[@]}"; do
            commit_hashes+=("${line%% *}")
            echo "   - ${line#* }"
        done
    elif [ "$use_hash_mode" = true ]; then
        if ! git rev-parse --verify "${second_arg}^{commit}" >/dev/null 2>&1; then
            echo "❌ Hash inválido ou não encontrado: ${second_arg}"
            return 1
        fi
        commit_hashes=("$(git rev-parse "$second_arg")")
        echo "🔗 Utilizando commit existente: ${commit_hashes[1]}"
    else
        local commit_msg="$second_arg"

        echo "📝 Adicionando arquivos..."
        git add .

        echo "💾 Fazendo commit: $commit_msg"
        if ! git commit -m "$commit_msg"; then
            echo "❌ Erro ao fazer commit"
            return 1
        fi

        commit_hashes=("$(git rev-parse HEAD)")
        echo "✅ Commit criado: ${commit_hashes[1]}"
    fi

    # Processa branch develop
    local develop_branch="feature/${feature_id}-develop"
    if _deploy_ensure_branch_exists "$develop_branch" "develop"; then
        for commit_hash in "${commit_hashes[@]}"; do
            _deploy_apply_commit_to_branch "$develop_branch" "$commit_hash" || { has_errors=true; break; }
        done
    else
        has_errors=true
    fi

    # Processa branch stage
    local stage_branch="feature/${feature_id}-stage"
    if _deploy_ensure_branch_exists "$stage_branch" "stage"; then
        for commit_hash in "${commit_hashes[@]}"; do
            _deploy_apply_commit_to_branch "$stage_branch" "$commit_hash" || { has_errors=true; break; }
        done
    else
        has_errors=true
    fi

    # Retorna à branch original
    echo "\n↩️  Voltando para branch original: $current_branch"
    git checkout "$current_branch"

    if [ "$has_errors" = true ]; then
        echo "⚠️  Deploy concluído com alguns erros"
    else
        echo "✨ Deploy concluído com sucesso!"
    fi
    echo "   Feature: $feature_id"
    echo "   Commits: ${#commit_hashes[@]}"
}

deploy_help() {
    cat << 'EOF'
  deploy <id-feature>
      Busca commits com padrão feature(<id-feature>) ou hotfix(<id-feature>) no
      histórico e aplica (cherry-pick em lote) nas branches develop e stage
      Exemplo: juniper deploy 4911

  deploy <id-feature> <mensagem>
      Cria commit e aplica automaticamente nas branches develop e stage
      Exemplo: juniper deploy 4911 "Fix: corrige bug no login"

  deploy <id-feature> <hash> --hash
      Aplica (cherry-pick e push) um commit já existente nas branches develop e stage
      Exemplo: juniper deploy 4911 11b81fbe88ed7867d2759037b9406c39f60666f1 --hash

  Observações:
    - Se as branches feature/<id>-develop ou feature/<id>-stage já existirem, elas
      são reutilizadas para os cherry-picks (nenhuma nova branch é criada).
    - Caso a branch não exista, o usuário é consultado antes de criá-la.
    - Em caso de conflito de cherry-pick, a execução é pausada até que o usuário
      resolva os conflitos e confirme como prosseguir (continuar/abortar).
EOF
}
