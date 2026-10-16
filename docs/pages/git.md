---
title: Comandos git
group: Integrações
order: 2
description: search e deploy no terminal e pelo agente (git_search e git_deploy).
status: pronta
tags: git search deploy cherry-pick commits legado shell zsh feature develop stage git_search git_deploy
---

## Visão geral {#visao-geral}

Os comandos da v2 (`search` e `deploy`) continuam funcionando no terminal, e a Juniper também os usa quando você pede em linguagem natural: *"Juniper, busque os commits da feature 1339"*. Arquivos: `shell/`, `brain/tools/juniper_cmds.py` e `brain/clients/cli.py`.

```text
Você: juniper search 1339 ──► CLI ──► shell/ (zsh): search_run, deploy_run
Agente: "busque a feature 1339" ──► tool git_search ──► shell/ (zsh)
```

A lógica continua nos scripts de `shell/` (a regra "shell é interface, Python é cérebro" vale no sentido de orquestrar). CLI e tools só chamam `zsh -c 'source shell/juniper.sh; _juniper_dispatch "$@"'`, **com cada argumento separado**: nada passa por uma string de shell.

## No terminal {#terminal}

```bash
juniper search 4911 6531                  # commits com qualquer um dos termos
juniper deploy 4911                       # cherry-pick em develop e stage
juniper deploy 6531 --tasks 7432 --stage  # multi-task, só stage
juniper deploy 4911 11b81fb --hash        # um commit já existente
```

Os argumentos passam direto ao script, com o terminal interativo (as perguntas de conflito e de criar branch funcionam como na v2). Os nomes antigos `grep`, `gitgrep` e `deployfeature` continuam existindo, escondidos do `--help`. O código de saída do script é preservado.

## Pelo agente {#agente}

Ambas agem no repositório do **diretório atual**: rode `juniper` dentro do projeto. Fora de um repo git, a tool avisa em vez de falhar misteriosamente. Não há parâmetro para apontar outro repositório, de propósito (menos superfície de ataque).

### git_search {#git-search kind=tool file=brain/tools/juniper_cmds.py}

`READ`, `locality = EXECUTOR`. Parâmetro `terms` (lista): "commits da feature 1339" vira `terms=["1339"]`. Roda direto, sem confirmação. Devolve data, hash, mensagem e autor, sem cores. "Nenhum commit encontrado" é uma resposta, não um erro. Limites: até 10 termos de até 200 caracteres.

### git_deploy {#git-deploy kind=tool file=brain/tools/juniper_cmds.py}

`DESTRUCTIVE`, `locality = EXECUTOR`. Parâmetros: `feature_id`, `extra_tasks` (opcional) e `target` (`both`, `develop` ou `stage`). Faz `push`, então **sempre** pede confirmação na conversa, com aviso de irreversível. Só o modo em lote é exposto ao agente: o modo "mensagem" (`git add .` + commit) e o `--hash` ficam apenas no terminal, onde você vê o que acontece.

O que ele verifica **antes** de te perguntar:

| Verificação | Resultado |
|---|---|
| `feature_id` e `extra_tasks` só com dígitos (`13;rm` e `$(x)` são recusados) | recusa |
| É um repositório git | recusa |
| Alterações não commitadas (o deploy troca de branch) | recusa |
| Existe commit `feature(id)` ou `hotfix(id)` | recusa se não houver: não adianta pedir confirmação para um deploy vazio |

## Sem terminal, sem travar {#sem-terminal}

O script `deploy` pergunta coisas (criar branch? como resolver conflito?). Quando o agente o roda, não há ninguém para responder:

- O comando roda com a entrada fechada e um tempo máximo (30 s no `search`, 5 min no `deploy`).
- **Branch inexistente:** a pergunta recebe "fim da entrada", conta como "não" e a Juniper conta que a criação foi recusada. Crie a branch você mesmo ou rode `juniper deploy` no terminal.
- **Conflito no cherry-pick:** sem terminal, o script aborta o cherry-pick e volta à branch original (antes, a pergunta repetia para sempre quando a entrada acabava).

## Scripts e caminhos {#scripts}

`search.sh`, `deploy.sh` e `install.sh` vieram da `origin/master` (mais novos que a v4): `search` com vários termos, `deploy` com detecção de commits já presentes, `--tasks` e `--develop`/`--stage`. Os scripts usam `JUNIPER_SHELL_DIR` (definido por `shell/juniper.sh`). Se o seu `.zshrc` faz `source ~/.juniper/juniper.sh`, troque para `source ~/.juniper/shell/juniper.sh`.

## Limitações conhecidas {#limitacoes}

- O repositório é o diretório atual.
- A saída do `deploy` (cortada em 20 KB) volta inteira ao LLM, que gasta tokens para resumi-la.
- Depende do `zsh` instalado, como na v2.
- Com o cérebro na VPS, essas tools precisam rodar **na sua máquina**: o CLI remoto as registra como executor enquanto estiver conectado ([Executor](executor.html#cli-executor)).

## Testes {#testes}

```bash
pytest tests/test_juniper_cmds.py tests/test_cli.py -v
```

Usam repositórios git temporários de verdade, sem rede: busca (sem cores, OU entre termos, sem resultado, metacaracteres literais, termos inválidos, fora de repo), as validações do deploy e um deploy completo contra um remoto local, inclusive rodá-lo duas vezes sem duplicar commits e a recusa de criar branch sem terminal.
