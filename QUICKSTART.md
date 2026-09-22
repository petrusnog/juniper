# 🚀 Guia Rápido - Juniper v2.0.0

## ⚡ Comandos Principais

```bash
# Saudação
juniper

# Ajuda
juniper help

# Versão
juniper --version

# Buscar commits
juniper grep 4911

# Deploy automatizado
juniper deploy 4911 "Fix: corrige bug"
```

## 📝 Criar Novo Comando (3 passos)

### 1️⃣ Copie o template
```bash
cp ~/.juniper/commands/template.sh ~/.juniper/commands/meucomando.sh
```

### 2️⃣ Edite o arquivo
```bash
# Substitua "seucomando" por "meucomando"
# Implemente a lógica em meucomando_run()
# Atualize a ajuda em meucomando_help()
```

### 3️⃣ Recarregue
```bash
source ~/.juniper/juniper.sh
juniper meucomando
```

## 🎯 Estrutura de Arquivos

> ⚠️ O projeto precisa estar em `~/.juniper` (a home reconhecida pelo seu terminal), senão o comando `juniper` não é encontrado.

```
~/.juniper/
├── juniper.sh            ← Ponto de entrada, source no .bashrc/.zshrc
├── commands/           ← Adicione seus comandos aqui
│   ├── template.sh     ← Use este como base
│   ├── gitgrep.sh
│   ├── deploy.sh
│   ├── deployfeature.sh (depreciado)
│   ├── help.sh
│   └── version.sh
├── core/
│   ├── dispatcher.sh   ← Adicione aliases aqui
│   └── loader.sh
└── README.md           ← Documentação completa
```

## 🔧 Adicionar Alias

Edite `~/.juniper/core/dispatcher.sh`:

```bash
JUNIPER_ALIASES=(
    ["grep"]="gitgrep"
    ["grep"]="gitgrep"
    ["mc"]="meucomando"    # ← Adicione aqui
)
```

## 📚 Documentação Completa

Leia o README.md para documentação detalhada:
```bash
cat ~/.juniper/README.md
```

## 🐛 Debug

Se algo não funcionar:
```bash
# 1. Recarregue o shell
source ~/.juniper/juniper.sh

# 2. Verifique se o arquivo existe
ls -la ~/.juniper/commands/

# 3. Teste o comando diretamente
source ~/.juniper/commands/meucomando.sh
meucomando_run teste
```

## 💡 Exemplo Completo

```bash
# Criar novo comando "gitlog"
cat > ~/.juniper/commands/gitlog.sh << 'EOF'
#!/bin/zsh
gitlog_run() {
    git log --oneline -n "${1:-10}"
}
gitlog_help() {
    echo "  gitlog [n] - Mostra últimos n commits (padrão: 10)"
}
EOF

# Recarregar
source ~/.juniper/juniper.sh

# Usar
juniper gitlog 5
```

## 🎨 Dicas

- Use `template.sh` como base para novos comandos
- Comandos são descobertos automaticamente
- Não precisa reiniciar o terminal, apenas `source ~/.juniper.sh`
- Cada comando é independente e testável
- Use `juniper help` para ver todos os comandos

---

**Happy Coding! 🌿**
