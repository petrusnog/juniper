#!/usr/bin/env bash
#
# Juniper v4 - Instalador do cérebro (idempotente, sem configuração manual)
#
# Uso:
#   juniper install                    # (shell) ou ./scripts/install.sh
#   ./scripts/install.sh --dev         # inclui ferramentas de desenvolvimento (pytest, ruff, mypy)
#   ./scripts/install.sh --clean       # apaga o ambiente e instala do zero
#   ./scripts/install.sh --check       # só verifica; não altera nada
#   ./scripts/install.sh --yes         # não faz perguntas (CI, scripts)
#   ./scripts/install.sh --no-rc       # não mexe em ~/.zshrc / ~/.bashrc
#
# Funciona em máquina SEM Python. Tudo vive dentro de ~/.juniper (ambiente virtual .venv/):
#   1. Python >= 3.11 do sistema (se existir e funcionar) ou um Python isolado baixado
#      pelo uv (sem root, sem tocar no Python da máquina).
#   2. Reaproveita o que já existe e está saudável; apaga e reinstala só o que está com defeito.
#   3. Cria config.yaml e .env, o atalho ~/.local/bin/juniper e o gancho do shell.
#   4. Termina com um teste real. Se terminar com sucesso, a Juniper está pronta.

set -euo pipefail

# --- Caminhos -----------------------------------------------------------

JUNIPER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$JUNIPER_ROOT/.venv"
LEGACY_VENV_DIR="$JUNIPER_ROOT/venv"
TOOLS_DIR="$JUNIPER_ROOT/.tools"
LOCAL_BIN="$HOME/.local/bin"
LOG_DIR="$HOME/.juniper/logs"
LOG_FILE="$LOG_DIR/install.log"
PATH_MARKER='# Juniper - PATH'
HOOK_MARKER='# Juniper - shell'
PYTHON_VERSION_WANTED="3.12"
UV_BASE_URL="https://github.com/astral-sh/uv/releases/latest/download"

# --- Flags ----------------------------------------------------------------

WITH_DEV=false
CLEAN=false
CHECK_ONLY=false
ASSUME_YES=false
TOUCH_RC=true

parse_flags() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            --dev)       WITH_DEV=true ;;
            --clean)     CLEAN=true ;;
            --check)     CHECK_ONLY=true ;;
            --yes|--ci)  ASSUME_YES=true ;;
            --no-rc)     TOUCH_RC=false ;;
            --no-ollama) ;; # aceito por compatibilidade; o Ollama é opcional
            -h|--help)
                sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                exit 0
                ;;
            *)
                printf '🌿 JUNIPER: Opção desconhecida: %s (use --help)\n' "$arg" >&2
                exit 2
                ;;
        esac
    done
}

# --- Saída --------------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET=$'\033[0m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
    C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
else
    C_RESET=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_DIM=""
fi

# A Juniper fala sempre com o mesmo prefixo (igual a shell/core/voice.sh e brain/clients/voice.py);
# o usuário responde com o dele.
JUNIPER_TAG="🌿 JUNIPER:"
USER_TAG="👤 VOCÊ:"

say()   { printf '%s %s\n' "$JUNIPER_TAG" "$1"; }
ok()    { printf '%s %s✔%s %s\n' "$JUNIPER_TAG" "$C_GREEN" "$C_RESET" "$1"; }
warn()  { printf '%s %s⚠%s %s\n' "$JUNIPER_TAG" "$C_YELLOW" "$C_RESET" "$1"; }
info()  { printf '%s %s…%s %s\n' "$JUNIPER_TAG" "$C_DIM" "$C_RESET" "$1"; }
fail()  { printf '%s %s✖%s %s\n' "$JUNIPER_TAG" "$C_RED" "$C_RESET" "$1" >&2; }
title() { printf '\n%s %s▸ %s%s\n' "$JUNIPER_TAG" "$C_BOLD" "$1" "$C_RESET"; }

