---
title: Memória V2
group: Memória
order: 4
description: A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com a passada noturna.
status: proposto
tags: memória v2 umwelt crenças confiança evidência alpha beta decaimento metacognição inconsciente passada noturna camadas personalidade sentimentos interesses adr-017
---

> **Nota:** esta página acompanha a [ADR-017](adr-017.html), que é a fonte de verdade. A Memória V2 vem **desligada** (`memory.v2.enabled: false`); desligada, nada muda em relação à [memória de longo prazo](memoria-longo-prazo.html). As camadas vão sendo entregues em tasks; cada seção abaixo diz o que já está pronto.

## Visão geral {#visao-geral}

A memória v1 guarda **fatos**. A V2 acrescenta camadas em que cada uma é uma **leitura da anterior**, e a personalidade deixa de ser um texto fixo.

```text
0. NÚCLEO (fixo)   regras de tools, confirmação, segurança, formato, honestidade
1. MUNDO           fatos que ela identificou (a memória v1)
2. UMWELT          interpretações: crenças, hipóteses, opiniões e interesses
3. PREOCUPAÇÕES    o que importa para ela (a régua dos sentimentos)
4. AFETO           sentimentos e humor
5. PERSONALIDADE   traços e narrativa recente (viva)
```

Tudo mora no **MongoDB** (o mesmo banco da v1), em coleções por camada; só o núcleo é arquivo (`prompts/persona.core.md`). A V2 exige `memory.session.store: mongo`: com arquivos, ligar `memory.v2.enabled` é recusado com uma mensagem clara. O código está em `brain/memory/v2/`.

## Base de dados {#base}

| Coleção | O que guarda |
|---|---|
| `memories` | Os fatos (a v1). Cada um agora cita a sessão e o turno de origem (`source.turn`). |
| `beliefs` | Crenças com `alpha`, `beta`, evidências e status. |
| `interests`, `affects`, `concerns` | Interesses, sentimentos e preocupações. |
| `persona_state`, `persona_history` | A personalidade atual (um documento versionado) e as versões anteriores. |
| `journal`, `dream_runs`, `dream_watermarks` | O diário, as execuções da passada noturna e até onde cada sessão foi lida. |
| `transcript_turns` | A **transcrição completa** de cada turno (o que o Petrus escreveu e a resposta dela). A sessão salva é *compactada* (só os últimos turnos), então a passada noturna precisa desta. Nunca tem saída de tool. |
| `calibration` | Quanto as opiniões dela acertaram em cada tema. |

O armazenamento é uma interface (`DocStore`) com duas implementações, MongoDB e memória, que passam nos mesmos testes. O MongoDB do compose é um nó único e não tem transações entre documentos: as operações são **idempotentes e retomáveis**, e a personalidade usa versão otimista. Um documento corrompido é ignorado ao listar, sem esconder os bons. `juniper memory reindex` refaz também os vetores de crenças e interesses.

## Crenças e confiança {#crencas kind=conceito file=brain/memory/v2/confidence.py}

Uma **crença** é uma hipótese em primeira pessoa sobre o Petrus ou sobre uma decisão dele ("acho que o Petrus se cobra bem mais do que cobraria de outra pessoa; vi isso em 3 conversas"). A confiança **sai de contagens, nunca de um número que o LLM inventa**: um modelo que diz "80% de certeza" produz um número bonito e não calibrado.

Cada crença nasce com um a priori fraco (`alpha = beta = 1`, "não sei") e cada evidência soma um peso ao lado que apoia, conforme a **fonte**:

| Evidência | Peso | Lado |
|---|---|---|
| Observação indireta num turno (ela inferiu) | 1 | a favor (`alpha`) |
| Observação contrária | 1 | contra (`beta`) |
| O Petrus disse explicitamente | 3 | a favor |
| O Petrus **confirmou** a hipótese quando ela perguntou | 4 | a favor |
| O Petrus **negou** | 4 | contra |

