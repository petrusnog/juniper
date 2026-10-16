---
title: Voz no terminal
group: Clientes
order: 2
description: Como ela fala: os prefixos, fala fixa versus gerada por IA, e a regra para comandos novos.
status: guia
tags: voz prefixo juniper voce say start_reply fala fixa gerada terminal voice.py
---

## O padrão {#padrao}

Tudo que a Juniper diz no terminal segue o mesmo padrão. Você sempre sabe quem está falando, venha a frase de uma IA ou de um texto fixo.

```text
🌿 JUNIPER: Olá, sou Juniper, sua assistente de automações favorita!
👤 VOCÊ: oi, tudo bem?
🌿 JUNIPER: Tô bem, e você? Como foi o dia?
```

| Prefixo | Quem | Observação |
|---|---|---|
| `🌿 JUNIPER:` | A Juniper | Sempre, em qualquer comando. |
| `👤 VOCÊ:` | Você, ao digitar | Um único ponto de troca (`voice.user_tag()`) para virar o seu nome quando o perfil existir. |

> **Nota:** isto é a "voz" **escrita** do terminal. A voz por áudio (STT e TTS locais no cliente, wake word) é da Sprint 4 e ainda não tem ADR.

## Fala gerada × fala fixa {#gerada-fixa}

Nem tudo precisa de IA. Comandos que sempre dizem a mesma coisa usam **texto pronto**: é instantâneo, grátis e funciona sem internet. Só o chat chama o LLM.

| Tipo | Onde | Como fala |
|---|---|---|
| Gerada (IA) | `juniper` (chat) | O LLM escreve a resposta; o terminal coloca o prefixo e mostra token a token. |
| Fixa | `juniper` sem argumentos | Apresentação e lista de comandos. |
| Fixa | `version`, `name`, `help` | Textos prontos. |
| Fixa | `tools` | Uma frase e a tabela do que ela sabe fazer. |
| Fixa | `search`, `deploy` | Mensagens de progresso fixas; os commits listados são dados. |
| Fixa | `install`, `uninstall` | Cada etapa é uma fala dela. |
| Confirmações | Na conversa | "Posso apagar esses cinco? É irreversível." e você responde "sim". Sem `[y/N]`. |
| Perguntas dos scripts | `_juniper_ask` | Ela pergunta; a resposta é sua, com o seu prefixo. |
| Erros | Em português | Com o que fazer. |
| Abertura e despedida do chat | Fixas | "Pode falar, tô te ouvindo" e "Até logo!". |

> **Dica:** regra para comandos novos: se a resposta é sempre a mesma, escreva um texto fixo e fale por `_juniper_say` (shell) ou `voice.say` (Python). Só use o LLM quando a resposta depender do que você disse.

## Como é implementado {#implementacao}

### Python: voice.py {#voice kind=módulo file=brain/clients/voice.py}

```python
from brain.clients import voice
voice.say(console, "mensagem")     # fala fixa (texto puro, nunca interpretado como markup)
voice.start_reply(console)          # abre uma fala gerada: o prefixo, sem quebra de linha
voice.user_prompt()                 # "👤 VOCÊ: "
```

Falas de várias linhas indentam as linhas seguintes sob a primeira.

### Shell: voice.sh {#voice-sh kind=módulo file=shell/core/voice.sh}

```bash
_juniper_say "mensagem"
_juniper_ask "pergunta?" resposta   # mostra a pergunta com o prefixo dela e lê a sua resposta com o seu prefixo
```

Os instaladores em bash têm as mesmas duas constantes. Um teste (`test_shell_and_python_voices_are_identical`) garante que os prefixos do shell, do Python e dos instaladores são **exatamente** os mesmos.

## Detalhes do chat {#detalhes}

- **Prefixo só quando há texto.** Se o LLM falha antes de escrever, não sobra uma linha `🌿 JUNIPER:` vazia: o erro vem como fala dela.
- **Eco do nome é removido.** Se o modelo começar a frase com "Juniper:" ou "🌿 JUNIPER:", o terminal descarta, para não duplicar.
- **Confirmação é diálogo.** Ela pergunta em palavras, você responde "sim" ou "não", e ela conta o resultado. Funciona igual por voz ([Agente](agente.html#confirmacao)).
- **Texto puro.** A fala nunca é interpretada como markup: colchetes e asteriscos aparecem como foram escritos.

## Fora do padrão (de propósito) {#fora}

Scripts de desenvolvimento (`scripts/try_*.py`, `healthcheck.py`, `check_google.py`) e a saída crua de ferramentas (a lista de commits do `search`, a ajuda do `--help` do Typer) não são fala dela e ficam como estão.
