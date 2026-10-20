---
title: Bot do Telegram
group: Clientes
order: 3
description: O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
status: proposto
tags: telegram bot botfather cliente celular token allowed_user_ids lista permissão privacidade long polling python-telegram-bot mensagens chat id
---

> **Nota:** esta página resume o [ADR-016](adr-016.html). O bot (`brain/clients/telegram.py`, comando `juniper telegram`), o serviço no compose de produção e o endurecimento (limite de mensagens, apagar depois de N horas) estão implementados.

## Visão geral {#visao-geral}

O bot do Telegram é um **cliente** da Juniper, como o [CLI](cli.html): leva o que você escreve ao cérebro pelo [protocolo](protocolo.html) e mostra a resposta. Ele **não** é um executor: não age no seu celular. As tools (como o Google Calendar) rodam no cérebro e valem para qualquer cliente.

```text
Seu celular ─► Telegram (nuvem) ◄── long polling ── Bot ──ws://brain:8765──► Cérebro ─► Google Calendar
```

- O bot **busca** as mensagens no Telegram (*long polling*): não abre porta pública nem usa *webhook*.
- Ele fala com o cérebro pela rede interna do Docker; o **celular não precisa do Tailscale**.
- Um cliente fino: sem lógica de agente e sem acesso ao MongoDB.

## Criar o bot {#criar}

1. No Telegram, abra o `@BotFather` (o oficial tem o selo azul) e envie `/newbot`. Escolha um nome e um *username* terminado em `bot`.
2. O BotFather entrega o **token**. Ele é a senha do bot: guarde no gerenciador de senhas e **nunca** o coloque em código, PR, log ou conversa. Se vazar, `/revoke` no BotFather.
3. Desligue a entrada em grupos: `/mybots`, o bot, **Bot Settings**, **Allow Groups?**, **Turn groups off**.
4. Proteja a sua conta do Telegram com a verificação em duas etapas: quem controla a conta controla o BotFather.

## Configurar {#configurar}

| O que | Onde | Exemplo |
|---|---|---|
| Token do bot | `.env`, `JUNIPER_TELEGRAM_BOT_TOKEN` | `123456:ABC...` |
| Quem pode falar com o bot | `telegram.allowed_user_ids` ou `JUNIPER_TELEGRAM__ALLOWED_USER_IDS` | `[123456789]` |

### Descobrir o seu ID {#descobrir-id}

O ID é um **número** (não o *username*). Não use bots de terceiros para descobri-lo. Suba o bot com a lista vazia e mande uma mensagem a ele: ninguém recebe resposta, e o log mostra uma linha como `Telegram: mensagem de usuário não autorizado (id=123456789)`. Copie o número para a lista e reinicie o bot. O **texto** da mensagem nunca vai para o log.

### Rodar na sua máquina (desenvolvimento) {#rodar-dev}

Com o brain de pé (por exemplo, o [ambiente de desenvolvimento](dev-docker.html)), em outro terminal:

```bash
export JUNIPER_DEPLOYMENT__MODE=server
export JUNIPER_DEPLOYMENT__AUTH__TOKEN_FILE="$PWD/.juniper-dev/state/token"
export JUNIPER_TELEGRAM_BOT_TOKEN='<o token>'
export JUNIPER_TELEGRAM__ALLOWED_USER_IDS='[123456789]'
juniper telegram
```

O comando recusa subir sem o token. Com a lista vazia ele avisa que ninguém está autorizado.

### Rodar na VPS {#vps}

O compose de produção tem o serviço `telegram` como **opt-in** (perfil `telegram`): quem não usa o bot não muda nada. Ele roda a mesma imagem do brain, fala com o `brain` pela rede do compose, **não publica porta**, **não recebe o `.env`** (então não tem a senha do Mongo nem as chaves de LLM) e só **lê** o token dos clientes.

Na VPS, dentro de `~/juniper`:

```bash
# 1. o token do bot, sem aparecer na tela nem no histórico do shell
read -rsp "Token do bot: " T; echo
echo "JUNIPER_TELEGRAM_BOT_TOKEN=$T" >> .env && unset T
echo "COMPOSE_PROFILES=telegram" >> .env        # liga o serviço (ou use --profile telegram)

# 2. sobe (a lista de permissão ainda vazia: o bot não responde a ninguém)
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps        # o telegram fica "healthy" em ~1 min

# 3. mande uma mensagem ao bot no Telegram e leia o seu ID no log
docker compose -f docker-compose.prod.yml logs --tail=20 telegram
#    ...Telegram: mensagem de usuário não autorizado (id=123456789)

# 4. autorize o seu ID e reinicie o bot
echo "JUNIPER_TELEGRAM__ALLOWED_USER_IDS=[123456789]" >> .env
docker compose -f docker-compose.prod.yml up -d telegram
```

Agora mande "oi" ao bot. Para desligar: tire `COMPOSE_PROFILES=telegram` do `.env` e rode `docker compose -f docker-compose.prod.yml up -d --remove-orphans`, ou `stop telegram`.

| Sintoma | Causa e solução |
|---|---|
| O contêiner reinicia sem parar | Falta `JUNIPER_TELEGRAM_BOT_TOKEN`, ou o Telegram **recusou o token** (o log diz qual dos dois; o token nunca aparece no log). Corrija o `.env` e rode `up -d telegram`. |
| O bot não responde a você | O seu ID não está na lista (o log mostra `não autorizado (id=...)`), ou você escreveu num grupo. |
| "Não consegui falar com o servidor" | O brain não está saudável: veja `ps` e `logs brain`. |
| `unhealthy` | O laço do bot travou; `logs telegram` mostra o motivo. O Docker reinicia o contêiner. |
| Mensagem velha sem resposta | Depois de uma pausa o bot descarta pedidos pendentes de horas atrás, de propósito. |