- **Confiança** = `alpha / (alpha + beta)`; **firmeza** `n` = a evidência acumulada além do a priori.
- **Decaimento:** `alpha` e `beta` encolhem na direção do a priori com meia-vida de 60 dias (`memory.v2.beliefs.half_life_days`). Sem reforço, a crença volta à **incerteza**, não ao erro.
- **Formação:** uma observação isolada é um palpite, não uma crença. A hipótese (`candidate`) vira `active` com pelo menos `min_evidence` (2) de evidência a favor vinda de **sessões ou dias diferentes**, ou com uma afirmação explícita ou confirmação do Petrus.
- **Em palavras** (é o que o Petrus lê e o que vai ao prompt; o número fica por baixo): "talvez" até 0,45; "acho que" de 0,45 a 0,70; "tenho bastante certeza" acima de 0,70 **e** `n >= 6`. Com pouca evidência nunca passa de "acho que".
- **`retired`** (final): o Petrus negou; ou a confiança caiu abaixo de 0,25 com `n >= 4` (ela estava errada); ou a crença desbotou (`n < 0,25`) sem confirmação.
- A **mesma referência** (sessão, turno) conta **uma vez**: reexecutar a passada noturna não infla nada. As referências guardadas são no máximo 50 por lado; os pesos continuam nas contagens.

Os valores são um primeiro palpite, ajustáveis em `memory.v2.beliefs.*`.

### Metacognição {#metacognicao}

A Juniper acompanha quando as próprias opiniões se mostram certas ou erradas, por tema: confirmar é um acerto; uma crença que ela **mantinha** e foi negada ou refutada é um erro (um mero palpite descartado, e uma crença que só desbotou, não contam). Num tema em que ela erra muito, uma crença nova **começa mais cética** (o a priori pende para o lado "errada"); um bom histórico não faz crença nova começar mais confiante. É humildade na conta, não só no tom.

### BeliefBook {#beliefbook kind=classe file=brain/memory/v2/beliefs.py}

O único lugar que cria, reforça, confirma, nega, revisa e aposenta crenças. O LLM pode *propor* (a reflexão noturna), mas todo número e toda mudança de status vêm de `confidence.py`. `create` junta o palpite a uma crença viva quase igual (similaridade 0,9 ou mais) em vez de duplicá-la; `review_all` aplica o decaimento, forma as que merecem e aposenta as erradas ou apagadas pelo tempo; `search` acha as crenças mantidas por significado.

## Guardas de conteúdo {#guardas kind=módulo file=brain/memory/v2/guards.py}

O LLM **propõe**; o código **dispõe**. Toda proposta da passada noturna passa por aqui antes de virar dado, e a recusa guarda só o **motivo** (um código curto), nunca o texto recusado.

| Regra | Como é garantida |
|---|---|
| **Só sobre o Petrus e as decisões dele** | `about` é `user` ou `decision`; o texto precisa se referir ao Petrus (o nome do perfil, "o usuário", "ele", "você") e **nenhum outro nome próprio** pode vir antes. "Acho que a Júlia é manipuladora" é recusada; "acho que o Petrus se afasta da Júlia quando está cansado" passa. |
| **Nada clínico** | Diagnósticos e rótulos (depressão, TDAH, transtorno, bipolar, burnout, narcisista...) são recusados; comportamento observável ("fica ansioso antes de apresentações") passa. |
| **Primeira pessoa** | A crença começa na voz dela ("acho que", "percebo que", "me parece que", "pelo que vi"). |
| **Sem segredos, ordens a uma IA, documentos, perguntas** | Os mesmos filtros da memória v1; até 400 caracteres. |
| **Interesses** | Um tema curto e geral: sem dado pessoal, sem endereço ou e-mail, sem o nome do Petrus. |
| **Sentimentos** | Rótulo curto, valência de -1 a 1, intensidade de 0 a 1 e **causa referenciável** (`fact:`, `belief:`, `concern:`...). |
| **Traços da personalidade** | Só traços que existem, dentro da faixa e no passo máximo por noite. |

Os guardas são heurísticas: erram para o lado de **recusar** (um falso positivo custa uma hipótese; um falso negativo custaria uma opinião indevida).

### O material da noite {#material}

A passada noturna só lê o que o Petrus **escreveu** e as respostas dela, nunca a saída de uma tool (a transcrição não a guarda). E mais: se **houve tool no turno**, a resposta dela é **omitida** do material, porque pode citar o que a tool devolveu (o título de um evento da agenda, uma mensagem de commit). Assim, um texto plantado num título de evento não chega às camadas subjetivas. Segredos são mascarados e cada linha leva uma etiqueta `[t<turno>]`, que é a referência que as crenças citam como evidência.
