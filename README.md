# 🌿 JUNIPER - Assistente de Automações

Toolkit de automação em shell para otimizar o fluxo de trabalho com Git e aumentar a produtividade do desenvolvedor.

## ⚙️ Instalação

### 1. Posicionamento do Projeto

O Juniper deve residir na pasta home do usuário para funcionar corretamente.

```bash
# Clone diretamente no destino correto
git clone git@github.com:petrusnog/juniper.git ~/.juniper
```

### 2. Instalação automática

```bash
bash ~/.juniper/scripts/install.sh
```

Não precisa ter Python: o instalador cria um ambiente virtual em `~/.juniper/.venv` (baixando um Python isolado se for preciso), reaproveita o que já estiver instalado, reinstala só o que estiver com defeito, cria `config.yaml` e `.env`, configura o `~/.zshrc` e termina com um teste real. Abra um terminal novo e use `juniper`.

Depois da primeira instalação, `juniper install` faz o mesmo (`--dev` inclui pytest/ruff/mypy, `--check` só verifica, `--clean` recomeça do zero). Guia completo: `docs/html/install.html`.

### 4. Chave de conta de serviço do GCP

O instalador (`./scripts/install.sh`) cria `~/.juniper/certs/` e pergunta o caminho da sua chave de conta de serviço (qualquer nome de arquivo); ela é validada e copiada para `certs/gcp-key.json`. Para informar sem prompt: `./scripts/install.sh --gcp-key=/caminho/chave.json`.

Tutorial de geração da conta de serviço: `docs/html/install.html` (seção "Conta de serviço no GCP"), ou rode `juniper docs`.

### 5. Documentação

```bash
juniper docs   # serve o livro de bolso em http://localhost:47831 (Ctrl+C encerra)
```

Se a porta 47831 estiver ocupada, a Juniper usa a próxima livre e mostra o endereço no terminal.

## 🚀 Guia de Uso

### `search` (ou `grep`)

Busca commits no histórico do repositório por um ou mais termos (ID de tarefa, palavra-chave ou autor).

```bash
juniper search "4911"
juniper grep "correção de bug"
```

#### Busca por múltiplas chaves

Informe duas ou mais chaves para obter os commits que contenham **qualquer uma delas** (lógica OR). É útil quando uma task gerou subtasks que foram mergeadas separadamente.

```bash
# Task 6531 e sua subtask 7432: commits que contenham 6531 ou 7432
juniper search 6531 7432
```

Cada termo é interpretado como regex básico do `git log --grep`. Se nenhum commit for encontrado, a Juniper lista os termos buscados.

### `deploy`

Automatiza o processo de deploy (cherry-pick) para as branches `feature/<id>-develop` e `feature/<id>-stage`.

```bash
# Busca os commits feature(<id>) e hotfix(<id>) e aplica em develop e stage
juniper deploy 4911

# Cria o commit e aplica em develop e stage
juniper deploy 4911 "Fix: corrige bug no login"

# Aplica um commit já existente
juniper deploy 4911 <hash> --hash
```

Commits que já estão presentes na branch de destino (de execuções anteriores) são detectados e ignorados; apenas a diferença é aplicada. Em caso de conflito, a execução pausa até você resolver e responder `continuar` ou `abortar`.

#### Multi-task (`--tasks`)

Sincroniza os commits da task pai **e** de uma ou mais tasks relacionadas nas branches da task pai. Os commits são reunidos em ordem cronológica.

```bash
# Commits de 6531 e 7432 → feature/6531-develop e feature/6531-stage
juniper deploy 6531 --tasks 7432

# Mais de uma task extra
juniper deploy 6531 --tasks 7432 8100
```

Restrições:

- A task pai (primeiro argumento) define as branches de destino e sempre é incluída.
- `--tasks` aceita apenas IDs numéricos.
- `--tasks` só funciona no modo em lote: não pode ser combinado com `<mensagem>` nem com `--hash`.

#### Destino (`--develop` / `--stage`)

Escolhe em quais branches aplicar. Sem nenhuma flag (ou com as duas), aplica em ambas. As flags valem para todos os modos do `deploy`.

| Flag | Destino |
|------|---------|
| *(nenhuma)* | `feature/<id>-develop` e `feature/<id>-stage` |
| `--develop` | somente `feature/<id>-develop` |
| `--stage` | somente `feature/<id>-stage` |
| `--develop --stage` | ambas |

```bash
juniper deploy 6531 --tasks 7432 --develop
juniper deploy 6531 --tasks 7432 --stage
juniper deploy 4911 --stage
```

Se uma branch de destino não existir, a Juniper pergunta antes de criá-la a partir de `develop` ou `stage`.

### `help`

Lista todos os comandos disponíveis e suas respectivas sintaxes.

```bash
juniper help
```

### `version`

Exibe a versão atual do toolkit.

```bash
juniper version
```

---

*Criado por: Petrus Rennan ☕*