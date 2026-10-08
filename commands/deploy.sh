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
        _juniper_say "🔀 Branch ${target_branch} já existe, utilizando a branch existente..."
        git checkout "$target_branch" 2>/dev/null && git pull origin "$target_branch" 2>/dev/null
        return 0
    fi

    if ! git show-ref --verify --quiet "refs/remotes/origin/${base_branch}" && \
       ! git show-ref --verify --quiet "refs/heads/${base_branch}"; then
        _juniper_say "⚠️  Branch ${base_branch} não encontrada, pulando ${target_branch}..."
        return 1
    fi

    local resp
    read "resp?🌿 JUNIPER: ❓ Branch ${target_branch} não existe. Deseja criá-la a partir de ${base_branch}? (s/n): "
    case "$resp" in
        s|S|y|Y|sim)
            _juniper_say "✨ Criando branch ${target_branch} a partir de ${base_branch}..."
            git checkout "$base_branch" && \
            git pull origin "$base_branch" 2>/dev/null && \
            git checkout -b "$target_branch" && \
            git push -u origin "$target_branch"
            return $?
            ;;
        *)
            _juniper_say "🚫 Criação de ${target_branch} recusada pelo usuário, pulando..."
            return 1
            ;;
    esac
}

# Função auxiliar: Verifica se um commit (ou o seu equivalente cherry-picked) já está na branch.
#
# O cherry-pick gera um NOVO hash na branch de destino, então comparar hashes
# diretamente não funciona. Por isso são usadas 3 verificações, da mais barata
# para a mais custosa:
#   1) O hash original é ancestral da branch (ex.: já entrou via merge)
#   2) Existe um commit na branch com o trailer "(cherry picked from commit <hash>)"
#      (gerado pelo cherry-pick -x; é o que sobrevive a conflitos resolvidos à mão)
#   3) Existe um commit com o mesmo patch-id (mesmo diff), via `git cherry`
#      (cobre cherry-picks antigos feitos sem -x)
#
# Retorna 0 se o commit já está presente, 1 se está ausente.
_deploy_commit_in_branch() {
    local branch_name="$1"
    local commit_hash="$2"

    # 1) Ancestral direto
    if git merge-base --is-ancestor "$commit_hash" "$branch_name" 2>/dev/null; then
        return 0
    fi

    # 2) Trailer do cherry-pick -x
    if [ -n "$(git log "$branch_name" --fixed-strings \
            --grep="(cherry picked from commit ${commit_hash})" \
            -n 1 --pretty=format:%H 2>/dev/null)" ]; then
        return 0
    fi

    # 3) Mesmo patch-id: `git cherry <branch> <commit> <commit>^` imprime
    #    "- <hash>" se já existe equivalente na branch, "+ <hash>" se não existe
    local cherry_out
    cherry_out=$(git cherry "$branch_name" "$commit_hash" "${commit_hash}^" 2>/dev/null)
    if [[ "$cherry_out" == -* ]]; then
        return 0
    fi

    return 1
}

# Função auxiliar: Aplica em uma branch apenas os commits que ainda NÃO estão nela
# Uso: _deploy_apply_missing_commits <branch> <hash1> <hash2> ...
# (a ordem dos hashes recebidos, do mais antigo ao mais novo, é preservada)
_deploy_apply_missing_commits() {
    local branch_name="$1"
    shift
    local all_hashes=("$@")
    local pending_hashes=()
    local skipped=0
    local h

    for h in "${all_hashes[@]}"; do
        if _deploy_commit_in_branch "$branch_name" "$h"; then
            skipped=$((skipped + 1))
            _juniper_say "⏭️  Já presente em ${branch_name}: $(git log -1 --format='%h %s' "$h")"
        else
            pending_hashes+=("$h")
        fi
    done

    if [ ${#pending_hashes[@]} -eq 0 ]; then
        _juniper_say "✅ ${branch_name} já está atualizada (${skipped} commit(s) já presentes)"
        return 0
    fi

    _juniper_say "📦 ${branch_name}: ${#pending_hashes[@]} commit(s) pendente(s), ${skipped} já presente(s)"

    for h in "${pending_hashes[@]}"; do
        _deploy_apply_commit_to_branch "$branch_name" "$h" || return 1
    done

    return 0
}

# Função auxiliar: Aplica commit em uma branch, pausando em caso de conflito
_deploy_apply_commit_to_branch() {
    local branch_name="$1"
    local commit_hash="$2"

    if ! git rev-parse --verify "$branch_name" >/dev/null 2>&1; then
        return 1
    fi

    git checkout "$branch_name" || return 1

    # -x grava "(cherry picked from commit <hash>)" na mensagem, o que permite
    # reconhecer este commit em execuções futuras (mesmo após resolver conflitos)
    if ! git cherry-pick -x "$commit_hash" 2>/dev/null; then
        # FIX: Expandida a regex para incluir UD e DU (modify/delete conflicts)
        if git status --porcelain | grep -qE '^(UU|AA|DD|UD|DU)'; then
            _juniper_say "⚠️  Conflito de cherry-pick em ${branch_name} para o commit ${commit_hash}"
            _juniper_say "Arquivos em conflito:"
            git diff --name-only --diff-filter=U | sed 's/^/     - /'
            echo ""
            while true; do
                local resp
                read "resp?🌿 JUNIPER: Resolva os conflitos, faça 'git add' nos arquivos e digite (continuar/abortar): "
                case "$resp" in
                    continuar|c)
                        if git cherry-pick --continue; then
                            break
                        else
                            _juniper_say "❌ Ainda há conflitos pendentes ou erro ao continuar."
                        fi
                        ;;
                    abortar|a)
                        git cherry-pick --abort 2>/dev/null
                        _juniper_say "🚫 Cherry-pick abortado para ${branch_name}."
                        return 1
                        ;;
                    *)
                        _juniper_say "Resposta inválida. Digite 'continuar' ou 'abortar'."
                        ;;
                esac
            done
        else
            _juniper_say "❌ Erro no cherry-pick para ${branch_name}"
            git cherry-pick --abort 2>/dev/null
            return 1
        fi
    fi

    _juniper_say "⬆️  Push para ${branch_name}..."
    git push origin "$branch_name" || return 1

    return 0
}

