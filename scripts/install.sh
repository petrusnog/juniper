#!/usr/bin/env bash
#
# Juniper v4 - Setup de ambiente de desenvolvimento (idempotente)
#
# Uso:
#   ./scripts/install.sh              # setup completo
#   ./scripts/install.sh --clean      # recria o venv do zero
#   ./scripts/install.sh --no-ollama  # pula validação do Ollama
#   ./scripts/install.sh --ci         # modo CI (sem cores, sem alterar .zshrc)
#   ./scripts/install.sh --gcp-key=/caminho/chave.json   # informa a chave sem prompt
#
# O que este script faz:
#   1. Valida Python >= 3.11
#   2. Cria/reaproveita .venv/ na raiz do repo
#   3. Instala dependências (pip install -e ".[dev]")
#   4. Cria config.yaml e .env a partir dos exemplos
#      Cria ~/.juniper/certs/ e pede o caminho da chave de conta de serviço
#      do GCP (qualquer nome); copia para certs/gcp-key.json
#   5. Cria symlink ~/.local/bin/juniper -> .venv/bin/juniper
#   6. Adiciona ~/.local/bin ao PATH do .zshrc/.bashrc (se necessário)
#   7. Aplica o PATH na sessão atual (funciona imediatamente)
#   8. Valida que 'juniper version' funciona em shell novo
#
# Após rodar, 'juniper' funciona em qualquer terminal, sem ativar venv.

set -euo pipefail

# --- Configuração -----------------------------------------------------

JUNIPER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$JUNIPER_ROOT/.venv"
LEGACY_VENV_DIR="$JUNIPER_ROOT/venv"
LOCAL_BIN="$HOME/.local/bin"
LOG_DIR="$HOME/.juniper/logs"
PATH_MARKER='# Juniper - PATH'

# --- Flags ------------------------------------------------------------

CLEAN=false
CHECK_OLLAMA=true
CI_MODE=false
GCP_KEY_ARG=""
CERTS_DIR="${JUNIPER_HOME:-$HOME/.juniper}/certs"
GCP_KEY_DEST="$CERTS_DIR/gcp-key.json"

for arg in "$@"; do
    case "$arg" in
        --clean)     CLEAN=true ;;
        --no-ollama) CHECK_OLLAMA=false ;;
        --ci)        CI_MODE=true ;;
        --gcp-key=*) GCP_KEY_ARG="${arg#--gcp-key=}" ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Flag desconhecida: $arg" >&2
            exit 1
            ;;
    esac
done

# --- Cores ------------------------------------------------------------

if [ "$CI_MODE" = true ] || [ ! -t 1 ]; then
    C_RESET=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BOLD=""; C_DIM=""
else
    C_RESET="\033[0m"
    C_GREEN="\033[32m"
    C_YELLOW="\033[33m"
    C_RED="\033[31m"
    C_BOLD="\033[1m"
    C_DIM="\033[2m"
fi

ok()    { printf "${C_GREEN}[ OK ]${C_RESET} %s\n" "$1"; }
warn()  { printf "${C_YELLOW}[WARN]${C_RESET} %s\n" "$1"; }
fail()  { printf "${C_RED}[FAIL]${C_RESET} %s\n" "$1" >&2; }
info()  { printf "${C_DIM}[ .. ]${C_RESET} %s\n" "$1"; }
title() { printf "\n${C_BOLD}== %s ==${C_RESET}\n" "$1"; }

declare -a SUMMARY=()
add_summary() { SUMMARY+=("$1"); }

# --- Guard clauses ----------------------------------------------------

title "Juniper v4 - Setup"

if [ ! -f "$JUNIPER_ROOT/pyproject.toml" ]; then
    fail "pyproject.toml não encontrado em $JUNIPER_ROOT"
    fail "Execute este script de dentro do repositório Juniper."
    exit 1
fi
info "Raiz do projeto: $JUNIPER_ROOT"

