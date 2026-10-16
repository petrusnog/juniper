---
title: CLI
group: Clientes
order: 1
description: O comando juniper: chat, mensagem única, retomar conversa, comandos de memória, git e utilitários.
status: pronta
tags: juniper comando terminal chat repl provider resume memory tools docs install version exit typer rich
---

## Visão geral {#visao-geral}

O CLI é o cliente atual da Juniper: um cliente **fino** que lê o que você digita, mostra a resposta chegando token a token e deixa a confirmação de ações acontecer na conversa. Toda a lógica continua em `brain/core`. Arquivo: `brain/clients/cli.py` (Typer + Rich). Por padrão o cérebro roda dentro do mesmo processo (`deployment.mode: local`); no modo `server` o CLI fala com o brain remoto ([Modo server](#server), [Produção](producao.html), [Protocolo](protocolo.html)).

Tudo que o terminal diz segue o padrão de [Voz no terminal](voz.html).

## Conversar {#conversar}

```bash
juniper "o que tenho amanhã?"     # uma mensagem: responde e sai
juniper                           # conversa (REPL)
python -m brain chat              # equivalente, sem instalar o atalho
```

Qualquer coisa que não seja um comando conhecido é uma mensagem: `juniper oi` e `juniper "marca dentista amanhã às 16h"` funcionam sem aspas ou comando. No REPL:

```text
👤 VOCÊ: oi
🌿 JUNIPER: Oi! ...
👤 VOCÊ: /sair
```

`/sair`, `/exit` ou Ctrl+D encerram; Ctrl+C também (ainda não cancela só o turno).

### Opções do chat {#opcoes}

| Opção | O que faz |
|---|---|
| `--provider`, `-p` `deepinfra`/`groq`/`ollama` | Força um provider, **sem failover** (útil para testar o Ollama sem a Groq responder no lugar). Um nome inválido é recusado antes de qualquer trabalho, listando os válidos. |
| `--resume`, `-r` | Retoma a conversa mais recente ([Memória de sessão](memoria-sessao.html)). |

```bash
juniper -p ollama "oi"
juniper -r "e o prazo?"           # retoma e já manda uma mensagem
```

### Códigos de saída {#saida}

Uma mensagem única sai com `1` se nenhum LLM respondeu. No REPL, um erro de LLM é mostrado e a sessão continua. `--provider` inválido sai com `2`; Ctrl+C sai com `130`; memória indisponível sai com `1` (antes de falar com o LLM).

## Modo server: o CLI falando com o brain remoto {#server}

Com `deployment.mode: server`, o `juniper` não monta o `Agent` no próprio processo: abre um WebSocket com o servidor (`brain/clients/remote.py` e `remote_cli.py`) e só entrega texto e mostra texto. **A aparência é a mesma**: `juniper`, `juniper <mensagem>` e `--resume` funcionam igual, com streaming, confirmação na conversa e o tempo por etapa quando o servidor liga `agent.show_timing`. Conversas, memórias e perfil ficam **no servidor**: existe uma memória só.

```yaml
# config.yaml do cliente
deployment:
  mode: server
  server:
    host: 127.0.0.1          # o nome do servidor na rede (na VPS, o nome Tailscale)
    port: 8765
  auth:
    token_file: ~/.juniper/token   # o token que o servidor criou no primeiro boot
```

No ambiente Docker de desenvolvimento ([Produção](producao.html#dev)), o token está em `.juniper-dev/state/token`; sem mexer no `config.yaml`: `JUNIPER_DEPLOYMENT__MODE=server JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE=.juniper-dev/state/token juniper "oi"`.

- **Ctrl+C** durante uma resposta **cancela o turno** (o chat continua); no prompt, sai.
- **Queda de conexão:** o cliente reconecta sozinho (1, 2, 4, 8, 16 e depois 30 s, com variação; logo depois de um deploy, em até 2 s) e retoma a conversa. Se cair no meio de uma resposta, o turno termina no servidor e, ao reconectar, o CLI mostra a resposta que ficou salva. Sem servidor: "Juniper indisponível no momento".
- **Comandos de memória** (`list`, `search`, `forget`, `export`, `profile`) falam com a memória do servidor; a confirmação do `forget` continua no cliente. `memory reindex` é administrativo e só roda no servidor.
- **`juniper tools`** lista as tools do servidor (inclui as de executores conectados).
- **Comandos locais** (`search`, `deploy`, `install`, `docs`, `version`) não precisam do servidor.
- Cada processo do CLI é uma conexão própria (`juniper memory list` não derruba o chat aberto).

## Como funciona {#funciona}

| Peça | O que faz |
|---|---|
| `chat` | Comando do Typer: monta o router, o `Dispatcher(conversational=True)`, a memória e o `Agent`, e chama `converse`. |
| `converse` | Um turno (mensagem única) ou um laço de turnos (REPL). Entrega cada turno ao [extrator de memória](memoria-longo-prazo.html#backgroundextractor) em segundo plano. |
| `Printer` | Imprime cada token assim que chega, sem interpretar colchetes como markup do Rich (uma resposta com `[bold]` sai literal). |
| `LineReader` | Lê o teclado numa thread, sem travar o event loop. |
| `friendly_error` | Traduz falhas do LLM em instruções em PT-BR. |
| Confirmações | Sem prompt: a Juniper pergunta em palavras e o seu "sim" é interpretado pelo sistema ([Agente](agente.html#confirmacao)). |

## Erros em português {#erros}

| Situação | O que você vê (resumo) |
|---|---|
| Chave ausente ou inválida | "Não consegui falar com nenhum LLM agora." e a dica "· Groq sem chave válida: defina `JUNIPER_GROQ_API_KEY` no .env." (o provider certo é citado) |
| Limite do provider (429) | "· Limite da Groq atingido: espere um minuto." |
| Ollama fora do ar ou lento | "· Ollama não respondeu: ele está rodando? (`ollama serve`)" |
| Troca de provider (failover) | "A DeepInfra recusou as credenciais. Vou tentar a Groq." (e, ao cair no Ollama: "Sem GPU isso pode levar alguns minutos.") |

Depois das dicas vem uma linha "Detalhe técnico: ..." com a causa de cada provider, para depuração.

## Comandos {#comandos}

| Comando | O que faz |
|---|---|
| `juniper version` | Mostra a versão. |
| `juniper tools` | Lista as [tools](tools.html#disponiveis) registradas: nome, tier, locality e descrição. |
| `juniper search <termos>` | Busca commits ([Comandos git](git.html)). |
| `juniper deploy <feature> [opções]` | Cherry-pick de feature em develop e stage ([Comandos git](git.html)). |
| `juniper install [--dev\|--check\|--clean\|--yes\|--memory=...]` | Instala ou repara a Juniper ([Instalação](install.html#opcoes)). |
| `juniper docs` | Serve este livro de bolso em `http://localhost:47831`. |
| `juniper memory ...` | Memória de longo prazo (abaixo). |

### juniper memory {#memory kind=comando file=brain/clients/cli.py}

Precisam do MongoDB no ar; se não estiver, a mensagem diz o que fazer, sem stacktrace.

```bash
juniper memory list [--type preference|project|fact] [--all]
juniper memory search <termo...> [--limit N] [--threshold X]
juniper memory forget <id> [--yes]
juniper memory export [--vectors] [--output arquivo.json]
juniper memory profile                         # ver
juniper memory profile set "..." --name "..." --birth-date AAAA-MM-DD --nationality "..."
juniper memory profile forget [--yes]
juniper memory reindex [--force]
```

| Comando | O que faz |
|---|---|
| `list` | Mais novas primeiro, com id, tipo, status, data e texto. `--all` inclui as `superseded` (só para auditoria). |
| `search` | Mostra a **nota de cada resultado**: é a ferramenta para calibrar `memory.retrieval.threshold` (`--threshold -1` mostra a nota de tudo). |
| `forget` | Mostra o texto, pergunta "Pode apagar?" (padrão não) e apaga com as versões antigas. `--yes` pula a pergunta. |
| `export` | JSON com `{"profile": ..., "memories": [...]}`; sem os vetores, a menos que `--vectors`. |
| `profile` | Mostra, define (`set`) ou apaga (`forget`) o perfil. |
| `reindex` | Refaz os vetores ([Memória de longo prazo](memoria-longo-prazo.html#reindex)). |

### juniper docs {#docs kind=comando file=brain/clients/cli.py}

Serve a documentação localmente; abra `http://localhost:47831` no navegador (a Juniper não abre sozinha). Ctrl+C encerra. Se a porta estiver ocupada, usa a próxima livre (47832, 47833...) e mostra o endereço certo.

## Aliases escondidos {#aliases}

`grep` e `gitgrep` (= `search`) e `deployfeature` (= `deploy`) continuam funcionando, escondidos do `--help`.

## Limitações conhecidas {#limitacoes}

- Resposta em texto puro: não há renderização de Markdown (a persona já evita Markdown fora de código).
- Ctrl+C encerra o programa mesmo no meio de uma resposta.
- Se uma confirmação expirar, a pergunta antiga continua na tela até você digitar algo.
- `input()` não pode ser cancelado: a thread de leitura é reaproveitada, e é *daemon*, então nunca impede o programa de fechar.

## Testes {#testes}

```bash
pytest tests/test_cli.py -v
```

Cobrem: versão, mensagem única com streaming, colchetes literais, REPL até `/sair` e até EOF, `--provider` inválido e válido, chave ausente (instrução do `.env`, saída 1), REPL sobrevivendo a erro de LLM, as dicas de `friendly_error`, o aviso de irreversível, o reaproveitamento da thread de leitura e um fluxo completo com confirmação negada. Para ver ao vivo, com a chave no `.env`: `juniper "marca um teste amanhã às 10h"`.
