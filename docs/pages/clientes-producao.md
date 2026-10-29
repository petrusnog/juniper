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

Cada dispositivo (computador, celular, navegador) precisa estar na rede privada: no Tailscale, instale o app e entre na mesma conta (com a etiqueta `tag:juniper-client`, se a política exigir; veja o [passo 5.2](deploy-vps.md#rede)).

## Terminal {#cli}

No seu computador, com a Juniper instalada ([Instalação sem Docker](install.md) ou `pip install -e .` num clone):

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

Teste: `juniper "oi"`, `juniper` (conversa), `juniper --resume`, `juniper tools`, `juniper memory list`. Se o endereço for público (fora de uma rede privada), o CLI avisa antes de conectar, porque `ws://` não cifra. Detalhes: [CLI](cli.md#server).

## Telegram {#telegram}

O bot é um serviço **opcional** do compose de produção (perfil `telegram`). Ele roda no próprio servidor, fala com o cérebro pela rede interna do Docker e **busca** as mensagens no Telegram, então o celular não precisa estar na rede privada ([ADR-016](../decisions/ADR-016.md)).

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

Opções (`telegram.rate_limit_per_minute`, `telegram.delete_after_hours`) em [Configuração](configuracao.md#telegram-config); comportamento em [Bot do Telegram](telegram.md).

## App Android {#app}

1. **Gere o APK** no seu computador (nunca na VPS), assinado com uma chave sua: [Ambiente mobile → Gerar um APK](mobile-dev.md#apk). Guarde a chave e as senhas: sem elas não dá para atualizar o app por cima.
2. **Leve ao celular** pelo meio mais fácil: o compartilhamento de arquivos da sua VPN (no Tailscale, o Taildrop), um cabo, e-mail ou um serviço de arquivos privado. O arquivo é `mobile/android/app/build/outputs/apk/release/app-release.apk`.
3. **Instale:** abra o arquivo no celular e permita "instalar apps desta origem" quando o Android pedir (a permissão é do app que abriu o arquivo; dá para desligá-la depois).
4. **Ligue a VPN no celular** e abra o app. Na configuração, informe:
   - **Endereço:** o nome ou o IP do servidor na rede privada, sem `http://` e sem porta;
   - **Porta:** `8765`;
   - **Token:** o token dos clientes.

   O token fica no Android Keystore. A linha do topo deve mostrar **Conectada**.
5. **Permissões:** microfone e notificações (na primeira conversa por voz) e, para a música abrir com o app em segundo plano, **«Exibir sobre outros apps»** (a aba Voz mostra um botão «Permitir agora»).
6. **Teste** pelo [roteiro de aceite](mobile.md#aceite): texto, «toca Oceano do Djavan» (exige a [música](integracoes-producao.md#musica) ligada e o YouTube Music instalado), voz (exige a [voz](integracoes-producao.md#voz) ligada).

| Sintoma | Causa e solução |
|---|---|
| «o servidor recusou o token» | Token errado ou trocado: copie de novo. |
| «sem conexão» | VPN desligada no celular, endereço errado ou a política da VPN não inclui o aparelho. |
| Música: «o dispositivo não está conectado» | O app não está aberto e conectado (ele é o executor que abre a música). |
| A música só abre com o app na frente | Falta «Exibir sobre outros apps». |
| Quero chamar sem tocar na tela | Ligue «Botão «Falar» na notificação» na aba Voz, ou use o atalho do ícone (segurar o ícone → Falar) ([Voz por áudio](voz-audio.md#maos-livres)). A palavra falada «Juniper» está no backlog. |
| Pedi um álbum e tocou só uma faixa | Peça «toca o álbum X»; playlists suas tocam pelo nome. |
| Ela ouve a própria voz e se responde | Sem fone, use o botão «Segurar para falar», ou fone. |

### Atualizar o app {#app-atualizar}

O cérebro serve o APK mais recente na rede privada e o app se atualiza ([App mobile](mobile.md#atualizacao), [API HTTP](api-http.md#app-update), [ADR-026](../decisions/ADR-026.md)). Quem publica é o workflow **Mobile release** ([Ambiente mobile](mobile-dev.md#apk-pipeline)).

**Uma vez só:**

1. **Segredos** do GitHub (Settings → Secrets and variables → Actions), além dos do [deploy contínuo](deploy-continuo.md) (`TS_OAUTH_*`, `DEPLOY_*`):

   | Segredo | Conteúdo |
   |---|---|
   | `ANDROID_KEYSTORE_BASE64` | O keystore do app já instalado, em base64: `base64 -w0 ~/juniper-release.keystore`. |
   | `ANDROID_KEYSTORE_PASSWORD` | A senha do keystore. |
   | `ANDROID_KEY_ALIAS` | O alias da chave. |
   | `ANDROID_KEY_PASSWORD` | A senha da chave. |

   Tem de ser o **mesmo** keystore do app instalado: com outra chave o Android recusa a atualização por cima.
2. **Pasta no servidor:** o compose monta `JUNIPER_APP_DIR` (padrão `app-release`, ao lado do `docker-compose.prod.yml`) em `/home/juniper/.juniper/app`, só leitura. O `publish_app.sh` a cria; suba o cérebro com o compose atual para a montagem existir.
3. **Primeiro disparo manual:** Actions → Mobile release → Run workflow (branch `master`). Um workflow disparado por outro só vale com o arquivo na branch padrão. Depois disso, cada merge no `master` com o **Mobile** verde publica sozinho.
4. **Instale à mão um APK novo uma vez** (passos acima): o app instalado antes desse recurso não sabe se atualizar.

**No dia a dia:** ao abrir, o app mostra «Nova versão X disponível»; toque em **Atualizar**, permita «instalar apps desconhecidos» (o app abre a tela do sistema) e confirme a instalação. Em Configurar → Atualizações há «Buscar atualizações». Para conferir o que está publicado:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://<endereço-do-servidor>:8765/app/update
```

| Sintoma | Causa e solução |
|---|---|
| O workflow falha em «Confere os segredos» | Falta algum segredo `ANDROID_*`. |
| O Android recusa a instalação | O APK publicado foi assinado com outra chave: use o keystore do app instalado (ou desinstale e reinstale uma vez). |
| `{"available": false}` | Nada publicado, pasta errada ou arquivo incompleto: confira `JUNIPER_APP_DIR` e os dois arquivos. |
| «já publicado… nada a fazer» no log | O código publicado é o mesmo do build (uma repetição do disparo): normal. |
| O job «Publica na VPS» falha com «recusado: a versão … é menor» | O código do build (número de commits) ficou abaixo do publicado, por exemplo depois de reescrever o histórico. O Android não aceita voltar de versão: veja o `latest.json` na pasta e o histórico do git. |

O primeiro build do CI ainda não foi executado: pode precisar de ajustes (licenças e NDK do Android SDK).

## Dashboard {#dashboard}

1. Libere a porta **8766** na política da VPN para os seus dispositivos (a política do [passo 5.2](deploy-vps.md#rede) já inclui).
2. No navegador de um dispositivo na rede privada: `http://<endereço-do-servidor>:8766/`.
3. Entre com o token dos clientes. O login vale 12 horas (`dashboard.session_hours`) e o token nunca vai na URL.

Para HTTPS (por exemplo, para "instalar" o painel no celular), ponha na frente um proxy que termine TLS dentro da rede privada (no Tailscale, `tailscale serve --bg http://<IP-PRIVADO>:8766`) e inclua o nome usado em `dashboard.allowed_hosts` se ele não for `*.ts.net`. Detalhes e problemas: [Dashboard](dashboard.md#acesso).

## Para saber mais {#para-saber-mais}

- [Bot do Telegram](telegram.md) (Documentação Técnica): O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor, o download do app Android e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
- [Visão geral da produção](producao.md) (Produção): Como a Juniper roda em produção num servidor qualquer: topologia, requisitos, modelo de segurança, configuração, dados e o roteiro das páginas seguintes.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