# --- Python >= 3.11 ---------------------------------------------------

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=${version%%.*}
        minor=${version##*.}
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    fail "Python >= 3.11 não encontrado."
    fail "Instale em https://www.python.org/downloads/"
    exit 1
fi
PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
ok "Python $PY_VERSION ($PYTHON_BIN)"
add_summary "[OK] Python $PY_VERSION"

# --- Limpeza / reconciliação de venv ----------------------------------

if [ "$CLEAN" = true ]; then
    [ -d "$VENV_DIR" ] && rm -rf "$VENV_DIR" && info "Removido .venv/ (--clean)"
    [ -d "$LEGACY_VENV_DIR" ] && rm -rf "$LEGACY_VENV_DIR" && info "Removido venv/ legado (--clean)"
fi

# Remover venv legado sem ponto (não dá para mover: shebangs são absolutos)
if [ -d "$LEGACY_VENV_DIR" ]; then
    info "Removendo venv legado em 'venv/' (não pode ser movido)"
    rm -rf "$LEGACY_VENV_DIR"
    ok "Venv legado removido"
    add_summary "[OK] Venv legado limpo"
fi

# --- Criar venv -------------------------------------------------------

if [ ! -d "$VENV_DIR" ]; then
    info "Criando .venv/ em $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Venv criado"
    add_summary "[OK] .venv/ criado"
else
    ok ".venv/ já existe"
    add_summary "[OK] .venv/ existente"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Sanity check do python do venv
if ! command -v python >/dev/null 2>&1 || ! python -c 'import sys' >/dev/null 2>&1; then
    fail "Python do venv não funciona"
    fail "Rode: rm -rf .venv && ./scripts/install.sh"
    exit 1
fi

# --- pip + dependências -----------------------------------------------

info "Atualizando pip"
python -m pip install --quiet --upgrade pip

info "Instalando dependências (editable + dev)"
if ! python -m pip install --quiet -e ".[dev]"; then
    fail "Falha ao instalar dependências"
    exit 1
fi
ok "Dependências instaladas"
add_summary "[OK] Dependências instaladas"

# --- Validar binário juniper ------------------------------------------

JUNIPER_BIN="$VENV_DIR/bin/juniper"

if [ ! -x "$JUNIPER_BIN" ]; then
    fail "Binário juniper não encontrado em $JUNIPER_BIN"
    fail "Verifique se [project.scripts] está no pyproject.toml"
    exit 1
fi

if [ ! -s "$JUNIPER_BIN" ]; then
    fail "Binário juniper está vazio (0 bytes)"
    fail "Tente: pip install --force-reinstall --no-deps -e ."
    exit 1
fi

if ! "$JUNIPER_BIN" version >/dev/null 2>&1; then
    fail "Binário juniper não executa corretamente"
    fail "Rode manualmente: $JUNIPER_BIN version"
    exit 1
fi
ok "Binário juniper funcional"
add_summary "[OK] Binário funcional"

# --- jq (aviso) -------------------------------------------------------

if command -v jq >/dev/null 2>&1; then
    ok "jq instalado"
    add_summary "[OK] jq"
else
    warn "jq não encontrado (recomendado para tools JSON)"
    add_summary "[WARN] jq ausente"
fi

# --- Ollama (aviso) ---------------------------------------------------

if [ "$CHECK_OLLAMA" = true ]; then
    if command -v ollama >/dev/null 2>&1 && curl -sS --max-time 2 http://localhost:11434 >/dev/null 2>&1; then
        ok "Ollama online em localhost:11434"
        add_summary "[OK] Ollama online"
    else
        warn "Ollama não disponível (opcional, use Groq)"
        add_summary "[WARN] Ollama offline"
    fi
else
    info "Validação do Ollama pulada"
    add_summary "[SKIP] Ollama"
fi

# --- config.yaml ------------------------------------------------------

if [ ! -f "$JUNIPER_ROOT/config.yaml" ] && [ -f "$JUNIPER_ROOT/config.yaml.example" ]; then
    cp "$JUNIPER_ROOT/config.yaml.example" "$JUNIPER_ROOT/config.yaml"
    ok "config.yaml criado"
    add_summary "[OK] config.yaml criado"
elif [ -f "$JUNIPER_ROOT/config.yaml" ]; then
    ok "config.yaml já existe"
    add_summary "[OK] config.yaml existente"
fi

# --- .env -------------------------------------------------------------

if [ ! -f "$JUNIPER_ROOT/.env" ] && [ -f "$JUNIPER_ROOT/.env.example" ]; then
    cp "$JUNIPER_ROOT/.env.example" "$JUNIPER_ROOT/.env"
    ok ".env criado (preencher GROQ_API_KEY depois)"
    add_summary "[OK] .env criado"
elif [ -f "$JUNIPER_ROOT/.env" ]; then
    ok ".env já existe"
    add_summary "[OK] .env existente"
fi

# --- Certificados (chave de conta de serviço do GCP) ------------------

# Valida que o arquivo é JSON de conta de serviço do GCP
validate_gcp_key() {
    python - "$1" <<'PYEOF'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
except (OSError, ValueError) as exc:
    print(f"não foi possível ler como JSON: {exc}")
    sys.exit(1)
if data.get("type") != "service_account":
    print("o JSON não é de uma conta de serviço (campo type != service_account)")
    sys.exit(1)
missing = [k for k in ("project_id", "private_key", "client_email") if not data.get(k)]
if missing:
    print("campos ausentes: " + ", ".join(missing))
    sys.exit(1)
PYEOF
}

# Copia a chave informada para certs/gcp-key.json (permissão 600)
install_gcp_key() {
    local src="$1" err
    src="${src/#\~/$HOME}"                # expande ~ no início
    src="${src%\"}"; src="${src#\"}"      # remove aspas coladas
    src="${src%\'}"; src="${src#\'}"

    if [ ! -f "$src" ]; then
        fail "Arquivo não encontrado: $src"
        return 1
    fi
    if ! err=$(validate_gcp_key "$src"); then
        fail "Chave inválida: $err"
        return 1
    fi

    cp "$src" "$GCP_KEY_DEST"
    chmod 600 "$GCP_KEY_DEST"
    ok "Chave copiada para $GCP_KEY_DEST"
    return 0
}

mkdir -p "$CERTS_DIR"
chmod 700 "$CERTS_DIR"
ok "Pasta de certificados: $CERTS_DIR"
add_summary "[OK] certs/ criada"

INTERACTIVE=true
if [ "$CI_MODE" = true ] || [ ! -t 0 ]; then INTERACTIVE=false; fi

if [ -n "$GCP_KEY_ARG" ]; then
    if install_gcp_key "$GCP_KEY_ARG"; then
        add_summary "[OK] gcp-key.json instalada"
    else
        fail "Falha ao instalar a chave informada em --gcp-key"
        exit 1
    fi
elif [ "$INTERACTIVE" = false ]; then
    if [ -f "$GCP_KEY_DEST" ]; then
        ok "gcp-key.json já existe"
        add_summary "[OK] gcp-key.json existente"
    else
        warn "Sem terminal interativo: pulei a chave do GCP (use --gcp-key=CAMINHO)"
        add_summary "[WARN] gcp-key.json pendente"
    fi
else
    DO_ASK=true
    if [ -f "$GCP_KEY_DEST" ]; then
        read -r -p "gcp-key.json já existe. Substituir? [s/N] " reply
        case "$reply" in s|S|y|Y) ;; *) DO_ASK=false ;; esac
        if [ "$DO_ASK" = false ]; then
            ok "Mantendo gcp-key.json atual"
            add_summary "[OK] gcp-key.json existente"
        fi
    fi
    if [ "$DO_ASK" = true ]; then
        info "Guia para gerar a chave: 'juniper docs' (seção Conta de serviço no GCP)"
        installed=false
        for attempt in 1 2 3; do
            read -r -p "Caminho da chave de conta de serviço do GCP (enter para pular): " key_path
            [ -z "$key_path" ] && break
            if install_gcp_key "$key_path"; then installed=true; break; fi
        done
        if [ "$installed" = true ]; then
            add_summary "[OK] gcp-key.json instalada"
        else
            warn "Chave do GCP não instalada. Rode ./scripts/install.sh --gcp-key=CAMINHO depois."
            add_summary "[WARN] gcp-key.json pendente"
        fi
    fi
