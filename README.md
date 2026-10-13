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

Tutorial de geração da conta de serviço: rode `juniper docs` (seção "Conta de serviço no GCP").

### 5. Documentação

```bash
juniper docs   # abre o livro de bolso no navegador padrão
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