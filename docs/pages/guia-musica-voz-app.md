---
title: Guia: música, voz e app no celular
group: Produção
order: 3
description: Passo a passo completo para ligar a música (YouTube), a voz (Google e Groq) na VPS, gerar o APK no WSL e testar o app com autoplay, com os erros reais que apareceram.
status: guia
tags: guia tutorial passo a passo youtube data api oauth google cloud text to speech groq vps env certs apk android sdk ndk gradle wsl keystore taildrop autoplay instalar testar problemas
---

> **Nota:** este guia junta, em uma ordem só, o que está espalhado em [Música](musica.html), [Voz por áudio](voz-audio.html), [App mobile](mobile.html) e no [Runbook da VPS](runbook-vps.html). Os passos foram escritos a partir do primeiro uso real; o que **ainda não foi confirmado no aparelho** está marcado.

## Visão geral {#visao-geral}

| Parte | Onde | O que sai dela |
|---|---|---|
| [1. Google Cloud](#google-cloud) | navegador | chave da API do YouTube, cliente OAuth, conta de serviço da voz |
| [2. A VPS](#vps) | servidor | música e voz ligadas e conferidas |
| [3. Testar no terminal](#terminal) | terminal | a busca de músicas funcionando, sem celular |
| [4. O APK](#apk) | seu computador (WSL ou Linux) | o arquivo `app-release.apk` |
| [5. O celular](#celular) | celular | app instalado, conectado, tocando música |
| [6. Problemas](#problemas) | | erros que já apareceram e a saída de cada um |

## As credenciais, de relance {#credenciais}

| Credencial | Para quê | Onde nasce | Onde fica |
|---|---|---|---|
| **Chave de API** do YouTube | buscar músicas | Google Cloud → Credenciais | `.env` da VPS (`JUNIPER_YOUTUBE_API_KEY`) |
| **Cliente OAuth** (ID e segredo) | login para criar playlists | Google Cloud → Credenciais | `.env` (`JUNIPER_YOUTUBE_CLIENT_ID` e `..._SECRET`) |
| **Token do login** (acesso e renovação) | acessar as playlists | `music login` | volume `juniper-music` (nunca no Git) |
| **Conta de serviço** (JSON) | falar com a voz do Google | Google Cloud → Credenciais | `certs/gcp-voice.json` (só leitura) |
| **Chave da Groq** | transcrever a fala | console da Groq | `.env` (`JUNIPER_GROQ_API_KEY`) |
| **Token dos clientes** | o app e o CLI falarem com o cérebro | criado no primeiro boot | volume `juniper-state`; no app, o Android Keystore |
| **Chave de assinatura** do APK | assinar o app | `keytool` | só no seu computador (`~/juniper-release.keystore`) |

Segredos só em `.env`, arquivos montados ou no Keystore: nunca em código, imagem ou log.

## 1. Google Cloud {#google-cloud}

Tudo no **mesmo projeto**, com o faturamento já criado (a voz é paga por caractere; a música não).

1. **Ative duas APIs** (menu «APIs e serviços» → «Biblioteca»): **YouTube Data API v3** e **Cloud Text-to-Speech API**.
2. **Chave de API** (Credenciais → «Criar credenciais» → «Chave de API»): restrinja a chave à *YouTube Data API v3*. Guarde o valor.
3. **Cliente OAuth** (Credenciais → «ID do cliente OAuth»), tipo **«TVs e dispositivos de entrada limitada»**. Guarde o *client id* e o *client secret*. Serve só para criar playlists.
4. **Conta de serviço** (Credenciais → «Conta de serviço»), sem papéis. Em «Chaves», crie uma chave **JSON** e baixe. Esse arquivo é a credencial da voz: não vai para o Git nem para a imagem.
5. **Publicar o app** (tela de consentimento OAuth → status «Em produção»). É grátis e não torna o app público. Sem isso, o token de renovação do login expira em **7 dias** (modo teste). Ao fazer o login aparece «app não verificado»: «Avançado» → «Acessar». Confira a tela atual do Google: os nomes dos menus mudam.
6. A chave da **Groq** (transcrição da fala) vem de `console.groq.com/keys`.

Custos: a YouTube Data API tem 10.000 unidades por dia sem cobrança (cada busca gasta 100). A voz do Google tem teto mensal de caracteres na Juniper (padrão 1.250.000, sob US$ 10; veja [Voz por áudio](voz-audio.html#orcamento)).

## 2. A VPS {#vps}

Todos os comandos na VPS, na pasta `~/juniper`. Veja o [Runbook](runbook-vps.html) para a estrutura de pastas e permissões.

**2.1 Atualizar o código** (se o deploy automático não rodou):
```bash
cd ~/juniper && git pull
docker compose -f docker-compose.prod.yml up -d --build
```

**2.2 Variáveis no `.env`** (`~/juniper/.env`, modo `600`; nunca no Git):
```bash
JUNIPER_GROQ_API_KEY=...
JUNIPER_YOUTUBE_API_KEY=...
JUNIPER_YOUTUBE_CLIENT_ID=...
JUNIPER_YOUTUBE_CLIENT_SECRET=...
JUNIPER_MUSIC__ENABLED=true
JUNIPER_VOICE__ENABLED=true
```
O prefixo `JUNIPER_` e o `__` entre os níveis ligam as opções sem editar o `config.yaml` ([Configuração](configuracao.html)).

**2.3 A chave JSON da voz.** Do seu computador, com o Tailscale ligado:
```bash
scp gcp-voice.json deploy@juniper-brain:~/juniper/certs/gcp-voice.json
ssh deploy@juniper-brain "chmod 600 ~/juniper/certs/gcp-voice.json"
```
A pasta `certs/` é montada no contêiner só para leitura. Se aparecer «permissão negada» nos logs, o dono do arquivo não é o uid do contêiner (10001): veja o aviso no [Runbook](runbook-vps.html#diretorios).

**2.4 Reiniciar e conferir:**
```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml exec brain python -m brain voice status
docker compose -f docker-compose.prod.yml exec brain python -m brain music status
```
Esperado: «Credencial do Google: encontrada», «Chave da Groq: configurada», «Chave da YouTube Data API: configurada», «Cliente OAuth: configurado». Nenhum dos comandos imprime chaves.

**2.5 Login no YouTube** (só para playlists; a busca e o autoplay não dependem dele):
```bash
docker compose -f docker-compose.prod.yml exec brain python -m brain music login
```
Ele mostra um código e um endereço do Google; confirme no navegador. Nos logs aparecem várias linhas `428` do `oauth2.googleapis.com/token` enquanto você não confirma: é o esperado («autorização pendente»). O token de acesso dura 1 hora e é **renovado sozinho** com o token de renovação, que fica no volume `juniper-music`.

**Atalho opcional** no `~/.bashrc` do `deploy`, para usar `juniper` dentro do contêiner ([Produção](producao.html#usar-na-vps)):
```bash
echo "alias juniper='docker compose -f ~/juniper/docker-compose.prod.yml exec brain python -m brain'" >> ~/.bashrc
source ~/.bashrc
```

## 3. Testar a busca no terminal {#terminal}

Sem celular dá para testar a busca de músicas (o `open_url` só funciona com o celular conectado):
```bash
juniper "procura Oceano do Djavan no YouTube Music e me mostra os links"
juniper logs        # procure: tool_call name=music_search
```
Cada busca gasta 100 unidades da cota diária (`juniper music status` mostra o saldo).

- Peça **«procura»**, não «toca»: sem celular, «toca» termina em «o dispositivo não está conectado».
- Se aparecer `tool_call name=open_url` com um link `music.youtube.com/search?...` e nenhum `music_search`, é o comportamento corrigido no T-164: a chamada é recusada e o modelo é mandado para `music_search`. Com a versão atual isso não deve mais chegar ao celular.

## 4. Gerar o APK {#apk}

O APK é gerado **no seu computador**, nunca na VPS. O guia usa o **WSL (Ubuntu) sem Android Studio**: só precisamos compilar, não usar emulador, e o repositório já está no WSL (compilar pelo Windows em `\\wsl$` é lento). Em Linux o procedimento é o mesmo.

**4.1 Pré-requisitos**
```bash
sudo apt update && sudo apt install -y openjdk-17-jdk unzip curl
node -v        # precisa ser 22 ou superior (instale com o nvm se não for)
cd ~/juniper && git checkout master && git pull    # a pasta mobile/ vem com a master
```

**4.2 As ferramentas de linha de comando do Android**

A página `developer.android.com/studio` carrega o link por JavaScript e não baixa pelo terminal. Use o endereço direto do arquivo (se der 404, o número da versão mudou: veja a dica depois do bloco):
```bash
mkdir -p ~/android-sdk/cmdline-tools && cd ~/android-sdk/cmdline-tools
curl -L -o cmdtools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip cmdtools.zip && mv cmdline-tools latest && rm cmdtools.zip
chmod +x latest/bin/*
```
Para achar o número mais novo: `curl -s https://dl.google.com/android/repository/repository2-3.xml | grep -o 'commandlinetools-linux-[0-9]*_latest.zip' | sort -u | tail -1`. Ou baixe o zip «Command line tools only» (Linux) pelo navegador do Windows e copie de `/mnt/c/Users/SEU_USUARIO/Downloads/`.

**4.3 Variáveis de ambiente** (no `~/.zshrc` se você usa zsh, no `~/.bashrc` se usa bash; depois `source` no arquivo):
```bash
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export ANDROID_HOME=$HOME/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools
```
Confira com `which sdkmanager` e `sdkmanager --version`.

**4.4 Licenças e pacotes** (as versões vêm de `mobile/android/build.gradle`; se mudarem lá, mude aqui):
```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-37.0" "build-tools;37.0.0" "ndk;27.1.12297006"
sdkmanager --list_installed
```
Atenção ao nome da plataforma: ela se chama **`android-37.0`** (com ponto), não `android-37`. Os avisos de que o `sdkmanager` «está obsoleto» podem ser ignorados.

**4.5 A chave de assinatura** (uma vez; **guarde o arquivo e as senhas**, sem eles não dá para atualizar o app por cima):
```bash
keytool -genkeypair -v -keystore ~/juniper-release.keystore -alias juniper \
  -keyalg RSA -keysize 2048 -validity 10000
```
Crie `~/juniper/mobile/android/keystore.properties` (o `.gitignore` do app já ignora o arquivo):
```
storeFile=/home/SEU_USUARIO/juniper-release.keystore
storePassword=...
keyAlias=juniper
keyPassword=...
```
Sem esse arquivo o APK sai assinado com a chave de depuração: serve para a primeira tentativa, não para manter.

**4.6 Compilar**
```bash
cd ~/juniper/mobile && npm install
cd android && ./gradlew assembleRelease
```
O primeiro build baixa muita coisa e pode passar de 10 minutos. O APK sai em `mobile/android/app/build/outputs/apk/release/app-release.apk`. Para levar ao Windows:
```bash
cp app/build/outputs/apk/release/app-release.apk /mnt/c/Users/SEU_USUARIO/Downloads/
```

**Atenção, não confirmado:** o código Kotlin do app (áudio, autoplay, serviço de voz) foi escrito sem Android SDK por perto e **nunca foi compilado antes do seu primeiro build**. Se o Gradle acusar erro de compilação, as linhas úteis são as que começam com `e:`. Se faltar memória no WSL, crie `C:\Users\SEU_USUARIO\.wslconfig` com `[wsl2]` e `memory=6GB` e rode `wsl --shutdown` no PowerShell.

## 5. O celular {#celular}

**5.1 Instalar sem adb.** Envie o APK pelo Taildrop do Tailscale (ou cabo, ou e-mail). Abra o arquivo no celular e, quando o Android pedir, permita «instalar apps desta origem».

**5.2 Primeira abertura.** Deixe o Tailscale **ligado** no celular e informe:
- **Endereço:** o nome ou IP Tailscale da VPS, sem `http://` e sem porta;
- **Porta:** `8765`;
- **Token:** na VPS, `docker compose -p juniper-prod exec brain cat /home/juniper/.juniper/state/token`.

A linha do topo deve mostrar «Conectada». O token fica no Android Keystore.

**5.3 Testar o autoplay (tela acesa).** Na aba **Texto**: `toca Oceano do Djavan`.
1. O YouTube Music deve abrir sozinho e a música começar.
2. A resposta da Juniper diz o resultado. «Abri, mas não consegui dar play sozinha» significa que o link abriu e o autoplay falhou: não é erro, basta tocar no play.
3. No servidor: `juniper logs` deve mostrar `music_search`, depois `open_url` com um link `watch?v=...`.
4. Se o play chegar cedo demais, o tempo de espera do app (3 s, 3 tentativas) precisa subir: avise.

**5.4 Autoplay com a tela apagada.** Use a aba **Voz** («Falar») e apague a tela. O Android 10 ou superior pode recusar abrir o app com a tela apagada: o app avisa que o Android bloqueou. A saída é conceder ao app «exibir sobre outros apps». *(Não confirmado no aparelho.)*

**5.5 Voz.** Aba **Voz** → «Falar» → permita microfone e notificações → «que horas são». Teste falar por cima dela (corta), «silêncio», «tchau, Juniper», um minuto calado (encerra por silêncio) e o botão «Encerrar» da notificação. Com e sem fone: se a voz dela vazar no microfone e se interromper, aumente `voice.vad.barge_in_ms` ([Configuração](configuracao.html#voice)).

O [roteiro completo de aceite](mobile.html#aceite) tem todos os itens.

## 6. Problemas que já apareceram {#problemas}

| Sintoma | Causa | Saída |
|---|---|---|
| `zsh: permission denied: sdkmanager` | o zip foi extraído sem permissão de execução | `chmod +x ~/android-sdk/cmdline-tools/latest/bin/*` |
| `sdkmanager: command not found` | variáveis no arquivo errado ou não recarregado, ou a pasta `latest` não existe | ponha no `~/.zshrc` (zsh) ou `~/.bashrc` (bash) e rode `source`; `mv cmdline-tools latest` se preciso |
| `Failed to find package 'platforms;android-37'` | o nome certo tem ponto | `platforms;android-37.0` |
| `Error reading Zip content from a SeekableByteChannel` (NDK) | download cortado | `rm -rf ~/android-sdk/.temp ~/android-sdk/ndk` e repita; se persistir, baixe `android-ndk-r27b-linux.zip` pelo navegador e extraia em `~/android-sdk/ndk/27.1.12297006` |
| `Failed to find target with hash string 'android-37'` no Gradle | o projeto pede `android-37`, o SDK instalou `android-37.0` | avise; a saída é ajustar o projeto (ou usar a plataforma 36) |
| Erros `e:` do Kotlin no build | código Kotlin nunca compilado antes | mande as linhas `e:` |
| App: «o servidor recusou o token» | token errado ou trocado | copie o token de novo da VPS |
| App: «sem conexão» | Tailscale desligado no celular, ou endereço errado | ligue o Tailscale; confira o endereço |
| Música: «o dispositivo não está conectado» | o celular não está conectado como executor (ou só o CLI está aberto) | abra o app com a conexão «Conectada» |
| Música abre a **busca**, não a faixa | o modelo montou um link de busca (corrigido no T-164) | atualize a VPS; confira `tool_call name=music_search` no log |
| `music status`: «Conta do YouTube» desconectada | o token de renovação expirou (7 dias em modo teste) ou foi revogado | publique o app (parte 1, passo 5) e rode `music login` de novo |
| A música abre só quando o app está na frente | o Android bloqueia que um app em segundo plano abra outro | permita «Exibir sobre outros apps» para a Juniper (aviso na aba Voz) |
| Ela ouve a própria voz e se responde (sem fone) | o microfone capta o alto-falante | o app já usa viva-voz com meio-duplex; se persistir, use fone ou «Segurar para falar» |
| Quero chamar sem tocar na tela | falta a palavra de chamada | crie a conta e a AccessKey na Picovoice, treine «Juniper» (Android), salve o `.ppn` em `mobile/android/app/src/main/assets/juniper.ppn`, gere o APK de novo e cole a chave em Configurar (passo a passo em [Voz e áudio](voz-audio.html#wake)) |
| Pedi um álbum e tocou só uma faixa | pedido sem a palavra «álbum» ou playlist | peça «toca o álbum Abbey Road»; playlists suas tocam pelo nome («toca minha playlist X») |
| Ela interpreta a música que está tocando | o cancelamento de eco não cobre a música de outro app | ligue «Segurar para falar» na aba Voz, ou use fone |
| Pediu música e ela mandou o link no texto | o pedido veio do terminal ou do Telegram (não abrem links no celular) ou o modelo não usou `play_music` | no app abre sozinho; de outros canais o link é o esperado |
| Logs com `428` do `oauth2.googleapis.com/token` | o login está esperando você confirmar no navegador | normal; confirme o código no endereço mostrado |