die() {
    fail "$1"
    [ -n "${2:-}" ] && printf '%s %s\n' "$JUNIPER_TAG" "$2" >&2
    printf '%s Detalhes técnicos: %s\n' "$JUNIPER_TAG" "$LOG_FILE" >&2
    exit 1
}

# --- Execução com log e retentativas -------------------------------------------

# Roda o comando com a saída indo para o log (o usuário só vê o resumo).
run_logged() {
    mkdir -p "$LOG_DIR"
    printf '\n$ %s\n' "$*" >> "$LOG_FILE"
    "$@" >> "$LOG_FILE" 2>&1
}

# Redes falham à toa: tenta até 3 vezes, esperando um pouco entre elas.
retry() {
    local attempt=1
    while true; do
        if run_logged "$@"; then
            return 0
        fi
        if [ "$attempt" -ge 3 ]; then
            return 1
        fi
        info "Falhou, tentando de novo ($((attempt + 1))/3)..."
        sleep $((attempt * 2))
        attempt=$((attempt + 1))
    done
}

online() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSI --max-time 8 https://pypi.org >/dev/null 2>&1
    elif command -v wget >/dev/null 2>&1; then
        wget -q --spider --timeout=8 https://pypi.org 2>/dev/null
    else
        return 1
    fi
}

network_hint() {
    if online; then
        echo "A internet está acessível, mas a instalação falhou. Rode de novo; se persistir, envie o log."
    else
        echo "Sem conexão com a internet (pypi.org inacessível). Conecte-se e rode 'juniper install' de novo."
    fi
}

# --- Download e uv ----------------------------------------------------------------

download() { # download <url> <destino>
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 3 --connect-timeout 15 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --tries=3 --timeout=30 -O "$2" "$1"
    else
        return 127
    fi
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

# Triplo de plataforma do uv: uv_triple <uname -s> <uname -m> <musl|gnu>
uv_triple() {
    local os="$1" arch="$2" libc="${3:-gnu}"
    case "$arch" in
        arm64) arch="aarch64" ;;
        amd64) arch="x86_64" ;;
    esac
    case "$os-$arch" in
        Linux-x86_64|Linux-aarch64) echo "$arch-unknown-linux-$libc" ;;
        Darwin-x86_64|Darwin-aarch64) echo "$arch-apple-darwin" ;;
        *) return 1 ;;
    esac
}

detect_libc() {
    if ls /lib/ld-musl-* >/dev/null 2>&1 || (ldd --version 2>&1 | grep -qi musl); then
        echo musl
    else
        echo gnu
    fi
}

UV_BIN=""
VENV_FRESH=false

# Define UV_BIN: o uv do sistema, o já baixado em .tools/, ou baixa (com checksum) para .tools/.
ensure_uv() {
    [ -n "$UV_BIN" ] && return 0
    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
        return 0
    fi
    export UV_PYTHON_INSTALL_DIR="$TOOLS_DIR/python"
    export UV_CACHE_DIR="$TOOLS_DIR/cache"
    if [ -x "$TOOLS_DIR/bin/uv" ] && "$TOOLS_DIR/bin/uv" --version >/dev/null 2>&1; then
        UV_BIN="$TOOLS_DIR/bin/uv"
        return 0
    fi

    info "Baixando o uv (gerenciador de Python isolado, ~20 MB)..."
    local triple tmp archive expected actual
    triple="$(uv_triple "$(uname -s)" "$(uname -m)" "$(detect_libc)")" ||
        die "Sistema não suportado ($(uname -s) $(uname -m))." \
            "Instale Python 3.11 ou mais novo e rode de novo."
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "Faltam curl ou wget para baixar o Python." \
            "Instale um deles (ex.: sudo apt install curl) e rode 'juniper install' de novo."
    tmp="$(mktemp -d)"
    archive="$tmp/uv.tar.gz"
    retry download "$UV_BASE_URL/uv-$triple.tar.gz" "$archive" ||
        die "Não consegui baixar o uv." "$(network_hint)"
    retry download "$UV_BASE_URL/uv-$triple.tar.gz.sha256" "$archive.sha256" ||
        die "Não consegui baixar o checksum do uv." "$(network_hint)"
    expected="$(awk '{print $1}' "$archive.sha256")"
    actual="$(sha256_of "$archive")"
    if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
        rm -rf "$tmp"
        die "O arquivo baixado do uv não confere com o checksum (download corrompido)." \
            "Tente de novo; se repetir, a rede pode estar alterando o arquivo."
    fi
    run_logged tar -xzf "$archive" -C "$tmp" || die "Não consegui extrair o uv."
    mkdir -p "$TOOLS_DIR/bin"
    cp "$tmp/uv-$triple/uv" "$TOOLS_DIR/bin/uv"
    chmod +x "$TOOLS_DIR/bin/uv"
    rm -rf "$tmp"
    "$TOOLS_DIR/bin/uv" --version >/dev/null 2>&1 || die "O uv baixado não executa nesta máquina."
    UV_BIN="$TOOLS_DIR/bin/uv"
    ok "uv pronto (dentro de ~/.juniper/.tools, nada instalado no sistema)"
}

