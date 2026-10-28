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

## Orçamento e voz do aparelho {#orcamento}

Implementado no T-150 (`brain/voice/budget.py` e `speech.py`):

- **Contagem** (`VoiceBudget`): os caracteres enviados ao Google são somados por mês civil, **no fuso da Juniper** (`agent.timezone`), num arquivo JSON pequeno (`voice.usage_file`, permissão 0600). Funciona sem MongoDB e sobrevive a reinício. Arquivo ausente, corrompido ou de outro mês começa o mês em zero; se o disco recusar a escrita, a contagem continua na memória e a conversa não para.
- **Reserva e estorno:** o texto é reservado antes da chamada e devolvido se ela falhar (o Google cobra só o que sintetiza). Se sobrar menos que uma frase, o mês é dado como gasto, para a voz não alternar entre Google e aparelho frase a frase. `monthly_chars_limit: 0` desliga a voz do Google.
- **Cada frase** (`SpeechService.speak`) sai como áudio (`voice.audio`) ou, se o orçamento acabou ou o Google falhou, como texto para o aparelho falar (`voice.say`). Depois de uma falha, o Google fica em paz por 30 s (10 min se o erro foi de cota) antes de outra tentativa.
- **Aviso uma vez:** cada troca de modo vira um único `voice.fallback` (motivo `budget`, `quota` ou `error`) e outro quando o Google volta (`recovered`, por exemplo na virada do mês). Falha inesperada do provedor também cai no aparelho: nenhuma frase fica sem voz.
- **Log:** só tamanhos e códigos, nunca o texto.
- **`juniper voice status`:** mostra se a voz está ligada, quanto do mês foi gasto, se a credencial do Google existe e se a chave da Groq está configurada. Nunca imprime segredos.

## Mensagens {#protocolo}

Extensão compatível do [protocolo](protocolo.html) (sem mudar `protocol_version`). O áudio vai em quadros de **texto** JSON com base64: `voice.start`, `voice.ready`, `voice.audio`, `voice.state`, `voice.transcript`, `voice.say`, `voice.fallback`, `voice.text`, `voice.interrupt`, `voice.end`; erros `voice_unavailable` e `voice_busy`. A lista completa e os campos estão no [ADR-023](adr-023.html#7-protocolo-extensao-compativel-do-adr-012).

## Tasks {#tasks}

T-148 (protocolo, **pronto**: modelos, `schema.json` e testes), T-149 (texto para fala e sintetizador, **pronto**), T-150 (orçamento e voz do aparelho, **pronto**), T-151 (VAD e STT, **pronto**), T-152 (sessão de voz no servidor), T-153 (documentação e aceite). Épica #179.
