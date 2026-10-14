#!/bin/zsh
################################################################################
# CORE VOICE
# Padroniza a fala da Juniper e o prompt do usuário no terminal.
#
#   🌿 JUNIPER: <fala da Juniper>      (sempre este prefixo)
#   👤 VOCÊ: <o que o usuário digita>  (em breve com o nome do usuário)
#
# Comandos sem IA falam com textos fixos, mas SEMPRE por aqui; o chat (IA) usa o
# mesmo prefixo em brain/clients/voice.py. Um teste garante que os dois batem.
################################################################################

JUNIPER_TAG="🌿 JUNIPER:"
JUNIPER_USER_TAG="👤 VOCÊ:"

# Uso: _juniper_say <mensagem>
_juniper_say() {
    echo "$JUNIPER_TAG $*"
}

# Prefixo do usuário (ponto único: quando a memória existir, devolve o nome dele).
_juniper_user_tag() {
    echo "$JUNIPER_USER_TAG"
}

# Uso: _juniper_ask <pergunta> <variável>
# A Juniper pergunta e a resposta do usuário (com o prefixo dele) vai para <variável>.
# Sem terminal (EOF), devolve 1 e a variável fica vazia.
_juniper_ask() {
    local _question="$1" _var_name="$2"
    _juniper_say "$_question"
    # Após git/editor/merge o terminal pode ficar sem icrnl: o Enter chega como ^M (\r)
    # e o read não recebe o fim da linha. Restaura o modo normal antes de ler.
    [ -t 0 ] && stty icrnl icanon echo 2>/dev/null
    local _answer
    read "_answer?$(_juniper_user_tag) " || return 1
    # Garante que um \r residual não contamine a resposta
    typeset -g "${_var_name}=${_answer//$'\r'/}"
}