# --- Python ----------------------------------------------------------------------

# Python >= 3.11 do sistema que sabe criar venv de verdade (no Debian/Ubuntu costuma faltar
# o pacote python3-venv, e aí ele NÃO serve). Imprime o caminho ou nada.
find_system_python() {
    local candidate tmp
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
            >/dev/null 2>&1 || continue
        tmp="$(mktemp -d)"
        if "$candidate" -m venv "$tmp/probe" >/dev/null 2>&1 &&
            "$tmp/probe/bin/python" -m pip --version >/dev/null 2>&1; then
            rm -rf "$tmp"
            command -v "$candidate"
            return 0
        fi
        rm -rf "$tmp"
    done
    return 0
}

venv_python_ok() {
    [ -x "$VENV_DIR/bin/python" ] &&
        "$VENV_DIR/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
            >/dev/null 2>&1
}

venv_has_pip() {
    "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1
}

create_venv() {
    local system_python
    system_python="$(find_system_python)"
    rm -rf "$VENV_DIR"
    VENV_FRESH=true
    if [ -n "$system_python" ]; then
        info "Criando o ambiente virtual com $("$system_python" -V 2>&1)..."
        run_logged "$system_python" -m venv "$VENV_DIR" || die "Não consegui criar o ambiente virtual."
        ok "Ambiente virtual criado (Python do sistema)"
    else
        info "Python 3.11+ não encontrado na máquina: usando um Python isolado."
        ensure_uv
        retry "$UV_BIN" venv --python "$PYTHON_VERSION_WANTED" "$VENV_DIR" ||
            die "Não consegui preparar o Python." "$(network_hint)"
        ok "Ambiente virtual criado (Python $PYTHON_VERSION_WANTED isolado)"
    fi
}

# --- Pacotes ----------------------------------------------------------------------

PKG_TARGET="."

pkg_install() { # pkg_install <args do pip install>
    if venv_has_pip; then
        retry "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check --quiet \
            --retries 5 --timeout 30 "$@"
    else
        ensure_uv
        retry "$UV_BIN" pip install --python "$VENV_DIR/bin/python" --quiet "$@"
    fi
}

pkg_uninstall() { # pkg_uninstall <nomes...>; falhar aqui não é grave
    [ "$#" -gt 0 ] || return 0
    if venv_has_pip; then
        run_logged "$VENV_DIR/bin/python" -m pip uninstall -y --disable-pip-version-check "$@" || true
    else
        ensure_uv
        run_logged "$UV_BIN" pip uninstall --python "$VENV_DIR/bin/python" "$@" || true
    fi
}