# Função auxiliar: Busca hashes de commits feature(<id>) ou hotfix(<id>) de uma ou
# mais tasks, do mais antigo ao mais novo (sem duplicatas)
# Uso: _deploy_get_batch_hashes <id1> [id2 ...]
_deploy_get_batch_hashes() {
    local grep_args=()
    local id
    for id in "$@"; do
        grep_args+=(--grep="feature\(${id}\)" --grep="hotfix\(${id}\)")
    done
    git log --extended-regexp --reverse "${grep_args[@]}" \
        --date=format:'%d/%m/%Y %H:%M' \
        --pretty=format:"%H|%ad|%s"
}

deploy_run() {
    # Separa flags dos argumentos posicionais
    local positional=()
    local extra_tasks=()
    local use_hash_mode=false
    local want_develop=false
    local want_stage=false
    while [ $# -gt 0 ]; do
        case "$1" in
            --hash)    use_hash_mode=true ;;
            --develop) want_develop=true ;;
            --stage)   want_stage=true ;;
            --tasks)
                shift
                while [ $# -gt 0 ] && [[ "$1" != -* ]]; do
                    extra_tasks+=("$1")
                    shift
                done
                continue
                ;;
            *) positional+=("$1") ;;
        esac
        shift
    done

    if [ ${#positional[@]} -eq 0 ]; then
        deploy_help
        return 1
    fi

    local feature_id="${positional[1]}"
    local second_arg="${positional[2]}"
    local commit_hashes=()   # lista de hashes da task (salva para comparar com cada branch)

    # Sem flag de destino, envia para ambos
    if [ "$want_develop" = false ] && [ "$want_stage" = false ]; then
        want_develop=true
        want_stage=true
    fi

    # Modo multi-task: commits da task pai + tasks extras, todos para as branches da pai
    local task_ids=("$feature_id")
    if [ ${#extra_tasks[@]} -gt 0 ]; then
        if [ -n "$second_arg" ]; then
            _juniper_say "❌ --tasks só pode ser usado no modo em lote (sem <mensagem> ou --hash)"
            return 1
        fi
        local t
        for t in "${extra_tasks[@]}"; do
            if [[ ! "$t" =~ ^[0-9]+$ ]]; then
                _juniper_say "❌ ID de task inválido em --tasks: ${t}"
                return 1
            fi
            # evita duplicar a task pai ou ids repetidos
            (( ${task_ids[(Ie)$t]} )) || task_ids+=("$t")
        done
    fi

    local user_name=$(_juniper_get_user_name)
    local current_branch=$(git branch --show-current)
    local has_errors=false

    _juniper_say "🔍 Buscando branches remotas..."
    git fetch origin

    if [ -z "$second_arg" ]; then
        _juniper_say "🔎 Buscando commits com padrão feature(<id>) ou hotfix(<id>) para: ${task_ids[*]}..."
        local hashes_output=$(_deploy_get_batch_hashes "${task_ids[@]}")
        if [ -z "$hashes_output" ]; then
            _juniper_say "❌ Nenhum commit encontrado para as tasks: ${task_ids[*]}"
            return 1
        fi
        local commit_lines=("${(@f)hashes_output}")
        commit_hashes=()
        _juniper_say "${#commit_lines[@]} commit(s) encontrado(s):"
        for line in "${commit_lines[@]}"; do
            local commit_date="${${line#*|}%%|*}"
            local commit_msg="${line#*|*|}"
            commit_hashes+=("${line%%|*}")
            echo "   - ($commit_date) $commit_msg"
        done
    elif [ "$use_hash_mode" = true ]; then
        if ! git rev-parse --verify "${second_arg}^{commit}" >/dev/null 2>&1; then
            _juniper_say "❌ Hash inválido ou não encontrado: ${second_arg}"
            return 1
        fi
        commit_hashes=("$(git rev-parse "$second_arg")")
        _juniper_say "🔗 Utilizando commit existente: ${commit_hashes[1]}"
    else
        local commit_msg="$second_arg"

        _juniper_say "📝 Adicionando arquivos..."
        git add .

        _juniper_say "💾 Fazendo commit: $commit_msg"
        if ! git commit -m "$commit_msg"; then
            _juniper_say "❌ Erro ao fazer commit"
            return 1
        fi

        commit_hashes=("$(git rev-parse HEAD)")
        _juniper_say "Commit criado: ${commit_hashes[1]}"
    fi

    # Para cada branch de destino, compara a lista de hashes da task com o que já
    # existe na branch e aplica (cherry-pick) somente a diferença.
    if [ "$want_develop" = true ]; then
        local develop_branch="feature/${feature_id}-develop"
        if _deploy_ensure_branch_exists "$develop_branch" "develop"; then
            _deploy_apply_missing_commits "$develop_branch" "${commit_hashes[@]}" || has_errors=true
        else
            has_errors=true
        fi
    fi

    if [ "$want_stage" = true ]; then
        local stage_branch="feature/${feature_id}-stage"
        if _deploy_ensure_branch_exists "$stage_branch" "stage"; then
            _deploy_apply_missing_commits "$stage_branch" "${commit_hashes[@]}" || has_errors=true
        else
            has_errors=true
        fi
    fi

    echo ""
    _juniper_say "↩️  Voltando para branch original: $current_branch"
    git checkout "$current_branch"

    if [ "$has_errors" = true ]; then
        _juniper_say "⚠️  Deploy concluído com alguns erros, $user_name"
    else
        _juniper_say "✨ Deploy concluído com sucesso, $user_name!"
    fi
    echo "   Feature: $feature_id"
    [ ${#task_ids[@]} -gt 1 ] && echo "   Tasks: ${task_ids[*]}"
    echo "   Commits: ${#commit_hashes[@]}"
}

deploy_help() {
    cat << 'EOF'
  deploy <id-feature>
      Busca commits com padrão feature(<id-feature>) ou hotfix(<id-feature>) no
      histórico e aplica (cherry-pick em lote) nas branches develop e stage.
      Commits que já estiverem presentes nas branches (de execuções anteriores)
      são detectados e ignorados: apenas a diferença é aplicada.
      Exemplo: juniper deploy 4911

  deploy <id-feature> <mensagem>
      Cria commit e aplica automaticamente nas branches develop e stage
      Exemplo: juniper deploy 4911 "Fix: corrige bug no login"

  deploy <id-feature> <hash> --hash
      Aplica (cherry-pick e push) um commit já existente nas branches develop e stage
      Exemplo: juniper deploy 4911 11b81fbe88ed7867d2759037b9406c39f60666f1 --hash

  deploy <id-pai> --tasks <id2> [id3 ...]
      Modo multi-task: reúne os commits da task pai e das tasks informadas e os
      aplica nas branches da task pai (feature/<id-pai>-develop e -stage).
      Exemplo: juniper deploy 6531 --tasks 7432

  Flags de destino (valem para todos os modos):
    --develop   aplica somente em feature/<id>-develop
    --stage     aplica somente em feature/<id>-stage
    (sem flag, ou com ambas, aplica em develop e stage)
      Exemplo: juniper deploy 6531 --tasks 7432 --stage

  Observações:
    - Se as branches feature/<id>-develop ou feature/<id>-stage já existirem, elas
      são reutilizadas para os cherry-picks (nenhuma nova branch é criada).
    - Caso a branch não exista, o usuário é consultado antes de criá-la.
    - Um commit é considerado "já presente" se: (1) o hash é ancestral da branch,
      (2) há um commit com o trailer "(cherry picked from commit <hash>)", ou
      (3) há um commit com o mesmo patch-id (mesmo diff).
    - Em caso de conflito de cherry-pick, a execução é pausada até que o usuário
      resolva os conflitos e confirme como prosseguir (continuar/abortar).
EOF
}