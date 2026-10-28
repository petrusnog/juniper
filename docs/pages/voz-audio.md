---
title: Voz por áudio
group: Clientes
order: 7
description: Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do protocolo.
status: proposto
tags: voz audio tts stt google chirp leda groq whisper vad orçamento fallback protocolo voice pcm base64 interrupção encerrar
---

> **Nota:** esta página resume o [ADR-023](adr-023.html) (proposto). A [voz do terminal](voz.html) é outra coisa: são as frases escritas que ela diz ao abrir o CLI.

## Visão geral {#visao-geral}

O áudio **passa pelo cérebro** (`brain/voice/`), porque a credencial do Google e a trava de orçamento não podem ficar no cliente. O agente continua só com texto: a voz é uma camada em volta dele.

```text
app ──áudio (PCM 16 kHz)──► cérebro: VAD → STT (Groq) → turno de chat (o de sempre) ──┐
app ◄──áudio (PCM 24 kHz)── cérebro: frases → TTS (Google, voz Leda) ◄───────────────┘
```

## Pontos de decisão {#decisoes}

- **TTS:** Google Cloud, Chirp 3 HD, voz `pt-BR-Chirp3-HD-Leda`. A v1 sintetiza **uma frase por vez** e envia o áudio enquanto o LLM ainda escreve a seguinte. URLs, markdown e código **não são lidos**.
- **STT:** Whisper da Groq (`pt`). Falas curtas, sem energia ou iguais a frases conhecidas de alucinação do Whisper são descartadas.
- **VAD no cérebro:** detector de energia com piso de ruído adaptativo; o app só transmite áudio. Falar por cima da Juniper a interrompe (`barge_in_ms`).
- **Orçamento:** o cérebro conta os caracteres enviados ao Google por mês; ao atingir o limite (padrão 1.250.000), a voz cai para o TTS do Android e a Juniper avisa uma vez.
- **Fallbacks:** limite da Groq → o app usa o STT nativo do Android em pt-BR e manda texto; orçamento ou falha do Google → o app fala o texto com o TTS do Android. A conversa não para.
- **Confirmação por voz:** uma única pergunta; o "sim" é a transcrição seguinte, interpretada por `consent.py` ([Agente](agente.html#confirmacao)).
- **Encerrar:** botão, comando falado (reconhecido sem LLM) ou silêncio prolongado.
- **A voz não autentica:** a sessão herda a autenticação da conexão ([Login com MFA](login.html)).
- **Dados que saem da máquina:** o áudio do Petrus vai à Groq e o texto das respostas ao Google. A política de retenção de cada um precisa ser conferida antes do uso diário.

## Falar: texto preparado e o sintetizador {#falar}

O que o cérebro faz com a resposta antes de ela virar voz (módulo `brain/voice`, T-149):

- **Texto para fala** (`speech_text`): tira os sinais de markdown, tabelas e linhas de separação; tira emojis e caracteres invisíveis; **nunca lê URL** (um link vira, uma vez por resposta, "O link está na tela."); um bloco de código vira uma frase só ("Deixei o trecho de código na tela.") e o conteúdo dele não é lido.
- **Frases enquanto o LLM escreve** (`SentenceChunker`): junta os `chat.token` em frases, na ordem, e entrega cada uma assim que fecha, para a primeira ser falada antes de a resposta acabar. Frases com menos de 24 caracteres viajam junto com a seguinte (a entonação não quebra); o ponto de um número decimal ("3.5") não corta a frase; um bloco de código aberto espera fechar. O total falado respeita `voice.max_spoken_chars`: passou disso, o resto fica na tela e ela diz "O resto está na tela." uma vez.
- **Sintetizador** (`Synthesizer`, interface; `GoogleSynthesizer`): uma chamada de `text:synthesize` por frase (textos acima de 450 caracteres são partidos nos espaços), `LINEAR16` na taxa configurada, WAV recebido → PCM sem o cabeçalho (reamostrado se a taxa vier outra). A autenticação é uma **conta de serviço em arquivo montado** (`voice.tts.credentials_file`); o token se renova sozinho e só viaja no cabeçalho `authorization` (o log mascara por nome).
- **Erros tipados** (`VoiceError`, mensagens fixas em PT-BR, sem chave, URL, token nem resposta crua): `not_configured` (credencial ausente, inválida ou recusada), `quota` (limite de uso do Google), `network_error`, `api_error`, `invalid_audio`.

As opções estão em [Configuração](configuracao.html#voice).

## Ouvir: detector de fala e transcrição {#ouvir}

Implementado no T-151 (`brain/voice/vad.py` e `stt.py`):

- **Detector de fala** (`Vad`): recebe PCM 16 bits, 16 kHz, mono, em pedaços de qualquer tamanho (quadros de 20 ms; sobra de um pedaço entra no seguinte). A fala começa quando a energia fica acima de 3 vezes o piso de ruído por `voice.vad.speech_start_ms`; termina após `silence_ms` de silêncio ou ao chegar em `max_utterance_seconds` (o que continuar sendo dito vira a fala seguinte). Entram na fala os 300 ms anteriores (para não cortar a primeira sílaba). Falas abaixo de `min_utterance_ms` de voz (um estalo, uma tosse) são descartadas. O piso de ruído acompanha o ambiente: desce rápido e sobe devagar, então uma sala barulhenta deixa de contar como fala, mas a voz continua se destacando.
- **Interromper** (`set_playing(True)` enquanto ela fala): o limiar sobe (6 vezes o piso, no mínimo 900) e são exigidos `barge_in_ms` de fala contínua. Um sopro ou a própria voz dela vazando no microfone não a corta. O evento é `barge_in`, no lugar de `speech_start`; a fala segue sendo gravada.
- **Transcrição** (`GroqTranscriber`): cada fala vai como WAV mono para `/audio/transcriptions` (`whisper-large-v3-turbo`, `pt`, temperatura 0) com a chave `JUNIPER_GROQ_API_KEY` (cabeçalho `authorization`, que o log mascara). Erros tipados, sem a resposta crua do provedor: 429 vira `quota` (com o `retry-after`, se for razoável), 401/403 `not_configured`, 400/413/422 `invalid_audio`, o resto `api_error`/`network_error`. Fala acima de 24 MB nem é enviada.
- **Alucinações** (`is_hallucination`): texto vazio, frases que o Whisper inventa no silêncio ("Legendas pela comunidade Amara.org", "Obrigado por assistir"...) e a mesma palavra repetida são descartados antes de virar turno. Um "obrigado" sozinho também cai; dentro de uma frase real ("obrigado, Juniper, toca uma música") ele passa.
- **Cota e volta** (`SpeechToText`): ao bater no limite da Groq, o cérebro manda `voice.fallback` (`stt` → `local`), fica quieto pelo `retry-after` (ou `voice.stt.cooldown_seconds`) e o app passa a transcrever sozinho (`voice.text`). A fala que estourou o limite se perde (o app não guarda o áudio): a sessão pede para repetir. Como o app para de enviar áudio, é o cérebro que avisa quando vale tentar de novo (`recovered()`); se falhar outra vez, volta ao aparelho com novo aviso. Só tamanhos vão ao log, nunca as palavras.

## Viva-voz, fone e música tocando {#viva-voz}

Sem fone, a voz sai pelo alto-falante e o microfone ouve a própria Juniper. O que existe contra o laço de «ela se ouve e responde a si mesma»:

1. **Cancelamento de eco do Android:** o app captura com a fonte `VOICE_COMMUNICATION`, liga o modo de comunicação e manda a voz para o **viva-voz** (sem isso o Android usa o alto-falante da chamada, junto ao ouvido). Com fone conectado, a voz vai para o fone.
2. **Meio-duplex em toda interação sem fone** ([protocolo](protocolo.html#voz)): enquanto ela fala (e por 1 s depois), o microfone fica **fechado**: o cérebro ignora o que chega e o app nem envia. A voz dela, o eco e a música dessa janela nunca viram fala. O preço: **não dá para interromper falando por cima**, só pelo botão «Parar de falar». Só com fone (`route: headset`) o corte por voz continua.
3. **«Segurar para falar»** (botão na aba Voz): o microfone só vai ao cérebro enquanto o botão está apertado (e ao soltar vão 900 ms de silêncio para o cérebro fechar a fala). É a saída segura quando há **música tocando no celular**: ela não é interpretada, porque o microfone só abre quando você quer. Apertar o botão enquanto ela fala também a interrompe.

O que **não** dá para garantir sem fone: falar por cima de uma música alta sem apertar o botão. O Android cancela o eco da voz da Juniper, não o de outro app; sem o botão, a música pode ser tomada por fala (e a transcrição de letra de música não é filtrada). Por isso: fone, ou «Segurar para falar».

## Chamar falando «Juniper» (palavra de chamada) {#wake}

Com a opção **Chamar falando «Juniper»** ligada na aba Voz, o app fica em **espera**: um serviço em primeiro plano (notificação «Juniper aguardando») escuta o microfone **só no aparelho**, com o detector da Picovoice (Porcupine); nenhum áudio vai ao cérebro nem à nuvem nesse estado. O fluxo, pensado para o celular como interface central e o carro como mero alto-falante:

1. Você diz «Juniper». O app para o detector, abre a sessão de voz com `wake: true` e o cérebro **cumprimenta** (uma frase curta, como hoje).
2. Você pede uma música, álbum ou playlist. Com o microfone **fechado** enquanto ela fala, o cérebro chama `play_music`, que abre o YouTube Music.
3. Ainda de microfone fechado, ela faz um **comentário curto** e, ao terminar, o cérebro manda `voice.end` com `reason: playback`. O app fecha a sessão de voz e **volta à espera** da palavra de chamada. Ela só volta a interagir depois de um novo «Juniper».
4. Se você chamou e não disse nada (ou depois de uma conversa sem música), a sessão encerra sozinha após `voice.wake_idle_seconds` (padrão 15 s) de silêncio e volta à espera.

Em qualquer interação sem fone o microfone fecha enquanto ela fala e reabre quando ela para (ver [acima](#viva-voz)). **Não verificado no aparelho:** com música alta no carro o detector pode falhar em ouvir a palavra; nesse caso use o botão do app.

**Configurar a Picovoice (uma vez, grátis):**
1. Crie a conta em console.picovoice.ai e copie a **AccessKey**.
2. Em «Porcupine», treine a palavra **Juniper** para **Android** e baixe o arquivo `.ppn`.
3. Salve-o como `mobile/android/app/src/main/assets/juniper.ppn` (o `*.ppn` é ignorado pelo git; gere o APK depois).
4. No app, em Configurar, cole a AccessKey em «Chave da Picovoice» (fica no armazenamento seguro do aparelho, nunca no código) e ligue a opção na aba Voz. Sem a chave ou sem o arquivo, o app avisa e a espera não liga.

## Orçamento e voz do aparelho {#orcamento}

Implementado no T-150 (`brain/voice/budget.py` e `speech.py`):

- **Contagem** (`VoiceBudget`): os caracteres enviados ao Google são somados por mês civil, **no fuso da Juniper** (`agent.timezone`), num arquivo JSON pequeno (`voice.usage_file`, permissão 0600). Funciona sem MongoDB e sobrevive a reinício. Arquivo ausente, corrompido ou de outro mês começa o mês em zero; se o disco recusar a escrita, a contagem continua na memória e a conversa não para.
- **Reserva e estorno:** o texto é reservado antes da chamada e devolvido se ela falhar (o Google cobra só o que sintetiza). Se sobrar menos que uma frase, o mês é dado como gasto, para a voz não alternar entre Google e aparelho frase a frase. `monthly_chars_limit: 0` desliga a voz do Google.
- **Cada frase** (`SpeechService.speak`) sai como áudio (`voice.audio`) ou, se o orçamento acabou ou o Google falhou, como texto para o aparelho falar (`voice.say`). Depois de uma falha, o Google fica em paz por 30 s (10 min se o erro foi de cota) antes de outra tentativa.
- **Aviso uma vez:** cada troca de modo vira um único `voice.fallback` (motivo `budget`, `quota` ou `error`) e outro quando o Google volta (`recovered`, por exemplo na virada do mês). Falha inesperada do provedor também cai no aparelho: nenhuma frase fica sem voz.
- **Log:** só tamanhos e códigos, nunca o texto.
- **`juniper voice status`:** (lê o arquivo de gasto do lugar onde o comando roda; na VPS, rode-o dentro do contêiner do cérebro) mostra se a voz está ligada, quanto do mês foi gasto, se a credencial do Google existe e se a chave da Groq está configurada. Nunca imprime segredos.

## A sessão de voz no servidor {#sessao}

Implementada no T-152 (`brain/clients/voice_session.py`, ligada ao `ChatHandler`; `voice.enabled: true` liga o conjunto no servidor):

1. `voice.start` abre a sessão (uma por conexão: a segunda recebe `voice_busy`; sem `voice.enabled` ou sem como ouvir, `voice_unavailable`). O cérebro responde `voice.ready` com os formatos e de onde vêm STT e TTS.
2. O áudio do microfone passa pelo detector de fala. Cada fala é transcrita (nuvem, ou `voice.text` se o aparelho transcreve) e vira um **turno de chat comum**: o agente, a confirmação ([ADR-010](adr-010.html)) e a memória só veem texto. Quem pediu «toca Djavan» recebe `voice.transcript`, `chat.started`, `chat.token` (a legenda) e o áudio.
3. Enquanto o LLM escreve, os tokens viram **frases** que são faladas na ordem (Google ou aparelho, conforme o [orçamento](#orcamento)). Uma frase que já terminou em pontuação é falada depois de 0,4 s de pausa, para ela dizer «Vou procurar» enquanto a tool roda.
4. **Interromper:** falar por cima dela por `barge_in_ms` corta a fala e cancela o turno; a fala nova é transcrita e vira o próximo turno. «Para», «chega» e «silêncio» (ou `voice.interrupt`) calam sem abrir turno.
5. **Encerrar:** botão (`voice.end`), comando falado («encerrar a conversa», «tchau, Juniper») ou `voice.idle_timeout_seconds` de silêncio. Os comandos são reconhecidos **sem o LLM** e só valem para a frase inteira.
6. **Confirmação por voz:** uma pergunta, uma resposta («Posso criar a playlist?» → «sim»); o «sim» é a transcrição seguinte, interpretada por `consent.py`. Nada repete a pergunta.
7. **Pergunta pendente morre com a voz:** «para», «chega», o botão de interromper e qualquer fim de sessão descartam a confirmação que estava aberta; um «sim» dito depois não executa a ação antiga. Terminar por voz também cancela o turno em andamento.
8. **Falhas viram fala:** erro do LLM é dito em voz alta; STT da nuvem fora do ar vira «Não consegui te ouvir agora. Pode repetir?» mais `voice.fallback`.

Estados enviados à tela (`voice.state`): `listening`, `thinking`, `speaking`. O detalhe de como o app descarta o áudio de um turno cortado está no [protocolo](protocolo.html#voz).

## Ligar a voz e testar de verdade {#ligar}

Tudo acima foi verificado com **peças falsas** nas pontas (LLM, Google, Groq, YouTube e um tom sintético no lugar da voz). O que só se confirma com os serviços e o aparelho reais está neste roteiro. Os passos de 1 a 4 não precisam do app.

1. **Conta de serviço do Google** (Text-to-Speech ligado no projeto, com faturamento): baixe o JSON e salve em `certs/gcp-voice.json` (a pasta `certs/` é montada só para leitura; o arquivo nunca entra na imagem nem no Git).
2. **Chave da Groq** em `JUNIPER_GROQ_API_KEY` no `.env`.
3. **Ligue** no seu config (`docker/config.base.yaml` ou o pessoal): `voice: {enabled: true}`. O gasto do mês fica em `voice-usage.json` no volume `juniper-music` (veja [Produção](producao.html)), então sobrevive a deploys.
4. **Confira:** `juniper voice status` deve mostrar «Credencial do Google: encontrada», «Chave da Groq: configurada» e o gasto do mês em zero. Ele nunca imprime chaves.
5. **No aparelho** (quando o app existir, [App mobile](mobile.html)): abrir a voz; dizer «toca Oceano do Djavan» e ouvir a resposta; falar por cima dela no meio de uma frase; dizer «silêncio»; pedir uma playlist e responder «sim»; dizer «tchau, Juniper»; ficar 60 s sem falar.
6. **Ajustes que só o uso mostra:** os limiares do detector (3 vezes o piso para falar, 6 vezes para interromper) e `voice.vad.*`; a qualidade da voz Leda; se o STT da Groq acerta nomes de músicas e artistas.
7. **Custo:** `juniper voice status` mostra quanto do orçamento de caracteres foi usado; ao esgotar, a voz do aparelho assume sozinha e a Juniper avisa uma vez.

O que ainda **não** foi verificado: o formato real das respostas do Google e da Groq, os limites gratuitos atuais da Groq (as fontes citam 20 requisições por minuto e 2.000 por dia, a confirmar no console) e o comportamento do cancelamento de eco em aparelhos reais.

## Mensagens {#protocolo}

Extensão compatível do [protocolo](protocolo.html) (sem mudar `protocol_version`). O áudio vai em quadros de **texto** JSON com base64: `voice.start`, `voice.ready`, `voice.audio`, `voice.state`, `voice.transcript`, `voice.say`, `voice.fallback`, `voice.text`, `voice.interrupt`, `voice.end`; erros `voice_unavailable` e `voice_busy`. A lista completa e os campos estão no [ADR-023](adr-023.html#7-protocolo-extensao-compativel-do-adr-012).

## Tasks {#tasks}

T-148 (protocolo, **pronto**: modelos, `schema.json` e testes), T-149 (texto para fala e sintetizador, **pronto**), T-150 (orçamento e voz do aparelho, **pronto**), T-151 (VAD e STT, **pronto**), T-152 (sessão de voz no servidor, **pronto**), T-153 (documentação e aceite do backend, **pronto**: `tests/test_acceptance_voice.py`). Épica #179.