# Dependências ausentes/quebradas, uma por linha (vazio = tudo certo).
broken_packages() {
    local flag=""
    [ "$WITH_DEV" = true ] && flag="--dev"
    "$VENV_DIR/bin/python" "$JUNIPER_ROOT/scripts/check_env.py" $flag 2>>"$LOG_FILE" |
        sed '/^[[:space:]]*$/d'
}

install_packages() {
    local target="$JUNIPER_ROOT"
    [ "$WITH_DEV" = true ] && target="$JUNIPER_ROOT[dev]"
    pkg_install -e "$target" ||
        die "Falha ao instalar as dependências." "$(network_hint)"
}

# Garante que o ambiente tem tudo, reinstalando SÓ o que está com defeito.
ensure_packages() {
    local broken
    broken="$(broken_packages || true)"
    if [ -z "$broken" ]; then
        ok "Dependências já instaladas e saudáveis (nada a fazer)"
        return 0
    fi

    if [ "$VENV_FRESH" = true ]; then
        info "Instalando dependências..."
    else
        info "Reinstalando o que está faltando ou com defeito: $(printf '%s' "$broken" | tr '\n' ' ')"
        # shellcheck disable=SC2046
        pkg_uninstall $(printf '%s' "$broken" | tr '\n' ' ')
    fi
    install_packages

    broken="$(broken_packages || true)"
    if [ -n "$broken" ]; then
        warn "Ainda há defeitos ($(printf '%s' "$broken" | tr '\n' ' ')); recriando o ambiente do zero."
        create_venv
        install_packages
        broken="$(broken_packages || true)"
        [ -z "$broken" ] ||
            die "Não consegui deixar as dependências saudáveis: $(printf '%s' "$broken" | tr '\n' ' ')" \
                "$(network_hint)"
    fi
    ok "Dependências instaladas"
}

# --- Configuração -----------------------------------------------------------------

ensure_config_files() {
    if [ ! -f "$JUNIPER_ROOT/config.yaml" ] && [ -f "$JUNIPER_ROOT/config.yaml.example" ]; then
        cp "$JUNIPER_ROOT/config.yaml.example" "$JUNIPER_ROOT/config.yaml"
        ok "config.yaml criado com os valores padrão"
    fi
    if [ ! -f "$JUNIPER_ROOT/.env" ] && [ -f "$JUNIPER_ROOT/.env.example" ]; then
        cp "$JUNIPER_ROOT/.env.example" "$JUNIPER_ROOT/.env"
        ok ".env criado"
    fi
    [ -f "$JUNIPER_ROOT/.env" ] && chmod 600 "$JUNIPER_ROOT/.env"
    mkdir -p "$LOG_DIR"
    return 0
}

api_key_present() { # api_key_present <VARIÁVEL>
    [ -f "$JUNIPER_ROOT/.env" ] &&
        grep -Eq "^$1=[^[:space:]]+" "$JUNIPER_ROOT/.env"
}

# Pergunta uma chave de API (a única coisa que a Juniper não consegue descobrir sozinha).
# ensure_api_key <VARIÁVEL> <nome> <url> <papel>
ensure_api_key() {
    local var="$1" name="$2" url="$3" role="$4"
    api_key_present "$var" && { ok "Chave $name configurada"; return 0; }
    if [ "$ASSUME_YES" = true ] || [ ! -t 0 ] || [ ! -t 1 ]; then
        warn "Falta a chave $name ($role): crie em $url e coloque $var em $JUNIPER_ROOT/.env"
        return 0
    fi
    local key=""
    say "Preciso de uma chave $name ($role)."
    say "Crie em $url e cole aqui (Enter para pular; a digitação fica oculta)."
    printf '%s ' "$USER_TAG"
    read -rs key || true
    printf '\n'
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    if [ -z "$key" ]; then
        warn "Sem a chave $name por enquanto. Depois, coloque $var=... em $JUNIPER_ROOT/.env"
        return 0
    fi
    if ! printf '%s' "$key" | grep -Eq '^[A-Za-z0-9_.-]{16,}$'; then
        warn "Isso não parece uma chave $name; não salvei. Edite $JUNIPER_ROOT/.env depois."
        return 0
    fi
    local tmp
    tmp="$(mktemp)"
    if grep -q "^$var=" "$JUNIPER_ROOT/.env"; then
        awk -v var="$var" -v k="$key" 'index($0, var "=") == 1 {print var "=" k; next} {print}' \
            "$JUNIPER_ROOT/.env" > "$tmp"
    else
        cat "$JUNIPER_ROOT/.env" > "$tmp"
        printf '%s=%s\n' "$var" "$key" >> "$tmp"
    fi
    cat "$tmp" > "$JUNIPER_ROOT/.env"
    rm -f "$tmp"
    chmod 600 "$JUNIPER_ROOT/.env"
    ok "Chave $name salva em .env"
}

