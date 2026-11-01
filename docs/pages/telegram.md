---
title: Bot do Telegram
group: Documentação Técnica
section: Clientes
order: 23
description: O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
status: pronta
tags: telegram bot botfather cliente celular token allowed_user_ids lista permissão privacidade long polling python-telegram-bot mensagens chat id
---

> **Nota:** esta página detalha o [ADR-016](../decisions/ADR-016.md). Código: `brain/clients/telegram.py`, comando `juniper telegram`.

## Visão geral {#visao-geral}

O bot do Telegram é um **cliente** da Juniper, como o [CLI](cli.md): leva o que você escreve ao cérebro pelo [protocolo](protocolo.md) e mostra a resposta. Ele **não** é um executor: não age no seu celular. As tools (como o Google Calendar) rodam no cérebro e valem para qualquer cliente.

```text
Seu celular ─► Telegram (nuvem) ◄── long polling ── Bot ──ws://brain:8765──► Cérebro ─► Google Calendar
```

- O bot **busca** as mensagens no Telegram (*long polling*): não abre porta pública nem usa *webhook*.
- Ele fala com o cérebro pela rede interna do Docker; o **celular não precisa estar na rede privada**.
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

O ID é um **número** (não o *username*). Não use bots de terceiros para descobri-lo. Suba o bot com a lista vazia, espere o log mostrar `Telegram: conectado como @... escutando mensagens.` e **só então** mande uma mensagem a ele: ninguém recebe resposta, e o log mostra uma linha como `Telegram: mensagem de usuário não autorizado (id=123456789)`. Mensagens enviadas **antes** de o bot subir são descartadas de propósito (ele não responde a pedidos velhos de horas), então, se a linha não aparecer, mande outra. Copie o número para a lista e reinicie o bot. O **texto** da mensagem nunca vai para o log.

### Rodar {#rodar}

- **Na sua máquina**, ligado ao cérebro de desenvolvimento: [Primeiros passos](primeiros-passos.md#telegram).
- **No servidor**, como serviço opcional do compose de produção: [Clientes em produção](clientes-producao.md#telegram). Nesse modo o contêiner do bot não publica porta, não recebe o `.env` (não tem a senha do MongoDB nem as chaves de LLM) e só lê o token dos clientes.

Com `telegram.require_login: true`, o bot só conversa depois de `/login <client_id> <client_secret>` (a mensagem é apagada na hora; a sessão vale 12 h de inatividade; `/sair` encerra): veja [Usuários e credenciais](usuarios.md#entrada). A lista de IDs continua valendo antes disso.

O comando recusa subir sem o token. Com a lista vazia ele avisa que ninguém está autorizado.

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

Comandos: `/ajuda` (e `/start`) mostra a ajuda; `/novo` começa uma conversa nova; `/vazou [nota]` marca que uma resposta vazou instruções ou texto interno ([Auditoria de vazamentos](vazamentos.md#relato)): o servidor guarda as últimas 10 interações e o bot mostra a confirmação. Como o bot atende uma mensagem por vez, mande-o **depois** que a resposta terminar.

## Limites e proteções {#protecoes}

| Proteção | Como funciona | Opção |
|---|---|---|
| **Limite de mensagens** | Por usuário, numa janela de 60 s. Acima do limite o bot avisa **uma vez** ("Muitas mensagens em pouco tempo") e ignora o resto em silêncio; **nada chega ao cérebro**, então não gera custo de LLM. | `telegram.rate_limit_per_minute` (padrão 20) |
| **Mensagem grande demais** | Acima de 4.096 caracteres: aviso, sem ir ao cérebro. | n/a |
| **Estranho insistindo** | Quem não está na lista nunca recebe resposta. O aviso no log sai no máximo **uma vez a cada 10 minutos por ID**: quem insiste não enche o log. | n/a |
| **Apagar depois de N horas** | Apaga a sua mensagem e as respostas do bot (inclusive avisos e respostas de comandos) depois de N horas. Reduz o que fica no histórico do Telegram, **mas o que o Telegram retém internamente depois de apagar não é verificável**. Desligado por padrão: ligar também some com o histórico que você vê no chat. | `telegram.delete_after_hours` (0 a 48; padrão 0) |

Detalhes do apagar: só vale para mensagens com menos de 48 h (por isso o prazo é limitado a 47,5 h); o agendamento fica **em memória** e se perde se o bot reiniciar; falhas ao apagar (mensagem já apagada, Telegram fora) são ignoradas.

## Uma conversa, várias janelas {#janelas}

No servidor, uma conversa fica ligada a **uma conexão por vez** ([Protocolo](protocolo.md#sessoes)). Se você abrir o terminal com `juniper --resume` (ou o desktop) enquanto conversa pelo Telegram, a janela nova **assume** a conversa. O bot percebe: se estava no meio de uma resposta, avisa que "outra janela assumiu" e o resto aparece lá; na mensagem seguinte abre uma **conversa nova**, sem roubar a conversa de volta.

Com **mais de um ID** na lista, todos compartilham a conversa mais recente e leem o histórico uns dos outros (o servidor é de um usuário só). O comando avisa em voz alta. Use um único ID.

## O que o bot não faz {#limites}

- **Só conversa.** Memória (listar, buscar, exportar, apagar), perfil, `juniper tools` e o diário de mensagens **não** passam pelo Telegram. Assuntos sensíveis ficam no CLI e no desktop.
- **Nunca grava o texto** das mensagens em log nem em evento; só metadados (ID, tamanho, duração, resultado).
- **Privacidade:** as mensagens passam pelos servidores do Telegram e não têm criptografia ponta a ponta (os chats secretos não existem para bots). O que a Juniper guarda no seu servidor continua só seu.

## Logs {#logs}

`docker compose logs telegram` mostra o essencial, **sem nunca imprimir o texto das mensagens nem o token**: a linha de início (`conectado como @... escutando mensagens`), os IDs não autorizados e os erros da biblioteca (só o tipo). Só o logger do bot é ligado; o `httpx` não, porque em INFO ele imprime a URL das requisições e ela leva o token.

## Eventos {#eventos}

Categoria `telegram` no [eventlog](observabilidade.md): `start`, `stop`, `message_in` (ID, tamanho), `unauthorized` (ID), `turn` (duração, `finish`), `ignored`, `error` (só o tipo).

## Saúde {#saude}

O bot escreve um sinal de vida a cada 15 s (`/tmp/telegram-health`). `juniper telegram --health` sai com 0 se o sinal é recente, e é o que o *healthcheck* do contêiner usa.

## Para saber mais {#para-saber-mais}

- [Primeiros passos](primeiros-passos.md) (Ambiente Local): Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
- [Onde conversar](produto-canais.md) (Produto): Os canais da Juniper (terminal, Telegram, app Android com texto e voz, painel web) e o que cada um faz de melhor.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [Voz por áudio](voz-audio.md) (Documentação Técnica): Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do protocolo.
- [Clientes em produção](clientes-producao.md) (Produção): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o dashboard.
