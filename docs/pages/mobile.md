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

**T-145, executor `open_url` e autoplay** (`mobile/src/executor`, `android/.../media`):

- **Registro:** a cada `welcome` o app manda `register` com `open_url` (tier `write`). Para cada `invoke` o resultado é sempre uma mensagem tipada, nunca uma exceção: ferramenta desconhecida (`unknown_tool`), endereço recusado (`policy_denied`), o Android não abriu (`os_error`).
- **O celular revalida tudo** (`urlPolicy.ts`, porta de `brain/tools/url_policy.py`): só `http` e `https`; recusa `file:`, `javascript:`, `intent:`, usuário e senha no endereço, `\\`, nomes inválidos, `localhost`, redes privadas, Tailscale (`100.64.0.0/10`) e formas numéricas de IPv4 (`2130706433`, `0x7f.1`, `127.1`), inclusive IPv4 embutido em IPv6; o link aberto é o **normalizado**. Os **74 casos de URL e 14 de confiança** de `tests/fixtures/url_policy_cases.json` rodam no pytest e no Jest, então as duas implementações não se afastam. Diferença conhecida: a conversão de nomes internacionais (IDNA) no app é NFKC + minúsculas + punycode, mais simples que a do Python; como o cérebro já manda o endereço normalizado em ASCII, isso só importa para endereços que cheguem ao app sem passar por ele.
- **Autoplay** (`MediaAutoplayModule.kt`, atrás da interface `MediaAutoplay`): abre o link com uma `Intent` `VIEW` (o Android escolhe o app que trata `music.youtube.com`), espera `waitMs` (3 s) e manda a tecla **play** de mídia até `attempts` (3) vezes, confirmando por `AudioManager.isMusicActive`. O resultado volta ao LLM: «a música começou a tocar», ou «abri, mas não consegui dar play sozinha» (`autoplay: false`, **que não é erro**: o link abriu e basta tocar no play).
- **Histórico local** das últimas 50 aberturas e recusas, só em memória.
- **Roteiro no aparelho (não verificado aqui):** (1) com a tela acesa, peça «toca Oceano do Djavan» e veja se o YouTube Music abre e toca sozinho; (2) repita com a tela apagada: o Android 10+ pode recusar abrir uma tela com o app em segundo plano (`blocked_by_android`); se acontecer, a saída é conceder «exibir sobre outros apps» ao app, ou aceitar um toque; (3) peça um link recusado («abre file:///etc/passwd») e confira que nada abre; (4) mude `waitMs`/`attempts` se o play chegar cedo demais.

**T-146, chat de voz** (`mobile/src/voice`, `mobile/src/ui/VoiceScreen.tsx`, `android/.../voice`):

- **`VoiceController`** (TypeScript, sem React): abre a sessão (`voice.start` com 16 kHz, mono, e se o aparelho sabe transcrever e falar), manda o microfone em pedaços de 100 ms (`voice.audio`, **nada fica enfileirado offline**), toca o áudio que volta, mostra o estado (`listening`, `thinking`, `speaking`) e as legendas. Não interpreta nada do que foi dito: «tchau, Juniper» e «silêncio» são reconhecidos **pelo cérebro**.
- **Interrupção:** `voice.state` com `listening` **e `turn_id`** significa «esse turno foi cortado»: o app descarta na hora o áudio tocando e o que chegar daquele turno ([protocolo](protocolo.html#voz)); um `listening` sem `turn_id` é o fim natural e o áudio já recebido termina de tocar. O botão «Parar de falar» manda `voice.interrupt`.
- **Encerrar:** botão da tela, botão **Encerrar** da notificação, comando falado ou silêncio (o cérebro manda `voice.end`). A queda da conexão também encerra, com aviso na tela.
- **Fallbacks:** `voice.fallback` de STT para `local` liga o reconhecimento de fala do Android (pt-BR, contínuo, pelo módulo Kotlin `JuniperStt`, que usa o `SpeechRecognizer` do Android; o microfone é liberado do cérebro enquanto ele escuta e devolvido quando a nuvem volta) e passa a mandar `voice.text` em vez de áudio; quando volta para `cloud`, o microfone volta a ir para o cérebro. `voice.say` é falado pelo `TextToSpeech` do Android (pt-BR). **Limite conhecido:** com o STT local o microfone é do reconhecedor do Android, então o cérebro não ouve áudio e não detecta interrupção por voz (o botão continua valendo).
- **Kotlin:** `JuniperAudioModule` captura com a fonte `VOICE_COMMUNICATION` (cancelamento de eco e supressão de ruído da plataforma, pedidos também explicitamente quando o aparelho tem) e toca com `AudioTrack` de uso `VOICE_COMMUNICATION`, com `flush` imediato; `VoiceService` é o serviço em primeiro plano do tipo `microphone` («Juniper está ouvindo», botão Encerrar), que mantém a conversa com a tela apagada; `JuniperTtsModule` é a voz do aparelho. Permissões: microfone, notificações (Android 13+), serviço em primeiro plano. Aparelho e fones: o cancelamento de eco depende do aparelho; **fone é recomendado**.
- **Tela:** aba **Voz** com um botão grande (Falar/Encerrar), o estado, a legenda do que o Petrus disse (`voice.transcript`) e a da Juniper (o texto do turno de chat), aviso de voz do aparelho em uso, e a razão quando a conversa termina.
- 26 testes novos (controlador com áudio, TTS e STT falsos: sessão, fluxo de áudio, corte de turno, fallbacks, fim por todos os meios, queda da conexão; tela).

**Roteiro de aceite da voz no aparelho (não verificado aqui):** (1) aba Voz → Falar: permitir microfone e notificações; (2) dizer «que horas são» e ouvir a resposta com a legenda; (3) falar por cima dela e ver o corte; (4) apagar a tela e conversar; a notificação deve continuar; (5) dizer «tchau, Juniper», tocar em Encerrar na notificação e esperar 60 s em silêncio (três jeitos de acabar); (6) pedir uma playlist e responder «sim» (uma pergunta só); (7) com o cérebro sem a chave da Groq ou sem o orçamento do Google, conferir os avisos de voz do aparelho; (8) com fone e sem fone, ver se a própria voz dela vaza e interrompe (ajuste `voice.vad.barge_in_ms`).

**Correções do primeiro uso real:**

- **A música toca em um passo só.** A Juniper agora sabe **por onde o pedido chegou** (app em texto ou voz, terminal, Telegram) e usa `play_music`: no app, abre e dá o play sem segunda pergunta; no terminal e no Telegram devolve o link ([Música](musica.html#play-music)). Antes o modelo às vezes mandava o link no texto e só abria se você insistisse.
- **Música em segundo plano.** O Android bloqueia, sem avisar, um app em segundo plano de abrir a tela de outro. A saída é a permissão **«Exibir sobre outros apps»**: com ela, o serviço de voz mantém uma janela invisível de 1 pixel enquanto a conversa está aberta e o pedido de música abre o YouTube Music mesmo com ele (ou outro app) na frente. A aba Voz mostra um aviso com o botão «Permitir agora» enquanto a permissão falta. Sem ela e com o app em segundo plano, a Juniper **não diz que abriu**: avisa que o Android pode ter bloqueado.
- **Viva-voz.** Sem fone a voz sai pelo alto-falante e o cérebro ignora o microfone enquanto ela fala ([Voz por áudio](voz-audio.html#viva-voz)); há também o botão «Segurar para falar», indicado para quando há música tocando.

Para rodar na sua máquina (JDK 17, Android SDK e Node 22 ou superior): `cd mobile && npm install && npx react-native run-android` (celular com depuração USB ou emulador). Para um APK: crie uma chave local, ponha os dados em `mobile/android/keystore.properties` (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`; o arquivo e a chave **não** vão para o Git) e rode `cd mobile/android && ./gradlew assembleRelease`; o APK sai em `app/build/outputs/apk/release/`. Sem `keystore.properties` o APK sai assinado com a chave de depuração: serve para testar, não para manter.

**Não verificado:** o build do APK e a execução no aparelho (o ambiente de desenvolvimento do Claude não tem Android SDK). Os testes acima provam a lógica em JavaScript.

## Instalar no seu celular (sem adb) {#instalar}

1. **Pré-requisitos no computador:** JDK 17, Android SDK (plataforma 36 e as *build tools*; o jeito mais simples é instalar o Android Studio uma vez) e Node 22 ou superior. Aponte `ANDROID_HOME` para a pasta do SDK.
2. **Gerar o APK:** `cd mobile && npm install && cd android && ./gradlew assembleRelease`. Para assinar com uma chave sua (recomendado), gere uma uma vez com `keytool -genkeypair -v -keystore ~/juniper-release.keystore -alias juniper -keyalg RSA -keysize 2048 -validity 10000` e crie `mobile/android/keystore.properties` com `storeFile`, `storePassword`, `keyAlias` e `keyPassword`. **Guarde a chave e a senha**: sem elas não há como atualizar o app por cima, só desinstalar e reinstalar.
3. **Levar o APK ao celular sem adb:** o arquivo fica em `mobile/android/app/build/outputs/apk/release/app-release.apk`. Mande para o celular pelo que for mais fácil: o Taildrop do Tailscale, um cabo (copiar o arquivo), e-mail ou um serviço de arquivos privado.
4. **Instalar:** abra o arquivo no celular e permita «instalar apps desta origem» quando o Android pedir (é uma permissão do app que abriu o arquivo, e dá para desligá-la depois).
5. **Primeira abertura:** informe o endereço do servidor (nome ou IP do Tailscale, sem `http://`), a porta (8765) e o token dos clientes (na VPS: `docker compose -p juniper-prod exec brain cat /home/juniper/.juniper/state/token`, o mesmo do [dashboard](dashboard.html)), e deixe o Tailscale ligado no celular.

## Roteiro de aceite completo {#aceite}

Marque cada item no aparelho; o que falhar vira uma issue, não um remendo às cegas.

1. **Conexão:** abrir o app com o Tailscale ligado → «Conectada». Desligar o Tailscale → mensagem de «sem conexão»; religar → volta sozinho. Token errado → «o servidor recusou o token».
2. **Texto:** enviar uma mensagem, ver a resposta chegar aos poucos; «Parar» no meio; «Conversas» → retomar uma antiga e começar uma nova; pedir algo que exige confirmação e responder «sim».
3. **Música:** «toca Oceano do Djavan» (texto) → o YouTube Music abre e toca sozinho; se não tocar sozinho, conferir a mensagem da Juniper («está pronta, é só tocar no play»). Pedir um endereço proibido e ver que nada abre.
4. **Voz:** todos os itens do roteiro de voz acima, com e sem fone, com a tela acesa e apagada.
5. **Falhas:** servidor parado no meio de uma conversa (a conversa para com aviso); trocar de rede (Wi-Fi ↔ dados); matar o app e abrir de novo (retoma a última conversa).

## Decisões em aberto {#abertas}

- Versão do Android do aparelho (o `targetSdk` atual é o do template do React Native).
- Identificador do app (`app.juniper.mobile`) e se vale pedir «exibir sobre outros apps» para abrir a música com a tela apagada.
- Se o `TextToSpeech` do Android em pt-BR for ruim, trocar por um modelo local (Piper), como diz o [ADR-023](adr-023.html).
- QR code para o token (hoje colado à mão) e o [login com MFA](login.html).

## O que os testes não provam {#limites}

Jest cobre a lógica (conexão, conversas, política de URL, tipos). Áudio, serviço em primeiro plano, autoplay e o comportamento do Android só se verificam **no aparelho**, pelo roteiro de aceite manual.

## Tasks {#tasks}

T-143 (base do app, **pronto**), T-144 (chat de texto, **pronto**), T-145 (executor e autoplay, **pronto**: o autoplay só se confirma no aparelho), T-146 (chat de voz, **pronto**: só se confirma no aparelho), T-147 (documentação e aceite, **pronto**). Épica #178.