ensure_api_keys() {
    ensure_api_key JUNIPER_DEEPINFRA_API_KEY DeepInfra https://deepinfra.com/dash/api_keys \
        "meu cérebro principal"
    ensure_api_key JUNIPER_GROQ_API_KEY Groq https://console.groq.com/keys \
        "plano B, é grátis"
}

# --- Atalho e shell -----------------------------------------------------------------

link_binary() {
    mkdir -p "$LOCAL_BIN"
    ln -sf "$VENV_DIR/bin/juniper" "$LOCAL_BIN/juniper"
    ok "Atalho: $LOCAL_BIN/juniper"
}

backup_once() {
    [ -f "$1.juniper.bak" ] || cp "$1" "$1.juniper.bak"
}

# rc_add_path <arquivo>: põe ~/.local/bin no PATH, uma única vez.
rc_add_path() {
    local rc="$1"
    [ -f "$rc" ] || return 0
    if grep -qF "$PATH_MARKER" "$rc" || grep -qE '^[[:space:]]*[^#].*(HOME|~)/\.local/bin' "$rc"; then
        return 0
    fi
    backup_once "$rc"
    printf '\n%s\nexport PATH="$HOME/.local/bin:$PATH"\n' "$PATH_MARKER" >> "$rc"
    ok "PATH ajustado em $rc"
}

# rc_add_hook <arquivo>: carrega as funções do shell (search, deploy...) e troca a linha antiga
# (~/.juniper/juniper.sh) pelo caminho novo (~/.juniper/shell/juniper.sh). Só zsh.
rc_add_hook() {
    local rc="$1"
    [ -f "$rc" ] || return 0
    local new='[ -f "$HOME/.juniper/shell/juniper.sh" ] && source "$HOME/.juniper/shell/juniper.sh"'
    if grep -qF 'juniper/shell/juniper.sh' "$rc"; then
        return 0
    fi
    backup_once "$rc"
    if grep -qE '\.juniper/juniper\.sh' "$rc"; then
        local tmp
        tmp="$(mktemp)"
        grep -vE '\.juniper/juniper\.sh' "$rc" > "$tmp"
        cat "$tmp" > "$rc"
        rm -f "$tmp"
    fi
    printf '\n%s\n%s\n' "$HOOK_MARKER" "$new" >> "$rc"
    ok "Comandos do shell carregados em $rc"
}

setup_shell() {
    if [ "$TOUCH_RC" != true ]; then
        info "Arquivos de shell não alterados (--no-rc)"
        return 0
    fi
    # Cria o .zshrc se o zsh é o shell do usuário e o arquivo ainda não existe.
    case "${SHELL:-}" in */zsh) [ -f "$HOME/.zshrc" ] || : > "$HOME/.zshrc" ;; esac
    rc_add_path "$HOME/.zshrc"
    rc_add_path "$HOME/.bashrc"
    if command -v zsh >/dev/null 2>&1; then
        rc_add_hook "$HOME/.zshrc"
    fi
}

# --- Verificação final ----------------------------------------------------------------

