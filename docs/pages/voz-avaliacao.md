---
title: Avaliar a voz
group: Documentação Técnica
section: Clientes
order: 28
description: Como medir a escuta da Juniper antes e depois de uma mudança: o conjunto de frases gravadas e seu roteiro, o script que calcula WER, CER, termos próprios e latência, e os eventos de latência e de corte da fala no log.
status: pronta
tags: voz stt avaliação wer cer latência baseline linha de base roteiro gravação termos groq whisper truncado fallback stt-v2 adr-034
---

> **Nota:** esta página nasce do [ADR-034](../decisions/ADR-034.md) (seção 9: «linha de base antes das alterações»). Código: `brain/voice/evaluation.py` e `scripts/voice_eval.py`; os eventos saem de `brain/clients/voice_session.py`. Como a voz funciona é assunto de [Voz por áudio](voz-audio.md).

## Visão geral {#visao-geral}

Dizer que a escuta «melhorou» sem medir é opinião. Há duas medidas, e elas respondem a perguntas diferentes:

| Medida | Responde | De onde vem |
|---|---|---|
| **Conjunto gravado** | O texto saiu certo? Quantas palavras erradas, em quais frases, quão rápido? | `scripts/voice_eval.py`, sob demanda |
| **Eventos da sessão** | Quanto tempo o turno leva do fim da fala ao primeiro som? Com que frequência a fala é cortada? | O log de eventos, em uso real |

O que cada uma **não** diz: o conjunto gravado mede a transcrição, não a conversa inteira (o LLM e a voz não entram); os eventos dizem onde o tempo foi, não se o texto saiu certo.

## O conjunto de frases {#conjunto kind=conceito file=brain/voice/evaluation.py}

Uma pasta com um par por frase: `nome.wav` (mono, 16 bits, qualquer taxa; é reamostrado para 16 kHz, que é o que a sessão de voz envia ao STT) e `nome.txt` com **exatamente** o que foi dito. Um `termos.txt` opcional lista os nomes próprios e termos técnicos (um por linha; `#` abre comentário). Sem o `.txt` de uma gravação, a avaliação para e diz qual faltou.

**O conjunto não vai para o git.** Tem a sua voz e o que você disse; mantenha-o fora do repositório (por exemplo em `~/juniper-voz/conjunto`). O relatório JSON também repete as frases.

### Roteiro de gravação {#roteiro}

Cerca de 60 frases. Dê nomes que ordenem por categoria (`01-comando-hora.wav`). Use as suas próprias palavras onde a lista dá exemplos.

| Categoria | Quantas | Exemplos |
|---|---|---|
| Comandos curtos | 10 | «Juniper, que horas são?», «Para.», «Toca Djavan.» |
| Frases longas | 10 | Pedidos de duas ou três orações, com pausa no meio |
| Nomes próprios e termos seus | 15 | «Abra o projeto Media Works.», nomes de pessoas, lugares, ferramentas que você usa |
| Números, datas e horas | 5 | «Marca dentista dia 14 às 15h30.» |
| Ambíguas | 5 | Palavras que soam parecido: «sem»/«cem», «acento»/«assento» |
| Com ruído | 10 | Repetir frases de cima com TV, ventilador ou rua ao fundo |
| Distância | 5 | As mesmas frases a 1 m e a 3 m do microfone |

Se houver mais de uma pessoa, grave cada uma numa subcategoria (`ana-`, `petrus-`): sotaque e velocidade mudam muito o resultado. Para converter uma gravação do celular:

```bash
ffmpeg -i entrada.m4a -ar 16000 -ac 1 -c:a pcm_s16le saida.wav
```

Grave **sempre do mesmo jeito** (mesmo aparelho e mesma distância) entre a medição de antes e a de depois; senão a diferença é do microfone, não da mudança. Gravar pelo próprio app é o mais fiel, porque ele já passa pelo cancelamento de eco e de ruído do Android.

## Rodar a avaliação {#rodar kind=comando file=scripts/voice_eval.py}

Usa a chave da Groq que o cérebro já usa (`JUNIPER_GROQ_API_KEY`, no `.env`); ela nunca é impressa.

```bash
python scripts/voice_eval.py ~/juniper-voz/conjunto --json antes.json
```

Depois de uma mudança, rode de novo e compare:

```bash
python scripts/voice_eval.py ~/juniper-voz/conjunto --json depois.json --compare antes.json
```

O script espera 3,2 s entre as chamadas (`--pause`) para respeitar o limite do plano gratuito da Groq, cerca de 20 por minuto; 60 frases levam uns 4 minutos. `--model` e `--language` trocam o modelo e o idioma só para essa rodada.

### O que o relatório diz {#relatorio}

