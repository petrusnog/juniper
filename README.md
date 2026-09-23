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

### `deploy`

Automatiza o processo de deploy para branches de feature.

```bash
# Uso básico: juniper deploy <id_tarefa>
juniper deploy 4911

# Com mensagem personalizada:
juniper deploy 4911 "Fix: corrige erro de autenticação"

# Via hash do commit:
juniper deploy 4911 11b81fbe88ed7867d2759037b9406c39f60666f1 --hash
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