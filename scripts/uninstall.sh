#!/usr/bin/env bash
#
# Juniper v4 - Desinstalação completa
#
# Uso:
#   ./scripts/uninstall.sh              # remove tudo, com confirmação
#   ./scripts/uninstall.sh --yes        # sem confirmação (uso em CI/automação)
#   ./scripts/uninstall.sh --keep-repo  # remove só artefatos fora do repo (default)
#   ./scripts/uninstall.sh --purge-repo # remove TAMBÉM o diretório do repo
#   ./scripts/uninstall.sh --dry-run    # mostra o que faria, sem fazer
#
# O que este script remove:
#   - .venv/ na raiz do repo (ou venv/ legado)
#   - Symlink ~/.local/bin/juniper
#   - Linhas injetadas no ~/.zshrc e ~/.bashrc (marcadas com "# Juniper - PATH")
#   - ~/.juniper/logs/ (diretório de runtime)
#   - config.yaml e .env gerados localmente (mantém os .example)
#   - Opcionalmente: o diretório inteiro do repo (--purge-repo)
#
# O que NÃO remove:
#   - Python, pip, jq, Ollama, Groq API key (responsabilidade do usuário)
#   - Dependências globais instaladas pelo sistema
#   - Outros projetos que compartilham ~/.local/bin

set -euo pipefail

# --- Configuração -----------------------------------------------------

JUNIPER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$JUNIPER_ROOT/.venv"
LEGACY_VENV_DIR="$JUNIPER_ROOT/venv"
LOCAL_BIN="$HOME/.local/bin"
LOCAL_BIN_SYMLINK="$LOCAL_BIN/juniper"
LOG_DIR="$HOME/.juniper/logs"
JUNIPER_HOME="$HOME/.juniper"
PATH_MARKER='# Juniper - PATH'
CONFIG_FILE="$JUNIPER_ROOT/config.yaml"
ENV_FILE="$JUNIPER_ROOT/.env"

# --- Flags ------------------------------------------------------------

SKIP_CONFIRM=false
PURGE_REPO=false
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --yes|-y)      SKIP_CONFIRM=true ;;
        --keep-repo)   PURGE_REPO=false ;;
        --purge-repo)  PURGE_REPO=true ;;
        --dry-run)     DRY_RUN=true ;;
        -h|--help)
            sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf '🌿 JUNIPER: Opção desconhecida: %s\n' "$arg" >&2
            exit 1
            ;;
    esac
done

# --- Cores ------------------------------------------------------------

if [ ! -t 1 ]; then
    C_RESET=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_DIM=""
else
    C_RESET="\033[0m"
    C_GREEN="\033[32m"
    C_YELLOW="\033[33m"
    C_RED="\033[31m"
    C_BOLD="\033[1m"
    C_DIM="\033[2m"
fi

# Mesmo prefixo de fala de shell/core/voice.sh e brain/clients/voice.py.
JUNIPER_TAG="🌿 JUNIPER:"
USER_TAG="👤 VOCÊ:"

say()   { printf "%s %s\n" "$JUNIPER_TAG" "$1"; }
ok()    { printf "%s ${C_GREEN}✔${C_RESET} %s\n" "$JUNIPER_TAG" "$1"; }
warn()  { printf "%s ${C_YELLOW}⚠${C_RESET} %s\n" "$JUNIPER_TAG" "$1"; }
info()  { printf "%s ${C_DIM}…${C_RESET} %s\n" "$JUNIPER_TAG" "$1"; }
skip()  { printf "%s ${C_DIM}–${C_RESET} %s\n" "$JUNIPER_TAG" "$1"; }
bad()   { printf "%s ${C_RED}✖${C_RESET} %s\n" "$JUNIPER_TAG" "$1" >&2; }
title() { printf "\n%s ${C_BOLD}▸ %s${C_RESET}\n" "$JUNIPER_TAG" "$1"; }
run()   {
    if [ "$DRY_RUN" = true ]; then
        printf "%s ${C_DIM}(simulação)${C_RESET} %s\n" "$JUNIPER_TAG" "$*"
    else
        "$@"
    fi
}

# --- Banner -----------------------------------------------------------

say "Entendi, você quer me desinstalar. Vou limpar com cuidado."

if [ "$DRY_RUN" = true ]; then
    warn "Modo DRY-RUN — nada será removido de verdade"
fi

