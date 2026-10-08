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

Busca commits no histórico do repositório por um termo específico (ID de tarefa, palavra-chave ou autor).

```bash
juniper search "4911"
juniper grep "correção de bug"
```

Aceita duas ou mais chaves alternativas (OR): retorna commits que contenham qualquer uma delas. Útil para tarefas com subtarefas mergeadas separadamente.

```bash
juniper search 6531 7432
```

### `deploy`

Automatiza o processo de deploy para branches de feature.

```bash
# Uso básico: juniper deploy <id_tarefa>
juniper deploy 4911

# Multi-task: commits de 6531 e 7432 nas branches da task pai (6531)
juniper deploy 6531 --tasks 7432

# Escolhendo o destino (sem flag = develop e stage)
juniper deploy 6531 --tasks 7432 --develop
juniper deploy 6531 --stage
```

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