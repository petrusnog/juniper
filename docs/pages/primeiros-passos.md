---
title: Primeiros passos
group: Ambiente Local
order: 2
description: Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
status: guia
tags: tutorial primeiros passos setup ambiente desenvolvimento docker compose cli telegram android emulador celular metro fast refresh watchfiles token dev_setup recarga
---

Este tutorial monta o ambiente completo de desenvolvimento numa máquina Linux, macOS ou Windows com WSL 2. No final você terá:

- o **cérebro** rodando em contêineres, com o código da sua máquina valendo na hora;
- a Juniper respondendo no **terminal**, num **bot do Telegram** e no **app Android** (emulador ou celular), os três conectados ao mesmo cérebro local;
- uma alteração de código aparecendo nos três clientes **sem reinstalar nada**.

A topologia é a mesma da produção: o cérebro e o MongoDB em contêineres e os clientes de fora, falando por WebSocket ([Arquitetura](architecture.md)). Leve cerca de uma hora na primeira vez (a maior parte é download).

## 0. Pré-requisitos {#pre-requisitos}

| Ferramenta | Para quê | Como conferir |
|---|---|---|
| `git` | Clonar o repositório. | `git --version` |
| **Docker** com o plugin **Compose v2** | Rodar o cérebro, o MongoDB e o LLM de teste. No Linux, o seu usuário no grupo `docker`. | `docker compose version` |
| **Python 3.11+** | O comando `juniper` (cliente) e os testes, na sua máquina. | `python3 --version` |
| **Node 22+**, **JDK 17** e **Android SDK** | Compilar e rodar o app. A instalação do SDK está em [Ambiente mobile](mobile-dev.md#sdk). | `node -v`, `java -version`, `adb version` |
| Um **emulador Android** ou um **celular** com depuração USB | Rodar o app. | `adb devices` |
| Uma conta no **Telegram** | Criar o bot de teste. | — |
| Cerca de 3 GB livres em disco (mais ~10 GB para o SDK do Android) | Imagens, o MongoDB e o modelo de embeddings. | `df -h` |

Uma chave de modelo de linguagem é **opcional**: sem ela o ambiente usa o `fake-llm`, um LLM de mentira que só ecoa a sua mensagem, suficiente para provar que tudo está conectado. Para ver a Juniper conversar e usar ferramentas, crie uma chave gratuita da Groq em `console.groq.com/keys` (ou uma da DeepInfra).

## 1. Clone e prepare a pasta {#clonar}

```bash
git clone https://github.com/<owner>/juniper.git
cd juniper
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # o comando `juniper` e as ferramentas de teste
./scripts/dev_setup.sh           # cria .juniper-dev/, certs/, .env e config.yaml
```

O `scripts/dev_setup.sh` é seguro de repetir: cria `.juniper-dev/state` e `.juniper-dev/logs`, copia `.env.example` para `.env` e `config.yaml.example` para `config.yaml` (se ainda não existem) e confere o que falta.

Abra o `.env` e preencha **o usuário e a senha do MongoDB** (escolha quaisquer valores; o banco é criado com eles) e, se tiver, a chave do LLM:

```bash
JUNIPER_MEMORY__MONGO__USERNAME=juniper
JUNIPER_MEMORY__MONGO__PASSWORD=uma-senha-qualquer
JUNIPER_GROQ_API_KEY=gsk_...          # opcional
```

O `.env` nunca é versionado. Se o `dev_setup.sh` avisar que o seu uid não é 1000 (`id -u`), rode `export UID` antes do próximo passo, para os arquivos de `.juniper-dev/` ficarem seus.

## 2. Suba o cérebro {#subir}

```bash
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml ps
```

Na primeira vez o `--build` baixa as imagens e instala as dependências (alguns minutos). Espere `juniper-dev-brain` e `juniper-dev-mongo` aparecerem como **healthy**. Depois confira:

```bash
curl -s http://127.0.0.1:8765/health                 # {"status": "ok", "memory": "ready", ...}
ls .juniper-dev/state                                # o arquivo `token`
python scripts/smoke_server.py --token-file .juniper-dev/state/token "oi, tudo bem?"
```

- Enquanto o modelo de embeddings baixa (só na primeira vez), o `/health` mostra `"status": "degraded", "memory": "warming_up"`. É normal: a conversa já funciona.
- O arquivo `.juniper-dev/state/token` é o **token dos clientes**: todo cliente precisa dele para conectar. Os três passos seguintes usam este arquivo.
- O `smoke_server.py` conecta, conversa e imprime a resposta chegando em pedaços.

Os serviços, as portas e o que fazer quando algo não sobe estão em [Ambiente Docker](dev-docker.md).

## 3. Converse pelo terminal {#terminal}

O CLI vira cliente do cérebro remoto quando está em `deployment.mode: server`. Para não mexer no seu `config.yaml`, defina isso só no terminal, a partir da raiz do repositório:

```bash
export JUNIPER_DEPLOYMENT__MODE=server
export JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE="$PWD/.juniper-dev/state/token"

juniper "oi"          # uma mensagem
juniper               # conversa interativa (/sair encerra)
juniper tools         # as ferramentas que o cérebro oferece
```

✅ **Marco 1:** a Juniper respondeu no terminal. Com o `fake-llm`, a resposta é `Eco (1): oi`; com uma chave real, é a Juniper de verdade. Deixe este terminal aberto numa conversa interativa: ele será usado no passo 6.

## 4. Converse pelo Telegram {#telegram}

O bot do Telegram é outro cliente do mesmo cérebro ([Bot do Telegram](telegram.md)). Ele **busca** as mensagens no Telegram (*long polling*), então funciona na sua máquina sem abrir nenhuma porta.

**4.1 Crie um bot de teste.** No Telegram, abra o `@BotFather` (o oficial tem o selo azul), envie `/newbot`, escolha um nome e um *username* terminado em `bot`. Guarde o **token** que ele devolve: é a senha do bot e nunca vai para código, log nem PR.

**4.2 Suba o bot**, num terminal novo, na raiz do repositório e com o ambiente virtual ativo:

```bash
source .venv/bin/activate
export JUNIPER_DEPLOYMENT__MODE=server
export JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE="$PWD/.juniper-dev/state/token"
read -rsp "Token do bot: " JUNIPER_TELEGRAM_BOT_TOKEN; echo
export JUNIPER_TELEGRAM_BOT_TOKEN
juniper telegram
```

Espere a linha `Telegram: conectado como @... escutando mensagens.`

**4.3 Autorize o seu usuário.** O bot só responde a IDs numéricos de uma lista de permissão, e a lista começa vazia (ninguém). Mande "oi" ao bot: ele não responde, e o terminal mostra `Telegram: mensagem de usuário não autorizado (id=123456789)`. Pare o bot (Ctrl+C), autorize o número e suba de novo:

```bash
export JUNIPER_TELEGRAM__ALLOWED_USER_IDS='[123456789]'
juniper telegram
```

✅ **Marco 2:** mande "oi" ao bot e veja a resposta no Telegram. Deixe o bot rodando.

## 5. Converse pelo Android {#android}

O app Android é um cliente fino em React Native ([App mobile](mobile.md)). Em desenvolvimento ele roda em modo *debug*, ligado ao **Metro** (o servidor de pacotes do React Native), que recarrega o código do app na hora.

**5.1 Prepare o Android.** Se ainda não tem o SDK, um emulador ou o celular com depuração USB, siga [Ambiente mobile](mobile-dev.md#sdk). Confira que o aparelho aparece:

```bash
adb devices          # uma linha "emulator-5554 device" ou o número do seu celular
```

**5.2 Rode o app**, num terminal novo:

```bash
cd mobile
npm install
npx react-native run-android      # compila o app de debug, instala e abre o Metro
```

O primeiro build baixa dependências do Gradle e leva vários minutos. O Metro fica aberto nesse terminal; deixe-o rodando.

**5.3 Aponte o app para o cérebro local.** A tela de configuração pede endereço, porta e token. O cérebro de desenvolvimento só escuta em `127.0.0.1` da sua máquina, então o endereço depende de onde o app roda:

| Onde o app roda | Endereço | Porta | Preparação |
|---|---|---|---|
| Emulador | `10.0.2.2` (o "localhost" do computador visto de dentro do emulador) | `8765` | nenhuma |
| Celular pelo cabo USB | `127.0.0.1` | `8765` | `adb reverse tcp:8765 tcp:8765` (repita sempre que reconectar o cabo) |

Para o **token**, copie o conteúdo de `.juniper-dev/state/token`. Num emulador, o jeito mais simples é tocar no campo do token e mandar o texto pelo `adb`:

```bash
adb shell input text "$(cat .juniper-dev/state/token)"
```

Salve. A linha de estado no topo deve mostrar **Conectada**.

✅ **Marco 3:** na aba **Texto**, mande "oi" e veja a resposta chegar aos poucos. Na aba **Voz** dá para testar a conversa falada se o cérebro tiver a voz ligada ([Voz por áudio](voz-audio.md#ligar)); sem ela, o cérebro recusa a sessão de voz com o erro `voice_unavailable`.

> **Nota:** no ambiente local o tráfego é `ws://` sem cifra, o que é aceitável porque não sai da sua máquina (o emulador e o `adb reverse` usam a interface local). Em produção os clientes chegam por uma rede privada cifrada ([Visão geral da produção](producao.md)).

## 6. Edite o código e veja ao vivo {#ao-vivo}

Agora os três clientes estão conectados ao mesmo cérebro. Vamos mudar o cérebro e ver o efeito em todos, sem reiniciar nada à mão.

**6.1 Acompanhe o cérebro**, num terminal à parte:

```bash
docker compose -f docker-compose.dev.yml logs -f brain
```

**6.2 Crie uma ferramenta nova.** Uma tool é só um arquivo em `brain/tools/` ([Tools](tools.md)). Crie `brain/tools/dev_hello.py`:

```python
from __future__ import annotations

from typing import Any

from brain.tools.base import Locality, Tier, Tool, ToolResult
from brain.tools.registry import register_tool


@register_tool
class DevHello(Tool):
    name = "dev_hello"
    description = "Cumprimenta a partir do ambiente de desenvolvimento. Use quando pedirem para testar o ambiente."
    parameters = {"type": "object", "properties": {}}
    tier = Tier.READ
    locality = Locality.CLOUD

    async def run(self, **kwargs: Any) -> ToolResult:
        return ToolResult(success=True, output="Olá do ambiente de desenvolvimento: o código novo já está no ar.")
```

Ao salvar, o log mostra o servidor reiniciando (o contêiner usa o `watchfiles`, que vigia `brain/` e `prompts/`). O servidor fecha as conexões de forma limpa e **os três clientes reconectam sozinhos**, retomando a conversa em que estavam.

**6.3 Confira nos clientes:**

- **Terminal:** `juniper tools` lista `dev_hello` (funciona mesmo com o `fake-llm`).
- **Com uma chave de LLM real**, peça "testa o ambiente de desenvolvimento" no terminal, no Telegram e no app: a Juniper chama `dev_hello` e responde com a frase nova nos três. Por ser `READ`, ela executa sem pedir confirmação ([Segurança](seguranca.md#tiers)).

**6.4 Mude o app.** Com o Metro rodando, edite um texto de `mobile/src/ui/ChatScreen.tsx` (por exemplo, o rótulo do botão «Enviar») e salve: o *Fast Refresh* atualiza a tela do app em um ou dois segundos, sem reinstalar. Mudanças em código Kotlin (`mobile/android/`) exigem rodar `npx react-native run-android` de novo.

✅ **Marco 4:** você mudou o cérebro e o app e viu o resultado sem reinstalar nada. Apague `brain/tools/dev_hello.py` antes de abrir um PR (era só um exemplo).

### O que recarrega sozinho {#recarga}

| Você mudou | O que acontece |
|---|---|
| `brain/` ou `prompts/` | O cérebro reinicia sozinho e os clientes reconectam. |
| `mobile/src/` (TypeScript) | O Metro aplica na hora (*Fast Refresh*). |
| `mobile/android/` (Kotlin, manifesto) | Rode `npx react-native run-android` de novo. |
| O código do bot (`brain/clients/telegram.py`) | O bot roda fora do contêiner: pare com Ctrl+C e rode `juniper telegram` de novo. |
| `.env` ou `docker/config.dev.yaml` | `docker compose -f docker-compose.dev.yml restart brain` |
| `requirements.txt` ou `pyproject.toml` | `docker compose -f docker-compose.dev.yml up -d --build` (e `pip install -e ".[dev]"` na sua máquina) |

## 7. Rode os testes {#testes}

Os testes rodam na sua máquina, sem Docker e sem rede:

```bash
pytest -m "not integration"            # o que o CI roda
ruff check brain/ tests/ && mypy brain/
cd mobile && npm test && npm run typecheck && npx eslint .
```

As regras do que testar e de como abrir o PR estão em [Como contribuir](contribuindo.md).

## 8. Parar e limpar {#limpar}

```bash
docker compose -f docker-compose.dev.yml down        # desliga; os dados ficam nos volumes
docker compose -f docker-compose.dev.yml down -v     # desliga e APAGA o banco e o modelo baixado
rm -rf .juniper-dev                                  # zera o token e os logs (um novo token nasce no próximo up)
```

Depois de apagar o token, todos os clientes precisam do novo (no app, abra a configuração e cole o token novo).

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| "Não achei o token do servidor em ~/.juniper/token" | O CLI está em modo `server` sem a variável do token. Exporte `JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE` ([passo 3](#terminal)). |
| A resposta é só o eco da mensagem | É o `fake-llm`: nenhuma chave válida no `.env`. Preencha e rode `restart brain`. |
| O bot não responde | O seu ID não está em `JUNIPER_TELEGRAM__ALLOWED_USER_IDS`, ou você escreveu num grupo (o bot só atende chats privados). |
| App: «sem conexão» no emulador | Use `10.0.2.2`, não `localhost`; confira `curl http://127.0.0.1:8765/health` no computador. |
| App: «sem conexão» no celular | Faltou `adb reverse tcp:8765 tcp:8765` (ou o cabo foi reconectado). |
| App: «o servidor recusou o token» | Copie o token de novo; ele muda se `.juniper-dev/` for apagada. |
| O app abre uma tela vermelha do Metro | O Metro não está rodando ou o aparelho não o alcança: `npx react-native start` e, no celular, `adb reverse tcp:8081 tcp:8081`. |
| Outros problemas do Docker | Veja [Ambiente Docker](dev-docker.md#problemas). |

## Para saber mais {#para-saber-mais}

- [Introdução](introducao.md) (Introdução): O que é a Juniper, o que ela sabe fazer hoje, como é construída e quais trilhas seguir para aprender mais.
- [Como contribuir](contribuindo.md) (Ambiente Local): O fluxo de trabalho (issue, branch, PR), as regras de arquitetura, os testes exigidos, as convenções de código e como manter a documentação em dia.
- [Ambiente Docker](dev-docker.md) (Ambiente Local): Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar logs e banco, e os problemas comuns.
- [Ambiente mobile](mobile-dev.md) (Ambiente Local): Como preparar o Android SDK, rodar o app num emulador ou celular com recarga ao vivo, testar, regenerar os tipos do protocolo e gerar um APK.
- [Instalação sem Docker (modo local)](install.md) (Ambiente Local): O instalador da Juniper: o cérebro dentro do próprio terminal (modo local), sem contêineres, a partir de uma máquina limpa.
- [CLI](cli.md) (Documentação Técnica): O comando juniper: chat, mensagem única, retomar conversa, comandos de memória, git e utilitários.
- [Bot do Telegram](telegram.md) (Documentação Técnica): O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