printf "  Raiz do repo:       %s\n" "$JUNIPER_ROOT"
printf "  Venv:               %s\n" "$VENV_DIR"
printf "  Symlink global:     %s\n" "$LOCAL_BIN_SYMLINK"
printf "  Logs de runtime:    %s\n" "$LOG_DIR"
printf "  RC files:           %s\n" "$HOME/.zshrc, $HOME/.bashrc"
if [ "$PURGE_REPO" = true ]; then
    printf "  ${C_RED}Repo inteiro:       %s${C_RESET}\n" "$JUNIPER_ROOT"
fi

# --- Confirmação ------------------------------------------------------

if [ "$SKIP_CONFIRM" = false ] && [ "$DRY_RUN" = false ]; then
    printf "\n${C_YELLOW}Esta ação vai remover:${C_RESET}\n"
    printf "  - .venv/ (ambiente virtual completo)\n"
    printf "  - symlink em ~/.local/bin/juniper\n"
    printf "  - linhas de PATH injetadas em ~/.zshrc e ~/.bashrc\n"
    printf "  - ~/.juniper/logs/\n"
    printf "  - config.yaml e .env gerados localmente\n"
    if [ "$PURGE_REPO" = true ]; then
        printf "  ${C_RED}- o diretório INTEIRO do repo ($JUNIPER_ROOT)${C_RESET}\n"
    fi
    printf "\n"
    say "Tem certeza? Vou embora do seu sistema. [y/N]"
    read -r -p "$USER_TAG " response
    case "$response" in
        [yY]|[yY][eE][sS]) ;;
        *) say "Combinado, não removi nada. Fico por aqui."; exit 0 ;;
    esac
fi

# --- 1. Remover venv --------------------------------------------------

title "1/6 Removendo ambiente virtual"

if [ -d "$VENV_DIR" ]; then
    info "Removendo $VENV_DIR"
    run rm -rf "$VENV_DIR"
    ok ".venv/ removido"
else
    skip ".venv/ não existe"
fi

if [ -d "$JUNIPER_ROOT/.tools" ]; then
    info "Removendo Python/uv isolados ($JUNIPER_ROOT/.tools)"
    run rm -rf "$JUNIPER_ROOT/.tools"
    ok ".tools/ removido"
fi

if [ -d "$LEGACY_VENV_DIR" ]; then
    info "Removendo venv legado $LEGACY_VENV_DIR"
    run rm -rf "$LEGACY_VENV_DIR"
    ok "venv/ legado removido"
else
    skip "venv/ legado não existe"
fi

# --- 2. Remover symlink global ----------------------------------------

title "2/6 Removendo symlink global"

if [ -L "$LOCAL_BIN_SYMLINK" ]; then
    info "Removendo symlink $LOCAL_BIN_SYMLINK"
    run rm -f "$LOCAL_BIN_SYMLINK"
    ok "Symlink removido"
elif [ -e "$LOCAL_BIN_SYMLINK" ]; then
    warn "$LOCAL_BIN_SYMLINK existe mas NÃO é symlink — não será removido"
    warn "Remova manualmente se for um arquivo da Juniper"
else
    skip "Symlink não existe"
fi

# Limpa ~/.local/bin se ficou vazio E é um diretório nosso (não do sistema)
if [ -d "$LOCAL_BIN" ] && [ "$DRY_RUN" = false ]; then
    if [ -z "$(ls -A "$LOCAL_BIN" 2>/dev/null)" ]; then
        info "~/.local/bin está vazio — removendo"
        rmdir "$LOCAL_BIN" 2>/dev/null && ok "~/.local/bin removido" || true
    fi
fi

# --- 3. Limpar PATH dos RC files --------------------------------------

title "3/6 Limpando PATH de .zshrc e .bashrc"

clean_rc_file() {
    local rc_file="$1"

    if [ ! -f "$rc_file" ]; then
        skip "$rc_file não existe"
        return 0
    fi

    if ! grep -qE "# Juniper - (PATH|shell)" "$rc_file"; then
        skip "Nenhum marker da Juniper em $rc_file"
        return 0
    fi

    info "Removendo linhas da Juniper de $rc_file"

    # Remove:
    #   - a linha do marker
    #   - a linha do export PATH (imediatamente após o marker)
    #   - uma linha vazia antes do marker (se houver, para não deixar buracos)
    #
    # Usamos awk para ter controle preciso do que remover.
    local tmp
    tmp="$(mktemp)"

    awk -v marker="$PATH_MARKER" '
        BEGIN { buf = "" }
        {
            # Se a linha contém o marker, pulamos ela e a próxima
            if ($0 ~ /# Juniper - (PATH|shell)/) {
                skip_next = 1
                next
            }
            # Se a próxima linha era para ser pulada (o export logo após o marker)
            if (skip_next) {
                skip_next = 0
                # Pulamos a linha do export
                next
            }
            print
        }
    ' "$rc_file" > "$tmp"

    # Remove linhas em branco duplicadas no final (efeito colateral da remoção)
    # e garante que o arquivo termina com uma única newline
    awk 'BEGIN { blank = 0 }
         /^[[:space:]]*$/ { blank++; if (blank > 1) next }
         { blank = 0; print }' "$tmp" > "$tmp.clean"

    run mv "$tmp.clean" "$rc_file"
    run rm -f "$tmp"

    ok "$rc_file limpo"
}

