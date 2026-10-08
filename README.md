# 🌿 JUNIPER - Assistente de Automações

Toolkit de automação em shell para otimizar o fluxo de trabalho com Git e aumentar a produtividade do desenvolvedor.

## ⚙️ Instalação

### 1. Posicionamento do Projeto

O Juniper deve residir na pasta home do usuário para funcionar corretamente.

```bash
# Clone diretamente no destino correto
git clone git@github.com:petrusnog/juniper.git ~/.juniper
```

### 2. Configuração do Shell

Adicione a linha de inicialização ao arquivo de configuração do seu shell:

**Para Zsh (`~/.zshrc`):**

```bash
echo '[ -f ~/.juniper/juniper.sh ] && source ~/.juniper/juniper.sh' >> ~/.zshrc && source ~/.zshrc
```

**Para Bash (`~/.bashrc`):**

```bash
echo '[ -f ~/.juniper/juniper.sh ] && source ~/.juniper/juniper.sh' >> ~/.bashrc && source ~/.bashrc
```

### 3. Primeiro Passo: Setup

Após a instalação, execute o comando de instalação para validar as dependências do sistema:

```bash
juniper install
```

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