smoke_test() {
    "$VENV_DIR/bin/python" -m brain version >/dev/null 2>&1 ||
        die "O teste final falhou: 'python -m brain version'." "Rode com --clean para recomeçar."
    # Importa todas as tools: pega dependência faltando que o 'version' não toca.
    "$VENV_DIR/bin/juniper" tools >/dev/null 2>&1 ||
        die "O teste final falhou: 'juniper tools'." "Rode com --clean para recomeçar."
    if ! env -i HOME="$HOME" PATH="$LOCAL_BIN:/usr/bin:/bin" "$LOCAL_BIN/juniper" version >/dev/null 2>&1; then
        die "O atalho $LOCAL_BIN/juniper não executa." "Rode com --clean para recomeçar."
    fi
    ok "Teste final passou: a Juniper executa"
}

optional_tools_report() {
    command -v git >/dev/null 2>&1 || warn "git não encontrado (os comandos search/deploy precisam dele)"
    command -v zsh >/dev/null 2>&1 || warn "zsh não encontrado (os comandos search/deploy precisam dele)"
    if command -v ollama >/dev/null 2>&1 && curl -fs --max-time 2 http://localhost:11434 >/dev/null 2>&1; then
        ok "Ollama detectado (será o plano B se a Groq falhar)"
    fi
    return 0
}

# --- Fluxo principal ----------------------------------------------------------------------

main() {
    parse_flags "$@"
    mkdir -p "$LOG_DIR"
    printf '\n=== install.sh %s (%s) ===\n' "$*" "$(date '+%F %T')" >> "$LOG_FILE"

    if [ "$CHECK_ONLY" = true ]; then
        say "Deixa eu ver como eu estou..."
    else
        say "Oi! Vou deixar tudo pronto por aqui. Pode relaxar."
    fi
    [ -f "$JUNIPER_ROOT/pyproject.toml" ] || die "pyproject.toml não encontrado em $JUNIPER_ROOT"
    info "Pasta: $JUNIPER_ROOT"

    if [ "$JUNIPER_ROOT" != "$HOME/.juniper" ]; then
        warn "A Juniper deve ficar em ~/.juniper (está em $JUNIPER_ROOT); alguns comandos podem não achar os scripts."
    fi

    if [ "$CHECK_ONLY" = true ]; then
        venv_python_ok || { fail "Ambiente virtual ausente ou quebrado."; exit 1; }
        local broken
        broken="$(broken_packages || true)"
        if [ -n "$broken" ]; then
            fail "Dependências com defeito: $(printf '%s' "$broken" | tr '\n' ' ')"
            exit 1
        fi
        ok "Juniper instalada e saudável"
        exit 0
    fi

    if [ "$CLEAN" = true ]; then
        info "Removendo o ambiente anterior (--clean)"
        rm -rf "$VENV_DIR" "$LEGACY_VENV_DIR"
    elif [ -d "$LEGACY_VENV_DIR" ]; then
        info "Removendo venv/ antigo (não pode ser movido)"
        rm -rf "$LEGACY_VENV_DIR"
    fi

    title "Ambiente"
    if venv_python_ok; then
        ok "Ambiente virtual existente e funcional"
    else
        [ -e "$VENV_DIR" ] && warn "Ambiente virtual quebrado (Python removido ou incompatível): recriando"
        create_venv
    fi

    title "Dependências"
    ensure_packages

    title "Configuração"
    ensure_config_files
    ensure_api_keys
    link_binary
    setup_shell
    optional_tools_report

    title "Verificação"
    smoke_test

    printf '\n'
    say "Pronto! Estou instalada e funcionando."
    say "Abra um terminal novo (ou rode: exec \$SHELL) e me chame:"
    printf '       juniper chat           conversar comigo\n'
    printf '       juniper search 1234    buscar commits\n'
    printf '       juniper tools          ver o que eu sei fazer\n\n'
}

# Permite carregar as funções sem executar (usado nos testes).
if [ "${JUNIPER_INSTALL_LIB:-}" = "1" ]; then
    return 0 2>/dev/null || exit 0
fi

main "$@"