clean_rc_file "$HOME/.zshrc"
clean_rc_file "$HOME/.bashrc"

# --- 4. Remover logs e runtime ----------------------------------------

title "4/6 Removendo diretórios de runtime"

if [ -d "$LOG_DIR" ]; then
    info "Removendo $LOG_DIR"
    run rm -rf "$LOG_DIR"
    ok "Logs removidos"
else
    skip "$LOG_DIR não existe"
fi

# Se ~/.juniper/ ficou vazio (apenas logs existiam lá), remove
if [ -d "$JUNIPER_HOME" ] && [ "$DRY_RUN" = false ]; then
    if [ -z "$(ls -A "$JUNIPER_HOME" 2>/dev/null)" ]; then
        info "$JUNIPER_HOME está vazio — removendo"
        rmdir "$JUNIPER_HOME" 2>/dev/null && ok "$JUNIPER_HOME removido" || true
    fi
fi

# --- 5. Remover configs geradas ---------------------------------------

title "5/6 Removendo configs geradas localmente"

if [ -f "$CONFIG_FILE" ]; then
    info "Removendo $CONFIG_FILE"
    run rm -f "$CONFIG_FILE"
    ok "config.yaml removido"
else
    skip "config.yaml não existe"
fi

if [ -f "$ENV_FILE" ]; then
    info "Removendo $ENV_FILE"
    run rm -f "$ENV_FILE"
    ok ".env removido"
else
    skip ".env não existe"
fi

# --- 6. Purgar repo (opcional) ----------------------------------------

if [ "$PURGE_REPO" = true ]; then
    title "6/6 Removendo repositório inteiro"

    # Guard clause: nunca remover / ou $HOME por acidente
    if [ "$JUNIPER_ROOT" = "/" ] || [ "$JUNIPER_ROOT" = "$HOME" ]; then
        bad "Caminho perigoso detectado: $JUNIPER_ROOT"
        bad "Abortando para evitar dano."
        exit 1
    fi

    # Guard clause: confirmar que parece ser um repo Juniper
    if [ ! -f "$JUNIPER_ROOT/pyproject.toml" ]; then
        bad "$JUNIPER_ROOT não parece ser um repo Juniper"
        bad "Abortando por segurança."
        exit 1
    fi

    if [ "$DRY_RUN" = true ]; then
        printf "${C_DIM}[DRY ]${C_RESET} rm -rf %s\n" "$JUNIPER_ROOT"
    else
        warn "Removendo $JUNIPER_ROOT"
        # Mudamos para um diretório pai antes de remover o diretório atual
        cd "$HOME"
        rm -rf "$JUNIPER_ROOT"
        ok "Repo removido"
    fi
else
    title "6/6 Repositório preservado"
    info "O repo em $JUNIPER_ROOT foi mantido (use --purge-repo para remover)"
fi

# --- Verificação final ------------------------------------------------

if [ "$DRY_RUN" = false ] && [ "$PURGE_REPO" = false ]; then
    title "Verificação"

    local_failed=false

    [ -d "$VENV_DIR" ] && { warn "Ainda existe: $VENV_DIR"; local_failed=true; }
    [ -L "$LOCAL_BIN_SYMLINK" ] && { warn "Ainda existe: $LOCAL_BIN_SYMLINK"; local_failed=true; }
    [ -d "$LOG_DIR" ] && { warn "Ainda existe: $LOG_DIR"; local_failed=true; }

    if [ "$local_failed" = false ]; then
        ok "Nenhum artefato residual detectado"
    fi
fi

# --- Resumo -----------------------------------------------------------

if [ "$DRY_RUN" = false ]; then
    printf "\n"
    say "Pronto, desinstalação concluída."
    if [ "$PURGE_REPO" = false ]; then
        say "Preservei a pasta em $JUNIPER_ROOT. Para me chamar de volta:"
        printf "       cd %s && bash scripts/install.sh\n" "$JUNIPER_ROOT"
    else
        say "Removi tudo. Nada de mim permanece no sistema."
    fi
    say "Abra um terminal novo para o PATH antigo sair da sessão."
    printf "\n"
fi

exit 0