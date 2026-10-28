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

## Mensagens {#protocolo}

Extensão compatível do [protocolo](protocolo.html) (sem mudar `protocol_version`). O áudio vai em quadros de **texto** JSON com base64: `voice.start`, `voice.ready`, `voice.audio`, `voice.state`, `voice.transcript`, `voice.say`, `voice.fallback`, `voice.text`, `voice.interrupt`, `voice.end`; erros `voice_unavailable` e `voice_busy`. A lista completa e os campos estão no [ADR-023](adr-023.html#7-protocolo-extensao-compativel-do-adr-012).

## Tasks {#tasks}

T-148 (protocolo), T-149 (texto para fala e sintetizador), T-150 (orçamento), T-151 (VAD e STT), T-152 (sessão de voz no servidor), T-153 (documentação e aceite). Épica #179.