fi

# --- Diretório de logs ------------------------------------------------

mkdir -p "$LOG_DIR"
ok "Logs em $LOG_DIR"
add_summary "[OK] ~/.juniper/logs/"

# --- Symlink global ---------------------------------------------------

mkdir -p "$LOCAL_BIN"
ln -sf "$JUNIPER_BIN" "$LOCAL_BIN/juniper"
ok "Symlink: $LOCAL_BIN/juniper"
add_summary "[OK] Symlink global criado"

# --- Injeção de PATH (robusta, no FINAL do arquivo) -------------------

inject_path() {
    local rc_file="$1"

    [ -f "$rc_file" ] || return 0

    # Já existe linha NÃO-COMENTADA com ~/.local/bin?
    if grep -qE '^[[:space:]]*[^#].*HOME/\.local/bin' "$rc_file"; then
        info "PATH já configurado em $rc_file"
        return 0
    fi

    # Remover marker antigo (se houver) e a linha que ele precede
    if grep -qF "$PATH_MARKER" "$rc_file"; then
        info "Removendo marker antigo em $rc_file"
        # Remove o marker e a linha seguinte (o export)
        sed -i.bak "/$PATH_MARKER/,+1d" "$rc_file"
        rm -f "$rc_file.bak"
    fi

    # Injetar no FINAL (depois de tudo, inclusive Oh My Zsh)
    {
        echo ""
        echo "$PATH_MARKER"
        echo 'export PATH="$HOME/.local/bin:$PATH"'
    } >> "$rc_file"

    ok "PATH injetado no final de $rc_file"
}

