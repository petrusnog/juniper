---
title: App mobile
group: Documentação Técnica
section: Clientes
order: 24
description: O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
status: pronta
tags: mobile android react native app celular atualização update apk chat texto voz keystore token executor open_url autoplay kotlin foreground service metro protocolo
---

> **Nota:** esta página detalha o [ADR-022](../decisions/ADR-022.md), que substituiu o cliente desktop em Electron ([ADR-015](../decisions/ADR-015.md)). Código em `mobile/`. Para montar o ambiente, veja [Ambiente mobile](mobile-dev.md).

## Visão geral {#visao-geral}

O app é um **cliente fino** ([Arquitetura](architecture.md#regras)): captura entrada e mostra saída, sem lógica de agente e sem acesso ao MongoDB. Ele fala com o cérebro pelo [WebSocket](protocolo.md) e, além de cliente, é o **executor** do celular: cumpre `open_url` ([Executor](executor.md)).

| Aba | O que faz |
|---|---|
| **Texto** | Lista de conversas, retomar, nova conversa, resposta em *streaming*, cancelar. O "sim" de uma confirmação é uma mensagem comum ([Agente](agente.md#confirmacao)). |
| **Voz** | Uma conversa contínua: o microfone fica aberto, a Juniper entende, responde e fala, com legenda e interrupção ([Voz por áudio](voz-audio.md)). |

## Decisões {#decisoes}

- **Só Android**, `minSdk` 26; **React Native** com TypeScript e módulos nativos em Kotlin, na pasta `mobile/` do mesmo repositório.
- **Tipos do protocolo gerados** de `docs/protocol/schema.json` (`npm run gen:protocol`); `npm run check:protocol` e um teste Jest falham se estiverem velhos.
- **Credenciais no Android Keystore**, nunca em código ou log.
- **Executor:** o app registra `open_url` e **revalida a política de URL** com os mesmos casos de teste do cérebro.
- **Voz em segundo plano** por um serviço em primeiro plano, com notificação.
- **Aparência:** visual de terminal, num tema único e escuro, qualquer que seja o tema do sistema: fundo preto, o verde neon da logo e a paleta Tango dos terminais, em fonte monoespaçada (`src/theme/tokens.ts`; o [dashboard](dashboard.md#visual) e o livro de bolso usam as mesmas cores).
- **A marca animada:** `src/ui/BrandMark.tsx` desenha a logo, o nome **Juniper** à direita e um cursor `_` que pisca (a cada 550 ms, como o de um terminal). Fica numa barra no topo da tela principal, visível nas abas Texto e Voz, e grande na tela de configuração. Com «remover animações» ligado no Android, o cursor fica parado. A imagem é `branding/logo.png` (PNG transparente feito de `branding/juniper-neon.jpg`).
- `android:usesCleartextTraffic` é `true` porque o cérebro fala `ws://`: a cifra vem da rede privada que liga o celular ao servidor ([ADR-013](../decisions/ADR-013.md)).

## Conexão {#conexao}

`src/connection/BrainConnection.ts`:

- WebSocket para `ws://<endereço>:<porta>/v1/ws` com `Authorization: Bearer` no handshake; `hello` como cliente `mobile`; `pong` para cada `ping`.
- Retoma a conversa aberta ao reconectar. Reconexão com recuo de 1, 2, 4, 8, 16 e 30 s (±20%), zerado depois de 30 s estáveis; `1012` (reinício do servidor) reconecta em 0,5 s.
- `4401`, `4426` e `4409` param de tentar e viram os estados «o servidor recusou o token», «versão incompatível» e «outro aparelho assumiu».
- Não enfileira mensagens offline: `send` devolve `false` e a tela mantém o texto.
- Um quadro malformado ou binário é ignorado sem derrubar a conexão.

**Credenciais** (`src/storage/credentials.ts`): endereço, porta, credencial e identificador do aparelho num único item do Keystore (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`). A credencial é o `client_id` e o `client_secret` ([Usuários e credenciais](usuarios.md)), guardados juntos como `client_id.client_secret` no campo `token`; com o *Client ID* vazio, vale o token estático antigo. A tela de configuração valida o endereço (nome ou IP, sem `http://` nem porta) e esconde o segredo.

## Chat de texto {#chat}

`src/chat/` e `src/ui/ChatScreen.tsx`:

- **Estado como reducer puro** (`state.ts`): espelha o que o cérebro mandou (mensagens, o turno em andamento, a lista de conversas). O app **não guarda estado de confirmação** ([ADR-010](../decisions/ADR-010.md)).
- **Controlador** (`ChatController.ts`): `send` recusa vazio, recusa enquanto ela responde e, sem conexão, não envia nem enfileira; `cancel` manda `chat.cancel`; nova conversa, retomar, listar. Ao conectar, retoma a conversa aberta (ou a última) e pede `session.resume` para trazer o histórico.
- **Streaming:** cada `chat.token` cresce a bolha da resposta; uma resposta cancelada sem texto some e uma com texto fica marcada «(interrompida)». Erro tipado aparece na conversa; `session.detached` avisa que outro aparelho assumiu.
- **Tela:** mensagens, campo de texto, botão «Enviar» que vira «Parar» enquanto ela responde, «Conversas» e a linha de estado da conexão sempre no topo.

## Executor e autoplay {#executor}

`src/executor/` e o módulo Kotlin de mídia:

- **Registro:** a cada `welcome` o app manda `register` com `open_url` (tier `write`). Todo `invoke` tem como resposta um `result` tipado: `unknown_tool`, `policy_denied` ou `os_error` quando falha.
- **O celular revalida tudo** (`urlPolicy.ts`, porta de `brain/tools/url_policy.py`): só `http` e `https`; recusa `file:`, `javascript:`, `intent:`, credenciais no endereço, `\`, nomes inválidos, `localhost`, redes privadas, a faixa `100.64.0.0/10` e formas numéricas de IPv4, inclusive IPv4 embutido em IPv6; abre o endereço **normalizado**. Os casos de `tests/fixtures/url_policy_cases.json` rodam no pytest e no Jest. Diferença conhecida: a conversão de nomes internacionais no app (NFKC, minúsculas e punycode) é mais simples que a do Python; como o cérebro já manda o endereço normalizado em ASCII, isso só importa para endereços que cheguem ao app sem passar por ele.
- **Autoplay** (`MediaAutoplayModule.kt`, atrás da interface `MediaAutoplay`): abre o link com uma `Intent` `VIEW` (o Android escolhe o app que trata `music.youtube.com`), espera 3 s e manda a tecla **play** de mídia até 3 vezes, confirmando por `AudioManager.isMusicActive`. O resultado volta ao modelo: «a música começou a tocar» ou «abri, mas não consegui dar play sozinha» (`autoplay: false`, que não é erro).
- **Em segundo plano:** o Android bloqueia, sem avisar, um app em segundo plano de abrir a tela de outro. Com a permissão **«Exibir sobre outros apps»**, o serviço de voz mantém uma janela invisível de 1 pixel enquanto a conversa está aberta, e a música abre mesmo com outro app na frente. A aba Voz mostra um aviso com o botão «Permitir agora» enquanto a permissão falta; sem ela, a Juniper não afirma que abriu.
- **Histórico local** das últimas 50 aberturas e recusas, só em memória.

## Voz {#voz}

`src/voice/`, `src/ui/VoiceScreen.tsx` e os módulos Kotlin de áudio:

- **`VoiceController`**: abre a sessão (`voice.start` com 16 kHz mono, a rota de áudio e se o aparelho sabe transcrever e falar), manda o microfone em pedaços de 100 ms (sem enfileirar offline), toca o áudio que volta, mostra o estado (`listening`, `thinking`, `speaking`) e as legendas. Não interpreta o que foi dito: «tchau, Juniper» e «silêncio» são reconhecidos **pelo cérebro**.
- **Interrupção:** `voice.state` com `listening` **e `turn_id`** significa «esse turno foi cortado»: o app descarta na hora o áudio daquele turno ([protocolo](protocolo.md#voz)). O botão «Parar de falar» manda `voice.interrupt`.
- **Viva-voz e fone:** sem fone, a voz sai pelo alto-falante e o microfone fica fechado enquanto ela fala ([Voz por áudio](voz-audio.md#viva-voz)). O botão **«Segurar para falar»** só abre o microfone enquanto pressionado, indicado com música tocando no aparelho.
- **Mãos livres:** o link `juniper://voz` (botão «Falar» da notificação, atalho do ícone) abre a aba Voz e inicia a sessão com `wake: true` (`VoiceLauncher`, módulo Kotlin `JuniperLauncher`, filtro de intent e `shortcuts.xml`). A escolha da notificação fica nas credenciais (`shortcutNotification`). Veja [Voz por áudio](voz-audio.md#maos-livres).
- **Encerrar:** botão da tela, botão **Encerrar** da notificação, comando falado ou silêncio (o cérebro manda `voice.end`). A queda da conexão também encerra, com aviso.
- **Fallbacks:** `voice.fallback` de STT para `local` liga o reconhecimento de fala do Android (`JuniperStt`, pt-BR) e o app passa a mandar `voice.text`; quando volta para `cloud`, o microfone volta ao cérebro. `voice.say` é falado pelo `TextToSpeech` do Android. Com o STT local, o cérebro não ouve áudio e não detecta interrupção por voz (o botão continua valendo).
- **Kotlin:** `JuniperAudioModule` captura com a fonte `VOICE_COMMUNICATION` (cancelamento de eco e supressão de ruído da plataforma) e toca com `AudioTrack` (uso de mídia) para o Bluetooth, o fone com fio ou o alto-falante, **nunca o auricular**, abaixando as outras mídias com o foco de áudio ([Voz por áudio](voz-audio.md#viva-voz)); `VoiceService` é o serviço em primeiro plano do tipo `microphone` («Juniper está ouvindo»), que mantém a conversa com a tela apagada; `JuniperTtsModule` é a voz do aparelho. Permissões: microfone, notificações (Android 13+) e serviço em primeiro plano.

## Atualização {#atualizacao}

`src/update/`, `src/ui/UpdateViews.tsx` e o módulo Kotlin `JuniperUpdateModule` ([ADR-026](../decisions/ADR-026.md)). O cérebro serve o APK dentro da rede privada ([API HTTP](api-http.md#app-update)); o app só decide se há versão nova e a entrega ao Android.

- **Verificação:** ao abrir, o `UpdateController` pergunta `GET /app/update` (com o token) em silêncio, usando o mesmo endereço e porta do WebSocket. Há versão nova quando o `version_code` publicado é maior que o instalado; então aparece a faixa «Nova versão X disponível» com o botão **Atualizar**. Em **Configurar**, a seção «Atualizações» mostra a versão instalada e o botão «Buscar atualizações».
- **Instalação:** o módulo baixa o APK direto para uma sessão do `PackageInstaller`, confere o **SHA-256** (e o tamanho) antes de confirmar e entrega ao instalador do sistema, que ainda pede a confirmação da pessoa. Em Android 12+ o sistema pode dispensar essa pergunta nas atualizações seguintes se o app for o instalador do app instalado; é comportamento do sistema, não verificado.
- **Permissão:** exige «instalar apps desconhecidos» para a Juniper; sem ela o app abre a tela do sistema e pede para tocar em Atualizar de novo.
- **Segurança:** o Android só aceita atualização assinada com a mesma chave do app instalado; o token protege o download; o tráfego é HTTP puro, mas dentro da rede privada (o mesmo modelo do WebSocket).
- **Versão:** `versionCode` é o número de commits do repositório e `versionName` é `0.1.<código>` (`mobile/android/app/build.gradle`). Um build local mais antigo que o publicado será «atualizado» para o publicado.
- **Limites:** o app instalado antes desse módulo precisa de um APK novo instalado à mão uma vez. O módulo Kotlin e o pipeline ainda não foram executados num aparelho nem no CI. Publicar: [Ambiente mobile](mobile-dev.md#apk-pipeline) e [Clientes em produção](clientes-producao.md#app-atualizar).

## Roteiro de aceite no aparelho {#aceite}

Os testes Jest provam a lógica (conexão, conversas, política de URL, voz com áudio falso). Áudio real, serviço em primeiro plano, autoplay e o comportamento do Android só se verificam no aparelho:

1. **Conexão:** abrir o app → «Conectada». Cortar a rede → «sem conexão»; religar → volta sozinho. Credencial errada → «o servidor recusou o token».
2. **Texto:** mandar uma mensagem e ver a resposta chegar aos poucos; «Parar» no meio; retomar uma conversa antiga e começar uma nova; pedir algo que exige confirmação e responder «sim».
3. **Música:** «toca Oceano do Djavan» → o YouTube Music abre e toca sozinho (ou a Juniper diz que está pronta para tocar). Pedir um endereço proibido («abre file:///etc/passwd») e ver que nada abre.
4. **Voz:** «que horas são» com legenda; falar por cima dela (com fone); «silêncio»; apagar a tela e continuar; «tchau, Juniper», o botão da notificação e um minuto em silêncio; pedir uma playlist e responder «sim»; com e sem fone.
5. **Falhas:** servidor parado no meio de uma conversa; trocar de rede (Wi-Fi ↔ dados); fechar o app e abrir de novo (retoma a última conversa); cérebro sem a chave da Groq ou sem o orçamento do Google (voz do aparelho assume).
6. **Atualização:** publicar uma versão com código maior e ver a faixa «Nova versão» ao abrir; tocar em Atualizar, permitir «instalar apps desconhecidos», confirmar e conferir a versão em Configurar; com a VPN desligada, «Buscar atualizações» deve falhar sem travar o app.

## Limites e decisões em aberto {#limites}

- O cancelamento de eco depende do aparelho; **fone é recomendado** para interromper falando.
- Com o app em segundo plano e sem «Exibir sobre outros apps», o Android pode bloquear a abertura da música.
- A credencial (`client_id` e `client_secret`, [Usuários e credenciais](usuarios.md)) é digitada à mão (sem QR code); o [login com MFA](login.md) é uma proposta.
- Se o `TextToSpeech` do Android em pt-BR for ruim, o [ADR-023](../decisions/ADR-023.md) prevê trocar por um modelo local.

## Testes {#testes}

```bash
cd mobile && npm test && npm run typecheck && npx eslint .
```

## Para saber mais {#para-saber-mais}

- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor, o download do app Android e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Ambiente mobile](mobile-dev.md) (Ambiente Local): Como preparar o Android SDK, rodar o app num emulador ou celular com recarga ao vivo, testar, regenerar os tipos do protocolo e gerar um APK.
- [Onde conversar](produto-canais.md) (Produto): Os canais da Juniper (terminal, Telegram, app Android com texto e voz, painel web) e o que cada um faz de melhor.
- [Login com MFA](login.md) (Documentação Técnica): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [Voz no terminal](voz.md) (Documentação Técnica): Como ela fala: os prefixos, fala fixa versus gerada por IA, e a regra para comandos novos.
- [Voz por áudio](voz-audio.md) (Documentação Técnica): Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do protocolo.
- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [Música](musica.md) (Documentação Técnica): Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
- [Clientes em produção](clientes-producao.md) (Produção): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o dashboard.
