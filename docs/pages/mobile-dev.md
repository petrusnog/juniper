---
title: Ambiente mobile
group: Ambiente Local
order: 4
description: Como preparar o Android SDK, rodar o app num emulador ou celular com recarga ao vivo, testar, regenerar os tipos do protocolo e gerar um APK.
status: guia
tags: android sdk emulador celular adb metro fast refresh react native node jdk gradle sdkmanager avd apk keystore assinatura testes jest eslint typecheck protocolo wsl
---

O app da Juniper fica em `mobile/`: React Native com TypeScript, Android apenas (`minSdk` 26), com módulos nativos em Kotlin ([App mobile](mobile.md)). Esta página prepara a máquina para desenvolvê-lo. Para ligá-lo ao cérebro local, siga [Primeiros passos](primeiros-passos.md#android).

## Ferramentas {#sdk}

| Ferramenta | Versão |
|---|---|
| Node | 22 ou superior |
| JDK | 17 |
| Android SDK | plataforma, *build tools* e NDK nas versões de `mobile/android/build.gradle` (`compileSdkVersion`, `buildToolsVersion`, `ndkVersion`) |

**Com Android Studio** (o caminho mais simples, e o necessário para usar o emulador com interface gráfica): instale o Android Studio, abra o *SDK Manager* e instale a plataforma, as *build tools* e o NDK das versões acima, mais o *Android Emulator* e o *Platform-Tools*. Aponte `ANDROID_HOME` para a pasta do SDK.

**Só com a linha de comando** (bom para WSL ou servidores de build, quando você vai usar um celular físico):

```bash
sudo apt update && sudo apt install -y openjdk-17-jdk unzip curl
mkdir -p ~/android-sdk/cmdline-tools && cd ~/android-sdk/cmdline-tools
# o nome do arquivo muda com a versão; para achar o mais novo:
#   curl -s https://dl.google.com/android/repository/repository2-3.xml | grep -o 'commandlinetools-linux-[0-9]*_latest.zip' | sort -u | tail -1
curl -L -o cmdtools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip cmdtools.zip && mv cmdline-tools latest && rm cmdtools.zip
chmod +x latest/bin/*
```

Variáveis de ambiente (no `~/.bashrc` ou `~/.zshrc`, depois `source`):

```bash
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
export ANDROID_HOME=$HOME/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator
```

Licenças e pacotes (confira as versões em `mobile/android/build.gradle`):

```bash
yes | sdkmanager --licenses
sdkmanager "platform-tools" "platforms;android-37.0" "build-tools;37.0.0" "ndk;27.1.12297006"
```

> **Atenção:** o nome da plataforma pode ter ponto (`android-37.0`); `sdkmanager --list` mostra os nomes exatos.

## Emulador ou celular {#aparelho}

**Emulador.** No Android Studio, *Device Manager → Create device*, uma imagem de sistema recente (x86_64 com Google APIs). Pela linha de comando:

```bash
sdkmanager "emulator" "system-images;android-36;google_apis;x86_64"
avdmanager create avd -n juniper -k "system-images;android-36;google_apis;x86_64"
emulator -avd juniper
```

O emulador enxerga o computador no endereço `10.0.2.2`. No WSL 2, o emulador precisa de aceleração de hardware, que nem sempre está disponível: nesse caso use um celular.

**Celular.** Ative as *Opções do desenvolvedor* (toque sete vezes em «Número da versão») e a *Depuração USB*, ligue o cabo e autorize o computador. Para o celular alcançar o cérebro e o Metro da sua máquina:

```bash
adb reverse tcp:8765 tcp:8765    # o cérebro (endereço 127.0.0.1 no app)
adb reverse tcp:8081 tcp:8081    # o Metro (o run-android já faz isto)
```

`adb devices` deve listar o aparelho como `device`.

## Rodar com recarga ao vivo {#rodar}

```bash
cd mobile
npm install
npx react-native run-android      # build de debug, instala no aparelho e abre o Metro
```

O Metro serve o JavaScript do app: ao salvar um arquivo de `mobile/src/`, o *Fast Refresh* atualiza a tela sem reinstalar. Se o Metro já estiver fechado, `npx react-native start` o abre de novo; no app, sacudir o aparelho (ou <kbd>Ctrl</kbd>+<kbd>M</kbd> no emulador) abre o menu de desenvolvimento, com *Reload*. Mudanças em Kotlin, no `AndroidManifest.xml` ou em dependências nativas exigem um novo `run-android`.

## Estrutura do código {#estrutura}

| Pasta | O que tem |
|---|---|
| `src/connection/` | O WebSocket com o cérebro: handshake, heartbeat, reconexão ([App mobile](mobile.md#conexao)). |
| `src/protocol/` | Tipos gerados do schema do protocolo e funções de parse e serialização. |
| `src/chat/` | O estado e o controlador do chat de texto. |
| `src/voice/` | O controlador da conversa por voz e as pontes para os módulos nativos de áudio, STT e TTS. |
| `src/executor/` | O executor `open_url`, a política de URL e o autoplay. |
| `src/storage/` | Endereço, porta e token no Android Keystore. |
| `src/ui/` | As telas: configuração, chat de texto e voz. |
| `android/app/src/main/java/.../` | Os módulos Kotlin: áudio, serviço de voz em primeiro plano, reconhecimento de fala, voz do aparelho e autoplay de mídia. |

## Testes e qualidade {#testes}

```bash
cd mobile
npm test                  # Jest: protocolo, conexão, chat, voz, executor, política de URL, telas
npm run typecheck         # tsc --noEmit
npx eslint .
npm run check:protocol    # falha se os tipos do protocolo estiverem velhos
```

O workflow `Mobile` do CI roda tudo isso quando `mobile/` ou o schema do protocolo mudam. Os casos da política de URL (`tests/fixtures/url_policy_cases.json`) rodam no Jest e no pytest, para as duas implementações não se afastarem.

## Tipos do protocolo {#protocolo}

Os tipos TypeScript das mensagens saem do schema que o cérebro gera. Quando o protocolo mudar no lado Python:

```bash
python scripts/export_protocol_schema.py     # regenera docs/protocol/schema.json
cd mobile && npm run gen:protocol             # regenera src/protocol/types.generated.ts
```

A referência das mensagens está em [API WebSocket](protocolo.md).

## Gerar um APK {#apk}

Para instalar sem o Metro (um build de *release*, com o JavaScript embutido):

1. **Chave de assinatura**, uma vez (guarde o arquivo e as senhas: sem eles não é possível atualizar o app por cima, só desinstalar e reinstalar):

   ```bash
   keytool -genkeypair -v -keystore ~/juniper-release.keystore -alias juniper -keyalg RSA -keysize 2048 -validity 10000
   ```

2. **`mobile/android/keystore.properties`** (fora do Git):

   ```text
   storeFile=/home/SEU_USUARIO/juniper-release.keystore
   storePassword=...
   keyAlias=juniper
   keyPassword=...
   ```

   Sem esse arquivo o APK sai assinado com a chave de depuração: serve para testar, não para manter.

3. **Compilar:**

   ```bash
   cd mobile && npm install
   cd android && ./gradlew assembleRelease
   ```

   O APK sai em `mobile/android/app/build/outputs/apk/release/app-release.apk`. A instalação no celular está em [Clientes em produção](clientes-producao.md#app).

## Problemas comuns {#problemas}

| Sintoma | Causa e solução |
|---|---|
| `permission denied: sdkmanager` | O zip foi extraído sem permissão de execução: `chmod +x ~/android-sdk/cmdline-tools/latest/bin/*`. |
| `sdkmanager: command not found` | As variáveis não foram carregadas (`source ~/.bashrc`) ou a pasta não se chama `latest`. |
| `Failed to find package 'platforms;android-NN'` | O nome tem ponto: veja `sdkmanager --list`. |
| `Error reading Zip content` ao instalar o NDK | Download cortado: `rm -rf ~/android-sdk/.temp ~/android-sdk/ndk` e repita. |
| Erros de compilação Kotlin (linhas `e:`) | Leia as linhas que começam com `e:`; costumam apontar o arquivo e a linha. |
| O Gradle fica sem memória no WSL | Crie `C:\Users\SEU_USUARIO\.wslconfig` com `[wsl2]` e `memory=6GB` e rode `wsl --shutdown`. |
| Tela vermelha «Unable to load script» | O Metro não está rodando ou o aparelho não o alcança: `npx react-native start` e `adb reverse tcp:8081 tcp:8081`. |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | O app instalado foi assinado com outra chave: desinstale antes. |

## Para saber mais {#para-saber-mais}

- [Primeiros passos](primeiros-passos.md) (Ambiente Local): Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
- [Ambiente Docker](dev-docker.md) (Ambiente Local): Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar logs e banco, e os problemas comuns.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