## Quem o bot atende {#autorizacao}

- **Só quem está na lista de permissão.** Os outros não recebem resposta nenhuma; o ID vai para o log. Lista vazia significa **ninguém**.
- **Só chats privados.** Grupos, canais e outros bots são ignorados.
- **O username do bot não protege nada:** qualquer pessoa pode achá-lo. A proteção é a lista.

## Como a conversa funciona {#conversa}

| Tema | Comportamento |
|---|---|
| **Sessão** | Uma conversa contínua: ao conectar, o bot retoma a mais recente. `/novo` abre outra. |
| **Um turno por vez** | Se você escreve de novo durante uma resposta, o bot diz "Ainda estou respondendo". Não há fila. |
| **Resposta** | O Telegram não mostra texto chegando aos poucos: o bot mostra "digitando…" e manda a resposta inteira no fim, em partes de até 4.096 caracteres. Texto puro, sem formatação. |
| **Confirmação** | Como no CLI: a Juniper pergunta em palavras ("Posso criar o evento?") e o "sim" é a próxima mensagem. Sem botões. |
| **Links** | As respostas saem **sem pré-visualização de link**: o Telegram não busca sozinho as URLs do texto (uma URL maliciosa vinda de um convite de calendário não vira um canal de vazamento sem clique). |
| **Erros** | O servidor manda uma mensagem amigável em PT-BR; o detalhe técnico fica de fora. Se o servidor cai, o bot avisa e reconecta sozinho. |
| **Queda no meio da resposta** | O turno termina no servidor; o bot recupera a resposta pelo histórico quando a conexão volta. |
| **Mensagens encaminhadas, áudio, foto, arquivo** | Recebem um aviso ("só entendo texto") e não chegam ao cérebro. |

Comandos: `/ajuda` (e `/start`) mostra a ajuda; `/novo` começa uma conversa nova.

## Limites e proteções {#protecoes}

| Proteção | Como funciona | Opção |
|---|---|---|
| **Limite de mensagens** | Por usuário, numa janela de 60 s. Acima do limite o bot avisa **uma vez** ("Muitas mensagens em pouco tempo") e ignora o resto em silêncio; **nada chega ao cérebro**, então não gera custo de LLM. | `telegram.rate_limit_per_minute` (padrão 20) |
| **Mensagem grande demais** | Acima de 4.096 caracteres: aviso, sem ir ao cérebro. | n/a |
| **Estranho insistindo** | Quem não está na lista nunca recebe resposta. O aviso no log sai no máximo **uma vez a cada 10 minutos por ID**: quem insiste não enche o log. | n/a |
| **Apagar depois de N horas** | Apaga a sua mensagem e as respostas do bot (inclusive avisos e respostas de comandos) depois de N horas. Reduz o que fica no histórico do Telegram, **mas o que o Telegram retém internamente depois de apagar não é verificável**. Desligado por padrão: ligar também some com o histórico que você vê no chat. | `telegram.delete_after_hours` (0 a 48; padrão 0) |

Detalhes do apagar: só vale para mensagens com menos de 48 h (por isso o prazo é limitado a 47,5 h); o agendamento fica **em memória** e se perde se o bot reiniciar; falhas ao apagar (mensagem já apagada, Telegram fora) são ignoradas.

## Uma conversa, várias janelas {#janelas}

No servidor, uma conversa fica ligada a **uma conexão por vez** ([Protocolo](protocolo.html#sessoes)). Se você abrir o terminal com `juniper --resume` (ou o desktop) enquanto conversa pelo Telegram, a janela nova **assume** a conversa. O bot percebe: se estava no meio de uma resposta, avisa que "outra janela assumiu" e o resto aparece lá; na mensagem seguinte abre uma **conversa nova**, sem roubar a conversa de volta.

Com **mais de um ID** na lista, todos compartilham a conversa mais recente e leem o histórico uns dos outros (o servidor é de um usuário só). O comando avisa em voz alta. Use um único ID.

## O que o bot não faz {#limites}

- **Só conversa.** Memória (listar, buscar, exportar, apagar), perfil, `juniper tools` e o diário de mensagens **não** passam pelo Telegram. Assuntos sensíveis ficam no CLI e no desktop.
- **Nunca grava o texto** das mensagens em log nem em evento; só metadados (ID, tamanho, duração, resultado).
- **Privacidade:** as mensagens passam pelos servidores do Telegram e não têm criptografia ponta a ponta (os chats secretos não existem para bots). O que a Juniper guarda no seu servidor continua só seu.

## Eventos {#eventos}

Categoria `telegram` no [eventlog](observabilidade.html): `start`, `stop`, `message_in` (ID, tamanho), `unauthorized` (ID), `turn` (duração, `finish`), `ignored`, `error` (só o tipo).

## Saúde {#saude}

O bot escreve um sinal de vida a cada 15 s (`/tmp/telegram-health`). `juniper telegram --health` sai com 0 se o sinal é recente, e é o que o *healthcheck* do contêiner usa.

## Tasks {#tasks}

T-093 (ADR-016), T-094 (Google Calendar na VPS), T-095 (o bot), T-096 (serviço no compose e segredo), T-097 (limite de mensagens e apagar depois de N horas).