- **WER** e **CER**: erros de palavra e de caractere sobre o que foi dito, somados no conjunto inteiro (uma frase longa pesa mais que uma curta). A comparação ignora maiúsculas, acentos e pontuação: mede *quais palavras* foram ouvidas, não vírgulas.
- **Termos próprios errados**: das vezes em que um termo do `termos.txt` foi dito, quantas saíram erradas. É o número que o glossário da T-205 deve mover.
- **Latência da transcrição**: média, mediana e p95 da chamada ao STT.
- **Falhas** (erro tipado da Groq, como o limite de uso) e **descartadas** (a resposta que o filtro de alucinação do Whisper jogaria fora). Uma falha diz que a chamada não funcionou, não que a palavra foi mal ouvida: a frase fica **fora** de WER, CER e termos, o relatório avisa e o script sai com código 1. Uma rodada com falhas não vale como linha de base; repita (talvez com `--pause` maior). A avaliação segue até o fim mesmo assim.
- As frases com erro, as piores primeiro, com o que foi dito e o que foi ouvido.

## Eventos da sessão {#eventos kind=evento file=brain/clients/voice_session.py}

Dois eventos `voice` (veja a tabela em [Observabilidade](observabilidade.md#eventos)). Nenhum leva texto falado nem áudio, só tempos e tamanhos.

| `action` | Quando | Campos |
|---|---|---|
| `latency` | No primeiro som de cada turno falado | `stt_ms`, `think_ms`, `speak_ms`, `total_ms`, `silence_ms` |
| `truncated` | A resposta passou do limite e o corte de segurança disse «O resto está na tela.» | `spoken_chars`, `max_chars` |

O caminho do `latency`: o VAD detecta a pausa que fechou a fala → o STT devolve o texto (`stt_ms`) → o modelo escreve o primeiro pedaço (`think_ms`) → a primeira frase vira som (`speak_ms`). `total_ms` é a soma, contada **a partir da pausa detectada**. A pessoa espera também essa pausa, `silence_ms` (700 ms na configuração padrão), então o tempo que ela sente é `silence_ms + total_ms`. Quando o aparelho transcreve sozinho (`voice.text`), o cérebro não ouviu o áudio: o evento traz só `think_ms` e `speak_ms`.

O `truncated` existe para medir o corte de segurança do limite de fala (`voice.max_spoken_chars`): ele deve ficar raro. Cada ocorrência é uma resposta que o modelo escreveu maior do que podia ser falada.

Medianas do dia, com o `jq` (veja [Observabilidade](observabilidade.md#log-tecnico) para `juniper logs`):

```bash
juniper logs --json -e voice --days 7 \
  | jq -s '[.[] | select(.action=="latency") | .total_ms] | sort | .[length/2|floor]'
juniper logs --json -e voice --days 7 | jq -s '[.[] | select(.action=="truncated")] | length'
```

## Linha de base {#linha-de-base}

**Ainda não medida.** Depende do conjunto gravado e da chave da Groq, que não existiam quando o harness foi escrito; fica na task T-216. Até lá, nenhum número desta página é resultado: o que existe é o harness e a instrumentação, testados com um transcritor falso e áudio sintético.

| Medida | Antes (T-216) | Depois |
|---|---|---|
| WER | a medir | |
| CER | a medir | |
| Termos próprios errados | a medir | |
| Latência do STT (mediana / p95) | a medir | |
| `total_ms` do turno (mediana) | a medir | |
| Respostas cortadas por 100 turnos | a medir | |

## O que foi verificado {#verificado}

| Como | O quê |
|---|---|
| **Automatizado** (`tests/test_voice_eval.py`, `tests/test_voice_session.py`) | WER, CER, termos e percentis em casos de resposta conhecida; leitura do conjunto e erros claros quando falta algo; falha da Groq e alucinação tratadas sem parar a rodada; os eventos `latency` e `truncated` na sessão de voz, sem texto falado |
| **Simulado** | Nada: o Whisper real nunca rodou neste harness |
| **Com voz e microfone reais** | Pendente: gravar o conjunto e rodar a avaliação (T-216) |

## Para saber mais {#para-saber-mais}

- [Voz por áudio](voz-audio.md) (Documentação Técnica): como a voz funciona hoje: STT, VAD, TTS, orçamento e as mensagens do protocolo.
- [Observabilidade](observabilidade.md) (Documentação Técnica): o log de eventos, `juniper logs` e a tabela de eventos.
- [App mobile](mobile.md) (Documentação Técnica): o cliente de voz no Android, onde a gravação mais fiel é feita.
- [Configuração](configuracao.md) (Referência): `voice.stt`, `voice.vad` e `voice.max_spoken_chars`.
