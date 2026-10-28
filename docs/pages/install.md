---
title: Instalação sem Docker (modo local)
group: Ambiente Local
order: 5
description: O instalador da Juniper: o cérebro dentro do próprio terminal (modo local), sem contêineres, a partir de uma máquina limpa.
status: guia
tags: instalar install.sh venv python uv gcp conta de serviço docker mongodb desinstalar dependências
---

## Instalação rápida {#rapida}

Além do [ambiente Docker](primeiros-passos.md), a Juniper pode rodar inteira dentro do processo do terminal: o **modo local** (`deployment.mode: local`), em que o cérebro, a memória e as ferramentas rodam no próprio comando `juniper`, sem servidor. É o jeito mais rápido de usar a Juniper sozinho numa máquina, e é o que o instalador monta. Os clientes remotos (Telegram, app) precisam do modo servidor e ficam de fora.

O instalador monta a Juniper e as dependências a partir de uma máquina limpa. Não precisa ter Python instalado e nada é instalado no sistema: tudo fica dentro de `~/.juniper`, num ambiente virtual. Você precisa só de `git`, `bash` e `curl` (ou `wget`).

```bash
git clone https://github.com/<owner>/juniper.git ~/.juniper
bash ~/.juniper/scripts/install.sh
```

No final aparece "Juniper instalada e pronta para uso". Abra um terminal novo e rode `juniper`. Depois da primeira instalação o comando também existe dentro do shell: `juniper install` faz o mesmo ([opções](#opcoes)).

Durante a instalação ela pede só o que não tem como descobrir sozinha:

1. **As chaves dos LLMs:** DeepInfra, o cérebro principal (deepinfra.com/dash/api_keys), e Groq, o plano B grátis (console.groq.com/keys). Cole e pronto; Enter pula, e você pode colocar depois em `~/.juniper/.env`.
2. **O caminho da chave da conta de serviço do GCP** (arquivo `.json` com qualquer nome). Ela cria `~/.juniper/certs/`, valida a chave e a salva como `certs/gcp-key.json`. Ainda não tem a chave? Siga a [conta de serviço no GCP](#gcp) e rode o instalador depois (ou use `--gcp-key`).
3. **Onde guardar a memória:** em arquivos (o padrão: basta Enter, não instala nada) ou no MongoDB, que precisa do **Docker** instalado e rodando. Se escolher o MongoDB, ela faz o resto ([detalhes](memoria-sessao.md#mongodb)). Sem Docker, escolha arquivos.

Para ler a documentação a qualquer momento: `juniper docs` e acesse `http://localhost:47831`.

## O que o instalador faz {#faz}

```text
Python (do sistema ou isolado) → .venv → dependências (só o que falta) → configuração (config, .env, atalho) → memória (arquivos ou MongoDB) → teste final (ela executa de verdade)
```

### Python {#python}

O instalador procura um Python 3.11+ que consiga criar ambientes virtuais. Se achar, usa. Se não achar (ou se for um Python incompleto, como o do Ubuntu sem `python3-venv`), baixa o `uv` com verificação de checksum e usa-o para baixar um Python 3.12 isolado. Os dois ficam em `~/.juniper/.tools/`, sem root e sem tocar no que já existe.

### Reaproveita o que existe, repara o que está com defeito {#repara}

| Componente | Como é verificado | Saudável | Com defeito |
|---|---|---|---|
| Ambiente virtual `.venv` | O Python dele executa e é 3.11+. | Reutiliza. | Apaga e recria. |
| Cada dependência | Está instalada, na versão mínima do `pyproject.toml`, e importa. | Pula. | Desinstala só ela e reinstala; se continuar ruim, recria o ambiente. |
| O pacote `juniper` | Importa a partir de `~/.juniper`. | Pula. | Reinstala em modo editável. |
| `config.yaml`, `.env` | Existem? | Nunca sobrescreve. | Cria a partir dos exemplos. |

Rodar de novo é seguro: numa instalação saudável ele só confirma ("nada a fazer") em poucos segundos.

### Falhas de rede {#rede}

Cada download e instalação é tentado até 3 vezes. Se mesmo assim falhar, a mensagem diz a causa em português (por exemplo, "sem conexão com a internet") e o detalhe técnico fica em `~/.juniper/logs/install.log`. O instalador só termina com sucesso se o teste final passar.

## Dependências {#dependencias}

| Grupo | Pacotes |
|---|---|
| Execução (sempre) | `httpx`, `pydantic`, `pydantic-settings`, `pyyaml`, `python-dotenv`, `rich`, `typer`, `groq`, `google-api-python-client`, `google-auth`, `pymongo`, `numpy`, `onnxruntime`, `tokenizers` |
| Desenvolvimento (`--dev`) | `pytest`, `pytest-asyncio`, `ruff`, `mypy` |

A fonte de verdade é o `pyproject.toml`. **Ferramentas do sistema** (o instalador só avisa, não instala sem root): `git` e `zsh` para os comandos `search`/`deploy`. O **Ollama** é opcional (plano B se os outros falharem). O **Docker** só é necessário se você escolher guardar a memória no MongoDB.

## Opções {#opcoes}

| Opção | Efeito |
|---|---|
| `--dev` | Instala também `pytest`, `pytest-asyncio`, `ruff` e `mypy`. |
| `--check` | Só verifica e diz se está saudável; não altera nada. |
| `--clean` | Apaga o ambiente e instala do zero. |
| `--yes` | Sem perguntas (scripts e CI). |
| `--no-rc` | Não altera `~/.zshrc` nem `~/.bashrc`. |
| `--memory=file\|mongo` | Escolhe onde guardar a memória sem perguntar. `mongo` exige o Docker instalado e rodando; sem ele, o instalador avisa e fica com arquivos. Sem esta opção e sem terminal (`--yes`, CI), usa arquivos e nunca mexe no Docker. |
| `--gcp-key=CAMINHO` | Informa a chave de conta de serviço do GCP sem perguntar; é copiada para `certs/gcp-key.json`. |

```bash
juniper install --dev                      # dentro do shell (zsh), ou:
bash ~/.juniper/scripts/install.sh --dev
```

## Ambiente de desenvolvimento {#dev}

> **Docker:** para subir o cérebro e o MongoDB em contêineres, com o código montado por volume e os clientes remotos, siga [Primeiros passos](primeiros-passos.md).

```bash
bash ~/.juniper/scripts/install.sh --dev
source ~/.juniper/.venv/bin/activate
pytest tests/ -v            # testes (não usam rede nem chave real)
ruff check brain/ tests/    # lint
mypy brain/                 # tipos (strict)
```

Uma tarefa só está pronta quando os três passam ([Definition of Done](contribuindo.md#dod)).

Sem o instalador (manual):

```bash
python3 -m venv ~/.juniper/.venv
~/.juniper/.venv/bin/pip install -e "~/.juniper[dev]"
cp ~/.juniper/config.yaml.example ~/.juniper/config.yaml
cp ~/.juniper/.env.example ~/.juniper/.env      # preencha as chaves
```

## O que é alterado na sua máquina {#alterado}

| Onde | O quê |
|---|---|
| `~/.juniper/.venv/` | Ambiente virtual com as dependências. |
| `~/.juniper/.tools/` | `uv` e Python isolado (só se não havia Python utilizável). |
| `~/.juniper/config.yaml`, `.env` | Configuração (o `.env` fica com permissão `600`). |
| `~/.juniper/certs/gcp-key.json` | Chave da conta de serviço do GCP (pasta `700`, arquivo `600`; fora do git). |
| `~/.juniper/.env` (só com MongoDB) | Usuário (`juniper`) e uma senha aleatória do banco, gerados na hora e válidos só nesta máquina. |
| Docker (só com MongoDB) | Um container `juniper-mongo` (imagem `mongo:7`, ~700 MB) e o volume `juniper_juniper-mongo-data`. A porta 27017 só escuta em `127.0.0.1`. |
| `~/.local/bin/juniper` | Atalho para o executável. |
| `~/.zshrc` | Linha que carrega os comandos do shell (`search`, `deploy`...). Troca a linha antiga pela nova. |
| `~/.zshrc`, `~/.bashrc` | Inclui `~/.local/bin` no PATH, se ainda não estiver. |
| `~/.juniper/logs/` | Log da instalação e os [logs de uso](observabilidade.md). |

Antes de alterar um arquivo de shell, o instalador guarda uma cópia ao lado dele (`.zshrc.juniper.bak`). Para desfazer tudo: `bash ~/.juniper/scripts/uninstall.sh`.

## Conta de serviço no GCP {#gcp}

A Juniper usa uma conta de serviço do Google Cloud para falar com a agenda ([Agenda Google](google-calendar.md)); a mesma configuração vale para o servidor ([Integrações Google Cloud e Groq](integracoes-producao.md#calendar)). Passo a passo no console:

1. **Projeto.** Em console.cloud.google.com, abra o seletor de projetos (topo da página) e escolha um projeto ou crie um novo. Confirme que ele está ativo.
2. **Ativar a API.** Menu ☰ → APIs e serviços → Biblioteca, procure **Google Calendar API** e clique em Ativar.
3. **Criar a conta de serviço.** Menu ☰ → IAM e administrador → Contas de serviço → **+ Criar conta de serviço**. Dê um nome (ex.: `juniper`) e clique em Criar e continuar. Não é preciso conceder papel de projeto: o acesso é dado **compartilhando a agenda** com o e-mail da conta. Clique em Concluir.
4. **Gerar a chave JSON.** Na lista, abra a conta criada → aba Chaves → Adicionar chave → Criar nova chave → tipo **JSON** → Criar. O navegador baixa um arquivo como `meu-projeto-a1b2c3.json`; essa é a **única vez** que a chave aparece. Informe o caminho dele ao instalador (o nome pode ser qualquer um).
5. **Conferir.**

```bash
ls -l ~/.juniper/certs/gcp-key.json     # -rw------- (600)
python scripts/check_google.py
```

> **Atenção:** a chave dá acesso à conta de serviço. Nunca a versione no git (`certs/` já está no `.gitignore`), não a envie por chat ou e-mail e apague o arquivo baixado depois de instalar. Se vazar, exclua a chave na aba Chaves e gere outra.

## Opcionais {#opcionais}

- **Ollama** (reserva local, o último da fila): instale em ollama.com e rode `ollama pull llama3.1`. A Juniper usa quando a DeepInfra e a Groq falham.
- **Agenda do Google:** precisa de conta de serviço e agenda própria ([acima](#gcp)); valide com `python scripts/check_google.py`.
- **Memória no MongoDB:** o instalador confere o Docker (com o compose e rodando; se não estiver, diz o que falta e mantém os arquivos), cria o usuário e a senha e grava no `.env`, sobe o banco (`docker compose up -d --wait`; a primeira vez baixa a imagem), testa a conexão com o mesmo código que a Juniper usa e só então grava `memory.session.store: mongo`. Mudou de ideia? `juniper install --memory=mongo` (ou `--memory=file`). Rodar de novo com o MongoDB já escolhido só garante que o banco está de pé e **nunca troca a senha**. As conversas que já estão em arquivos não migram. Detalhes em [Memória de sessão](memoria-sessao.md#mongodb).

## Se algo der errado {#problemas}

| Mensagem ou sintoma | O que fazer |
|---|---|
| "Faltam curl ou wget" | Só acontece sem Python na máquina: instale um deles (`sudo apt install curl`) e rode de novo. |
| "Sem conexão com a internet" | Conecte-se e rode de novo; o que já foi instalado é reaproveitado. |
| `juniper: command not found` | Abra um terminal novo (o PATH foi ajustado agora). Se persistir: `~/.local/bin/juniper version`. |
| Algo estranho no ambiente | `juniper install --clean` recria tudo e é seguro. |
| "Chave inválida: o JSON não é de uma conta de serviço" | Você apontou para outro tipo de credencial. Gere uma chave JSON em Contas de serviço e rode `install.sh --gcp-key=CAMINHO`. |
| "o Docker não está instalado" / "não está rodando" | A memória ficou em arquivos. Instale ou inicie o Docker e rode `juniper install --memory=mongo`. Se a mensagem fala em permissão, o seu usuário precisa estar no grupo `docker`. |
| "Não consegui subir o MongoDB" | Quase sempre é a porta 27017 ocupada por outro MongoDB. Pare o outro e rode de novo; o detalhe está em `~/.juniper/logs/install.log`. |
| "Já existe um banco da Juniper no Docker, mas o .env não tem o usuário e a senha dele" | O instalador não sobrescreve um banco existente. Ponha o usuário e a senha antigos no `.env` ou, se puder perder as conversas, rode `docker compose down -v` em `~/.juniper` e instale de novo. |
| Qualquer outra falha | O detalhe técnico está em `~/.juniper/logs/install.log`. |

## Ler esta documentação {#docs}

```bash
juniper docs
```

Serve este livro de bolso localmente em `http://localhost:47831` (a Juniper não abre o navegador sozinha). Ctrl+C encerra. Se a porta estiver ocupada, usa a próxima livre (47832, 47833...) e mostra o endereço certo no terminal.

## Para saber mais {#para-saber-mais}

- [Primeiros passos](primeiros-passos.md) (Ambiente Local): Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
- [Ambiente Docker](dev-docker.md) (Ambiente Local): Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar logs e banco, e os problemas comuns.
- [CLI](cli.md) (Documentação Técnica): O comando juniper: chat, mensagem única, retomar conversa, comandos de memória, git e utilitários.
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
