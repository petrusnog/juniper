---
title: Voz por áudio
group: Documentação Técnica
section: Clientes
order: 25
description: Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do protocolo.
status: pronta
tags: voz audio tts stt google chirp leda groq whisper vad orçamento fallback protocolo voice pcm base64 interrupção encerrar
---

> **Nota:** esta página detalha o [ADR-023](../decisions/ADR-023.md); o backend de voz está implementado e coberto por teste de aceite (`tests/test_acceptance_voice.py`). A [voz do terminal](voz.md) é outra coisa: são as frases escritas que ela diz ao abrir o CLI.

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
- **Confirmação por voz:** uma única pergunta; o "sim" é a transcrição seguinte, interpretada por `consent.py` ([Agente](agente.md#confirmacao)).
- **Encerrar:** botão, comando falado (reconhecido sem LLM) ou silêncio prolongado.
- **A voz não autentica:** a sessão herda a autenticação da conexão ([Login com MFA](login.md)).
- **Dados que saem da máquina:** o áudio do usuário vai à Groq e o texto das respostas ao Google. A política de retenção de cada um precisa ser conferida antes do uso diário.

## Falar: texto preparado e o sintetizador {#falar}

O que o cérebro faz com a resposta antes de ela virar voz (módulo `brain/voice`):

- **Texto para fala** (`speech_text`): tira os sinais de markdown, tabelas e linhas de separação; tira emojis e caracteres invisíveis; **nunca lê URL** (um link vira, uma vez por resposta, "O link está na tela."); um bloco de código vira uma frase só ("Deixei o trecho de código na tela.") e o conteúdo dele não é lido.
- **Frases enquanto o LLM escreve** (`SentenceChunker`): junta os `chat.token` em frases, na ordem, e entrega cada uma assim que fecha, para a primeira ser falada antes de a resposta acabar. Frases com menos de 24 caracteres viajam junto com a seguinte (a entonação não quebra); o ponto de um número decimal ("3.5") não corta a frase; um bloco de código aberto espera fechar. O total falado respeita `voice.max_spoken_chars`: passou disso, o resto fica na tela e ela diz "O resto está na tela." uma vez.
- **Sintetizador** (`Synthesizer`, interface; `GoogleSynthesizer`): uma chamada de `text:synthesize` por frase (textos acima de 450 caracteres são partidos nos espaços), `LINEAR16` na taxa configurada, WAV recebido → PCM sem o cabeçalho (reamostrado se a taxa vier outra). A autenticação é uma **conta de serviço em arquivo montado** (`voice.tts.credentials_file`); o token se renova sozinho e só viaja no cabeçalho `authorization` (o log mascara por nome).
- **Erros tipados** (`VoiceError`, mensagens fixas em PT-BR, sem chave, URL, token nem resposta crua): `not_configured` (credencial ausente, inválida ou recusada), `quota` (limite de uso do Google), `network_error`, `api_error`, `invalid_audio`.

As opções estão em [Configuração](configuracao.md#voice).

## Ouvir: detector de fala e transcrição {#ouvir}

Em `brain/voice/vad.py` e `stt.py`:

- **Detector de fala** (`Vad`): recebe PCM 16 bits, 16 kHz, mono, em pedaços de qualquer tamanho (quadros de 20 ms; sobra de um pedaço entra no seguinte). A fala começa quando a energia fica acima de 3 vezes o piso de ruído por `voice.vad.speech_start_ms`; termina após `silence_ms` de silêncio ou ao chegar em `max_utterance_seconds` (o que continuar sendo dito vira a fala seguinte). Entram na fala os 300 ms anteriores (para não cortar a primeira sílaba). Falas abaixo de `min_utterance_ms` de voz (um estalo, uma tosse) são descartadas. O piso de ruído acompanha o ambiente: desce rápido e sobe devagar, então uma sala barulhenta deixa de contar como fala, mas a voz continua se destacando.
- **Interromper** (`set_playing(True)` enquanto ela fala): o limiar sobe (6 vezes o piso, no mínimo 900) e são exigidos `barge_in_ms` de fala contínua. Um sopro ou a própria voz dela vazando no microfone não a corta. O evento é `barge_in`, no lugar de `speech_start`; a fala segue sendo gravada.
- **Transcrição** (`GroqTranscriber`): cada fala vai como WAV mono para `/audio/transcriptions` (`whisper-large-v3-turbo`, `pt`, temperatura 0) com a chave `JUNIPER_GROQ_API_KEY` (cabeçalho `authorization`, que o log mascara). Erros tipados, sem a resposta crua do provedor: 429 vira `quota` (com o `retry-after`, se for razoável), 401/403 `not_configured`, 400/413/422 `invalid_audio`, o resto `api_error`/`network_error`. Fala acima de 24 MB nem é enviada.
- **Alucinações** (`is_hallucination`): texto vazio, frases que o Whisper inventa no silêncio ("Legendas pela comunidade Amara.org", "Obrigado por assistir"...) e a mesma palavra repetida são descartados antes de virar turno. Um "obrigado" sozinho também cai; dentro de uma frase real ("obrigado, Juniper, toca uma música") ele passa.
- **Cota e volta** (`SpeechToText`): ao bater no limite da Groq, o cérebro manda `voice.fallback` (`stt` → `local`), fica quieto pelo `retry-after` (ou `voice.stt.cooldown_seconds`) e o app passa a transcrever sozinho (`voice.text`). A fala que estourou o limite se perde (o app não guarda o áudio): a sessão pede para repetir. Como o app para de enviar áudio, é o cérebro que avisa quando vale tentar de novo (`recovered()`); se falhar outra vez, volta ao aparelho com novo aviso. Só tamanhos vão ao log, nunca as palavras.

## Viva-voz, fone e música tocando {#viva-voz}

Fora de um fone com fio, a voz sai por um alto-falante (o do carro por Bluetooth ou o do celular) e o microfone ouve a própria Juniper. O que existe contra o laço de «ela se ouve e responde a si mesma»:

1. **Para onde a voz vai (nunca o auricular):** a voz da Juniper **nunca** sai pelo auricular (o alto-falante encostado no ouvido). A ordem é: dispositivo Bluetooth de áudio (o som do carro), depois fones com fio ou USB, depois o alto-falante do próprio celular; o auricular nem é candidato. O app também vigia o destino do áudio e, se o sistema o mandar para o auricular, devolve na hora. O Bluetooth conta como alto-falante (costuma ser o carro): o microfone o ouviria, então vale o meio-duplex abaixo. Só fones com fio ou USB contam como «fone». **Não verificado no aparelho** (Kotlin novo, nunca compilado).
2. **Volume sobre outras mídias:** enquanto ela fala, o app pede o foco de áudio do Android em modo «pode abaixar» (a música ou o vídeo tocando baixam o volume) e o devolve cerca de 1 s depois que ela para. O app **não** mexe no volume geral do celular nem do carro: se ela ficar baixa, é o volume de mídia do aparelho ou do carro.
3. **Meio-duplex em toda interação sem fone** ([protocolo](protocolo.md#voz)): enquanto ela fala (e por 1 s depois), o microfone fica **fechado**: o cérebro ignora o que chega e o app nem envia. A voz dela, o eco e a música dessa janela nunca viram fala. O preço: **não dá para interromper falando por cima**, só pelo botão «Parar de falar». Só com fone (`route: headset`) o corte por voz continua.
4. **«Segurar para falar»** (botão na aba Voz): o microfone só vai ao cérebro enquanto o botão está apertado (e ao soltar vão 900 ms de silêncio para o cérebro fechar a fala). É a saída segura quando há **música tocando no celular**: ela não é interpretada, porque o microfone só abre quando você quer. Apertar o botão enquanto ela fala também a interrompe.

O que **não** dá para garantir sem fone: falar por cima de uma música alta sem apertar o botão. O Android cancela o eco da voz da Juniper, não o de outro app; sem o botão, a música pode ser tomada por fala (e a transcrição de letra de música não é filtrada). Por isso: fone, ou «Segurar para falar».

## Mãos livres: abrir a voz com um toque {#maos-livres}

O celular é a interface central e o carro é só o alto-falante. Para abrir a conversa sem procurar nada na tela, o app aceita o link **`juniper://voz`**, que traz o app para a frente e abre a sessão de voz já no modo mãos livres (`wake: true`, [protocolo](protocolo.md#voz)). Três jeitos de chegar a ele:

1. **Botão «Falar» na notificação:** na aba Voz, ligue «Botão «Falar» na notificação». Fica uma notificação fixa com o botão, sem serviço e sem microfone aberto. O Android pede a permissão de notificações na primeira vez.
2. **Atalho do ícone:** segure o ícone da Juniper e toque em «Falar». Arraste-o para a tela inicial para ter um botão de um toque.
3. **Pelo Google:** «Ok Google, abre o app Juniper» abre o app, mas **não** inicia a conversa (isso é o Google, não o link). Para falar a frase inteira é preciso uma rotina do Google que abra o link `juniper://voz`. **Não verificado** no Android atual, em que o Gemini está substituindo o Assistente: teste no aparelho.

O fluxo depois do toque:

1. O app abre a sessão com `wake: true` e o cérebro **cumprimenta** (uma frase curta). Se a conexão ainda está subindo (app aberto do zero), o app espera até 8 s por ela.
2. Você pede uma música, álbum ou playlist. O microfone fica **fechado** enquanto ela fala; o cérebro chama `play_music`, que abre o YouTube Music.
3. Ainda de microfone fechado, ela faz um **comentário curto** e o cérebro manda `voice.end` com `reason: playback`. O app fecha a conversa e a música segue tocando; ela só volta a ouvir num novo toque.
4. Se você não disser nada, ou depois de uma conversa sem música, a sessão encerra sozinha após `voice.wake_idle_seconds` (padrão 15 s) de silêncio.

Em qualquer interação sem fone o microfone fecha enquanto ela fala e reabre quando ela para (ver [acima](#viva-voz)).

**Backlog: palavra falada «Juniper».** Detectar a palavra de chamada no aparelho (sem enviar áudio ao cérebro) fica para depois. O plano é um detector local sem conta nem custo (openWakeWord) com um modelo da palavra «Juniper» treinado no Google Colab (cerca de 1 a 3 h de espera, feito à mão, e a precisão com música alta no carro só se vê no aparelho), mais um módulo Kotlin que o carregue. A interface `WakeWordDetector` (`mobile/src/wake/`) já está reservada para ele, e o toque continua como reserva. A Picovoice foi descartada por incerteza de preço.

## Orçamento e voz do aparelho {#orcamento}

Em `brain/voice/budget.py` e `speech.py`:

- **Contagem** (`VoiceBudget`): os caracteres enviados ao Google são somados por mês civil, **no fuso da Juniper** (`agent.timezone`), num arquivo JSON pequeno (`voice.usage_file`, permissão 0600). Funciona sem MongoDB e sobrevive a reinício. Arquivo ausente, corrompido ou de outro mês começa o mês em zero; se o disco recusar a escrita, a contagem continua na memória e a conversa não para.
- **Reserva e estorno:** o texto é reservado antes da chamada e devolvido se ela falhar (o Google cobra só o que sintetiza). Se sobrar menos que uma frase, o mês é dado como gasto, para a voz não alternar entre Google e aparelho frase a frase. `monthly_chars_limit: 0` desliga a voz do Google.
- **Cada frase** (`SpeechService.speak`) sai como áudio (`voice.audio`) ou, se o orçamento acabou ou o Google falhou, como texto para o aparelho falar (`voice.say`). Depois de uma falha, o Google fica em paz por 30 s (10 min se o erro foi de cota) antes de outra tentativa.
- **Aviso uma vez:** cada troca de modo vira um único `voice.fallback` (motivo `budget`, `quota` ou `error`) e outro quando o Google volta (`recovered`, por exemplo na virada do mês). Falha inesperada do provedor também cai no aparelho: nenhuma frase fica sem voz.
- **Log:** só tamanhos e códigos, nunca o texto.
- **`juniper voice status`:** (lê o arquivo de gasto do lugar onde o comando roda; na VPS, rode-o dentro do contêiner do cérebro) mostra se a voz está ligada, quanto do mês foi gasto, se a credencial do Google existe e se a chave da Groq está configurada. Nunca imprime segredos.

## A sessão de voz no servidor {#sessao}

Em `brain/clients/voice_session.py`, ligada ao `ChatHandler` (`voice.enabled: true` liga o conjunto no servidor):

1. `voice.start` abre a sessão (uma por conexão: a segunda recebe `voice_busy`; sem `voice.enabled` ou sem como ouvir, `voice_unavailable`). O cérebro responde `voice.ready` com os formatos e de onde vêm STT e TTS.
2. O áudio do microfone passa pelo detector de fala. Cada fala é transcrita (nuvem, ou `voice.text` se o aparelho transcreve) e vira um **turno de chat comum**: o agente, a confirmação ([ADR-010](../decisions/ADR-010.md)) e a memória só veem texto. Quem pediu «toca Djavan» recebe `voice.transcript`, `chat.started`, `chat.token` (a legenda) e o áudio.
3. Enquanto o LLM escreve, os tokens viram **frases** que são faladas na ordem (Google ou aparelho, conforme o [orçamento](#orcamento)). Uma frase que já terminou em pontuação é falada depois de 0,4 s de pausa, para ela dizer «Vou procurar» enquanto a tool roda.
4. **Interromper:** falar por cima dela por `barge_in_ms` corta a fala e cancela o turno; a fala nova é transcrita e vira o próximo turno. **O que você disse no turno cortado não se perde:** a mensagem fica no histórico da conversa e na transcrição (sem resposta) e vai para a extração de memórias; só a resposta pela metade e as tools daquele turno são descartadas. Uma fala longa que o detector divide em pedaços (`voice.max_utterance_seconds`) fica inteira por isso. Só não chegam ao histórico as falas que nunca viraram texto: fila de áudio cheia (`utterance_dropped` no log) e transcrição sem texto. «Para», «chega» e «silêncio» (ou `voice.interrupt`) calam sem abrir turno.
5. **Encerrar:** botão (`voice.end`), comando falado («encerrar a conversa», «tchau, Juniper») ou `voice.idle_timeout_seconds` de silêncio. Os comandos são reconhecidos **sem o LLM** e só valem para a frase inteira. Há também **«isso vazou»** (e variações: «marcar vazamento», «registrar vazamento»): ela corta a fala, guarda um relato das últimas 10 interações e confirma em uma frase, sem abrir turno ([Auditoria de vazamentos](vazamentos.md#relato)).
6. **Confirmação por voz:** uma pergunta, uma resposta («Posso criar a playlist?» → «sim»); o «sim» é a transcrição seguinte, interpretada por `consent.py`. Nada repete a pergunta.
7. **Pergunta pendente morre com a voz:** «para», «chega», o botão de interromper e qualquer fim de sessão descartam a confirmação que estava aberta; um «sim» dito depois não executa a ação antiga. Terminar por voz também cancela o turno em andamento.
8. **Falhas viram fala:** erro do LLM é dito em voz alta; STT da nuvem fora do ar vira «Não consegui te ouvir agora. Pode repetir?» mais `voice.fallback`.

Estados enviados à tela (`voice.state`): `listening`, `thinking`, `speaking`. O detalhe de como o app descarta o áudio de um turno cortado está no [protocolo](protocolo.md#voz).

## Ligar a voz e testar de verdade {#ligar}

Tudo acima foi verificado com **peças falsas** nas pontas (LLM, Google, Groq, YouTube e um tom sintético no lugar da voz). O que só se confirma com os serviços e o aparelho reais está neste roteiro. Os passos de 1 a 4 não precisam do app.

1. **Conta de serviço do Google** (Text-to-Speech ligado no projeto, com faturamento): baixe o JSON e salve em `certs/gcp-voice.json` (a pasta `certs/` é montada só para leitura; o arquivo nunca entra na imagem nem no Git).
2. **Chave da Groq** em `JUNIPER_GROQ_API_KEY` no `.env`.
3. **Ligue** no config do servidor (`voice: {enabled: true}`) ou no `.env` (`JUNIPER_VOICE__ENABLED=true`). Em produção o gasto do mês fica em `voice-usage.json` no volume `juniper-music`, então sobrevive a deploys ([Integrações Google Cloud e Groq](integracoes-producao.md#voz)).
4. **Confira:** `juniper voice status` deve mostrar «Credencial do Google: encontrada», «Chave da Groq: configurada» e o gasto do mês em zero. Ele nunca imprime chaves.
5. **No aparelho** ([App mobile](mobile.md#voz)): abrir a voz; dizer «toca Oceano do Djavan» e ouvir a resposta; falar por cima dela no meio de uma frase; dizer «silêncio»; pedir uma playlist e responder «sim»; dizer «tchau, Juniper»; ficar 60 s sem falar.
6. **Ajustes que só o uso mostra:** os limiares do detector (3 vezes o piso para falar, 6 vezes para interromper) e `voice.vad.*`; a qualidade da voz Leda; se o STT da Groq acerta nomes de músicas e artistas.
7. **Custo:** `juniper voice status` mostra quanto do orçamento de caracteres foi usado; ao esgotar, a voz do aparelho assume sozinha e a Juniper avisa uma vez.

O que ainda **não** foi verificado: o formato real das respostas do Google e da Groq, os limites gratuitos atuais da Groq (as fontes citam 20 requisições por minuto e 2.000 por dia, a confirmar no console) e o comportamento do cancelamento de eco em aparelhos reais.

## Mensagens {#protocolo}

Extensão compatível do [protocolo](protocolo.md) (sem mudar `protocol_version`). O áudio vai em quadros de **texto** JSON com base64: `voice.start`, `voice.ready`, `voice.audio`, `voice.state`, `voice.transcript`, `voice.say`, `voice.fallback`, `voice.text`, `voice.interrupt`, `voice.end`; erros `voice_unavailable` e `voice_busy`. A lista completa e os campos estão no [ADR-023](../decisions/ADR-023.md#7-protocolo-extensao-compativel-do-adr-012).

## Para saber mais {#para-saber-mais}

- [Onde conversar](produto-canais.md) (Produto): Os canais da Juniper (terminal, Telegram, app Android com texto e voz, painel web) e o que cada um faz de melhor.
- [Voz no terminal](voz.md) (Documentação Técnica): Como ela fala: os prefixos, fala fixa versus gerada por IA, e a regra para comandos novos.
- [Bot do Telegram](telegram.md) (Documentação Técnica): O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