inject_path "$HOME/.zshrc"
inject_path "$HOME/.bashrc"

# --- Aplicar PATH na sessão atual -------------------------------------

# Detecta o shell atual pelo $SHELL
CURRENT_SHELL_NAME="$(basename "${SHELL:-bash}")"
export PATH="$HOME/.local/bin:$PATH"
ok "PATH aplicado na sessão atual (via export)"
add_summary "[OK] PATH ativo nesta sessão"

# --- Validar que PATH funciona em shell NOVO --------------------------

SHELL_TEST_PASSED=false

for shell_to_test in zsh bash; do
    if command -v "$shell_to_test" >/dev/null 2>&1; then
        if "$shell_to_test" -ic 'command -v juniper >/dev/null 2>&1' 2>/dev/null; then
            ok "Shell novo ($shell_to_test) encontra 'juniper'"
            add_summary "[OK] PATH funciona em $shell_to_test"
            SHELL_TEST_PASSED=true
        else
            warn "Shell novo ($shell_to_test) NÃO encontra 'juniper'"
            add_summary "[WARN] PATH não validado em $shell_to_test"
        fi
    fi
done

if [ "$SHELL_TEST_PASSED" = false ]; then
    warn "Nenhum shell novo conseguiu encontrar 'juniper'."
    warn "Verifique se $HOME/.local/bin existe e se o shell carrega o rc file."
fi

# --- Smoke test -------------------------------------------------------

title "Smoke test"

if python -m brain version >/dev/null 2>&1; then
    ok "python -m brain version OK"
    add_summary "[OK] Smoke test (módulo)"
else
    fail "python -m brain version falhou"
    exit 1
fi

if "$JUNIPER_BIN" version >/dev/null 2>&1; then
    ok "binário juniper OK"
    add_summary "[OK] Smoke test (binário)"
else
    fail "binário juniper falhou"
    exit 1
fi

# --- Resumo final -----------------------------------------------------

if [ "$CI_MODE" = false ]; then
    printf "\n${C_BOLD}---------------------------------------------${C_RESET}\n"
    printf "${C_BOLD}  Juniper v4 - Setup concluído${C_RESET}\n"
    printf "${C_BOLD}---------------------------------------------${C_RESET}\n"
    for line in "${SUMMARY[@]}"; do
        printf "  %s\n" "$line"
    done
    printf "${C_BOLD}---------------------------------------------${C_RESET}\n"
    if [ "$SHELL_TEST_PASSED" = true ]; then
        printf "  Pronto! Abra um terminal novo e rode:\n"
        printf "    juniper version\n"
        printf "  Ou, nesta sessão:\n"
        printf "    juniper version   (já funciona)\n"
    else
        printf "  ${C_YELLOW}⚠  Ação necessária:${C_RESET}\n"
        printf "  Abra um terminal novo e rode 'juniper version'.\n"
        printf "  Se falhar, verifique se seu shell carrega ~/.zshrc.\n"
    fi
    printf "${C_BOLD}---------------------------------------------${C_RESET}\n\n"
fi

exit 0