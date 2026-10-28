---
title: Clientes em produção
group: Produção
order: 4
description: Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o dashboard.
status: guia
tags: produção clientes token cli remoto terminal telegram bot botfather compose profile app android apk instalar keystore dashboard navegador rede privada
---

Todo cliente precisa de duas coisas: **alcançar o servidor pela rede privada** e o **token dos clientes**. O token nasce no primeiro boot do cérebro; para lê-lo, na VPS:

```bash
cd ~/juniper && docker compose -f docker-compose.prod.yml exec -T brain cat /home/juniper/.juniper/state/token
```

Trate-o como uma senha. Se ele vazar, apague o arquivo (`docker compose -f docker-compose.prod.yml exec brain rm /home/juniper/.juniper/state/token`) e reinicie o cérebro: um token novo é gerado e todos os clientes precisam dele.

Cada dispositivo (computador, celular, navegador) precisa estar na rede privada: no Tailscale, instale o app e entre na mesma conta (com a etiqueta `tag:juniper-client`, se a política exigir; veja o [passo 5.2](deploy-vps.html#rede)).

## Terminal {#cli}

No seu computador, com a Juniper instalada ([Instalação sem Docker](install.html) ou `pip install -e .` num clone):

```bash
mkdir -p ~/.juniper && umask 077
printf '%s' '<token>' > ~/.juniper/token
```

No `~/.juniper/config.yaml`:

```yaml
deployment:
  mode: server
  server:
    host: juniper-brain      # o nome ou o IP do servidor na rede privada
    port: 8765
  auth:
    token_file: ~/.juniper/token
```

Teste: `juniper "oi"`, `juniper` (conversa), `juniper --resume`, `juniper tools`, `juniper memory list`. Se o endereço for público (fora de uma rede privada), o CLI avisa antes de conectar, porque `ws://` não cifra. Detalhes: [CLI](cli.html#server).

## Telegram {#telegram}

O bot é um serviço **opcional** do compose de produção (perfil `telegram`). Ele roda no próprio servidor, fala com o cérebro pela rede interna do Docker e **busca** as mensagens no Telegram, então o celular não precisa estar na rede privada ([ADR-016](adr-016.html)).

1. **Crie o bot:** no Telegram, `@BotFather` (o oficial tem o selo azul) → `/newbot`. Guarde o token. Desligue grupos: `/mybots` → o bot → *Bot Settings* → *Allow Groups?* → *Turn groups off*. Ative a verificação em duas etapas na sua conta do Telegram.
2. **Na VPS**, em `~/juniper`:

   ```bash
   read -rsp "Token do bot: " T; echo
   echo "JUNIPER_TELEGRAM_BOT_TOKEN=$T" >> .env && unset T
   echo "COMPOSE_PROFILES=telegram" >> .env
   docker compose -f docker-compose.prod.yml up -d
   docker compose -f docker-compose.prod.yml ps          # o telegram fica "healthy" em ~1 min
   ```

3. **Descubra o seu ID numérico:** espere `escutando mensagens` no log, mande uma mensagem ao bot e leia:

   ```bash
   docker compose -f docker-compose.prod.yml logs --tail=20 telegram
   #   ...Telegram: mensagem de usuário não autorizado (id=123456789)
   ```

4. **Autorize** e recrie o bot:

   ```bash
   echo "JUNIPER_TELEGRAM__ALLOWED_USER_IDS=[123456789]" >> .env
   docker compose -f docker-compose.prod.yml up -d telegram
   ```

Mande "oi" ao bot. Para desligar: tire `COMPOSE_PROFILES=telegram` do `.env` e rode `docker compose -f docker-compose.prod.yml up -d --remove-orphans`.

| Sintoma | Causa e solução |
|---|---|
| O contêiner reinicia sem parar | Falta o token, ou o Telegram o recusou (o log diz qual; o token nunca aparece no log). |
| A mensagem não aparece no log | Foi enviada antes de o bot subir (as pendentes são descartadas): mande de novo. `Conflict` no log = outro processo usa o mesmo token. |
| O bot não responde | O seu ID não está na lista, ou você escreveu num grupo. |
| "Não consegui falar com o servidor" | O cérebro não está saudável: `ps` e `logs brain`. |

Opções (`telegram.rate_limit_per_minute`, `telegram.delete_after_hours`) em [Configuração](configuracao.html#telegram-config); comportamento em [Bot do Telegram](telegram.html).

## App Android {#app}

1. **Gere o APK** no seu computador (nunca na VPS), assinado com uma chave sua: [Ambiente mobile → Gerar um APK](mobile-dev.html#apk). Guarde a chave e as senhas: sem elas não dá para atualizar o app por cima.
2. **Leve ao celular** pelo meio mais fácil: o compartilhamento de arquivos da sua VPN (no Tailscale, o Taildrop), um cabo, e-mail ou um serviço de arquivos privado. O arquivo é `mobile/android/app/build/outputs/apk/release/app-release.apk`.
3. **Instale:** abra o arquivo no celular e permita "instalar apps desta origem" quando o Android pedir (a permissão é do app que abriu o arquivo; dá para desligá-la depois).
4. **Ligue a VPN no celular** e abra o app. Na configuração, informe:
   - **Endereço:** o nome ou o IP do servidor na rede privada, sem `http://` e sem porta;
   - **Porta:** `8765`;
   - **Token:** o token dos clientes.

   O token fica no Android Keystore. A linha do topo deve mostrar **Conectada**.
5. **Permissões:** microfone e notificações (na primeira conversa por voz) e, para a música abrir com o app em segundo plano, **«Exibir sobre outros apps»** (a aba Voz mostra um botão «Permitir agora»).
6. **Teste** pelo [roteiro de aceite](mobile.html#aceite): texto, «toca Oceano do Djavan» (exige a [música](integracoes-producao.html#musica) ligada e o YouTube Music instalado), voz (exige a [voz](integracoes-producao.html#voz) ligada).

| Sintoma | Causa e solução |
|---|---|
| «o servidor recusou o token» | Token errado ou trocado: copie de novo. |
| «sem conexão» | VPN desligada no celular, endereço errado ou a política da VPN não inclui o aparelho. |
| Música: «o dispositivo não está conectado» | O app não está aberto e conectado (ele é o executor que abre a música). |
| A música só abre com o app na frente | Falta «Exibir sobre outros apps». |
| Quero chamar sem tocar na tela | Palavra de chamada: AccessKey da Picovoice, palavra «Juniper» treinada (Android) salva em `mobile/android/app/src/main/assets/juniper.ppn`, APK novo e a chave em Configurar ([Voz por áudio](voz-audio.html#wake)). |
| Pedi um álbum e tocou só uma faixa | Peça «toca o álbum X»; playlists suas tocam pelo nome. |
| Ela ouve a própria voz e se responde | Sem fone, use o botão «Segurar para falar», ou fone. |

## Dashboard {#dashboard}

1. Libere a porta **8766** na política da VPN para os seus dispositivos (a política do [passo 5.2](deploy-vps.html#rede) já inclui).
2. No navegador de um dispositivo na rede privada: `http://<endereço-do-servidor>:8766/`.
3. Entre com o token dos clientes. O login vale 12 horas (`dashboard.session_hours`) e o token nunca vai na URL.

Para HTTPS (por exemplo, para "instalar" o painel no celular), ponha na frente um proxy que termine TLS dentro da rede privada (no Tailscale, `tailscale serve --bg http://<IP-PRIVADO>:8766`) e inclua o nome usado em `dashboard.allowed_hosts` se ele não for `*.ts.net`. Detalhes e problemas: [Dashboard](dashboard.html#acesso).
