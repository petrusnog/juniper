---
title: App mobile
group: Clientes
order: 6
description: O app da Juniper no celular Android (React Native): chat de texto, chat de voz contínuo, links do YouTube Music abertos no aparelho.
status: proposto
tags: mobile android react native app celular chat texto voz tailscale keystore token executor open_url autoplay kotlin foreground service
---

> **Nota:** esta página resume o [ADR-022](adr-022.html) (proposto), que **substitui** o [cliente desktop](desktop.html) em Electron. O código está em `mobile/`; esta página diz o que já existe (seção «O que já existe») e o que ainda é desenho.

## Visão geral {#visao-geral}

O app é um **cliente fino** ([Arquitetura](architecture.html#regras)): captura entrada e mostra saída, sem lógica de agente e sem acesso ao MongoDB. Ele fala com o cérebro por **WebSocket** dentro do Tailscale ([Produção](producao.html)), então o celular precisa do app do Tailscale ligado.

| Tela | O que faz |
|---|---|
| **Texto** | Lista de conversas, retomar, nova conversa, resposta em *streaming*, cancelar. O "sim" de uma confirmação é uma mensagem comum ([Agente](agente.html#confirmacao)). |
| **Voz** | Um botão abre uma conversa contínua: o microfone fica aberto, a Juniper entende, responde e fala, com legenda na tela e interrupção ([Voz por áudio](voz-audio.html)). |

## Decisões {#decisoes}

- **Só Android**, `minSdk` 26. **React Native puro** com TypeScript e módulos nativos em Kotlin; pasta `mobile/` no mesmo repositório.
- **Conexão:** `Authorization: Bearer` no handshake; o token vai para o **Android Keystore**, nunca em código ou log. Reconexão automática, estado da conexão sempre visível. Quando o [login com MFA](login.html) existir, o app abre sessão com o código sem refazer a conexão.
- **Executor:** o app registra `open_url` e **revalida a política de URL** com os mesmos casos de teste do cérebro ([Executor](executor.html#politica-url)).
- **Autoplay:** depois de abrir o link do YouTube Music, um módulo nativo manda "play" à sessão de mídia. Se falhar, o link já abriu e o Petrus aperta play ([Música](musica.html)).
- **Voz em segundo plano:** serviço em primeiro plano com notificação ("Juniper está ouvindo"); áudio de entrada com a fonte `VOICE_COMMUNICATION` (cancelamento de eco do aparelho; fone recomendado).
- **Tipos do protocolo gerados** de `docs/protocol/schema.json`; um teste falha se estiverem velhos.
- **Aparência:** os tokens do livro de bolso (paleta clara e escura, fonte monoespaçada para código).
- **Build local** de APK, instalado à mão na v1.

## O que já existe {#existe}

**T-143, base do app** (`mobile/`):

- **Projeto:** template oficial do React Native 0.88 (Android apenas; a pasta `ios` não foi trazida), TypeScript, `minSdk` 26, `applicationId` `app.juniper.mobile`, nome na tela «Juniper». `android:usesCleartextTraffic` é `true` porque o cérebro fala `ws://` e só é alcançável dentro do túnel do Tailscale, que já cifra o tráfego ([ADR-013](adr-013.html)).
- **Tipos do protocolo gerados** (`npm run gen:protocol`): `src/protocol/types.generated.ts` sai de `docs/protocol/schema.json`; `npm run check:protocol` (e um teste Jest) falham se estiver velho. `src/protocol/index.ts` tem `parseMessage`, `serialize` e `Msg<'tipo'>` para estreitar por `type`.
- **Conexão** (`src/connection/BrainConnection.ts`): WebSocket com `Authorization: Bearer` no handshake, `hello` como cliente `mobile`, `pong` para cada `ping`, retomada da conversa aberta, reconexão com recuo de 1, 2, 4, 8, 16 e 30 s (±20%), zerado depois de 30 s estáveis; `1012` (deploy) reconecta em 0,5 s; `4401`, `4426` e `4409` param de tentar e viram os estados `unauthorized`, `incompatible` e `replaced`. Não enfileira mensagens offline (ADR-012): `send` devolve `false`. Um quadro malformado ou binário é ignorado sem derrubar a conexão.
- **Credenciais** (`src/storage/credentials.ts`): endereço, porta, token e identificador do aparelho num único item do **Android Keystore** (Keychain, `WHEN_UNLOCKED_THIS_DEVICE_ONLY`); o token nunca vai a log nem a nenhuma mensagem. A tela de configuração valida o endereço (sem `http://` nem porta no campo) e esconde o token.
- **Tema** (`src/theme/tokens.ts`): a paleta clara e escura do livro de bolso, seguindo o tema do sistema.
- **Testes** (`npm test`): protocolo, conexão com um WebSocket falso (token, hello, ping/pong, backoff, códigos de fechamento, quadros ruins), credenciais com o Keychain simulado, tela de configuração. Mais `npm run typecheck` e `npx eslint .`; o workflow `Mobile` roda tudo isso quando `mobile/` ou o schema mudam.

**T-144, chat de texto** (`mobile/src/chat`, `mobile/src/ui/ChatScreen.tsx`):

- **Estado como reducer puro** (`state.ts`): espelha o que o cérebro mandou (mensagens, o turno em andamento, a lista de conversas). O app **não guarda estado de confirmação**: o «sim» de «Posso apagar?» é uma mensagem comum ([ADR-010](adr-010.html)).
- **Controlador** (`ChatController.ts`, TypeScript sem React): `send` (recusa vazio, recusa enquanto ela responde e, **sem conexão, não envia nem enfileira, avisa e devolve `false` para a tela manter o texto**), `cancel` (`chat.cancel` do turno ativo), nova conversa, retomar, listar. Ao conectar, retoma a conversa aberta (ou a última, na primeira vez) e, se o cérebro disser que retomou, pede `session.resume` para trazer o histórico.
- **Streaming:** cada `chat.token` cresce a bolha da resposta; uma resposta cancelada sem texto some e uma com texto fica marcada «(interrompida)». Erro tipado do cérebro aparece na conversa e libera o campo de envio. `session.detached` avisa que outro aparelho assumiu a conversa.
- **Tela:** lista de mensagens, campo de texto, botão «Enviar» que vira «Parar» enquanto ela responde, e «Conversas» (lista com prévia, retomar, nova conversa). A linha de estado da conexão fica sempre no topo.
- 22 testes novos (reducer, controlador com conexão falsa, tela).

Para rodar na sua máquina (JDK 17, Android SDK e Node 22 ou superior): `cd mobile && npm install && npx react-native run-android` (celular com depuração USB ou emulador). Para um APK: crie uma chave local, ponha os dados em `mobile/android/keystore.properties` (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`; o arquivo e a chave **não** vão para o Git) e rode `cd mobile/android && ./gradlew assembleRelease`; o APK sai em `app/build/outputs/apk/release/`. Sem `keystore.properties` o APK sai assinado com a chave de depuração: serve para testar, não para manter.

**Não verificado:** o build do APK e a execução no aparelho (o ambiente de desenvolvimento do Claude não tem Android SDK). Os testes acima provam a lógica em JavaScript.

## O que os testes não provam {#limites}

Jest cobre a lógica (conexão, conversas, política de URL, tipos). Áudio, serviço em primeiro plano, autoplay e o comportamento do Android só se verificam **no aparelho**, pelo roteiro de aceite manual.

## Tasks {#tasks}

T-143 (base do app, **pronto**), T-144 (chat de texto, **pronto**), T-145 (executor e autoplay), T-146 (chat de voz), T-147 (documentação e aceite). Épica #178.
