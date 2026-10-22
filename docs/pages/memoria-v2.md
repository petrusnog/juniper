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

## A passada noturna {#passada-noturna kind=módulo file=brain/memory/v2/night.py}

O "inconsciente": um job diário, no servidor, que lê o que a Juniper viveu e atualiza as camadas. Roda no mesmo agendador da rotação de logs, em `memory.v2.night.at` (padrão `03:00`, fuso do agente), **só quando a V2 está ligada e pode rodar**, e à mão: `juniper memory dream [--dry-run]`. Na VPS, pelo atalho do contêiner ([Usar a Juniper na VPS](producao.html#usar-na-vps)).

```text
coleta  →  fases (observar, refletir, sentir, personalidade, estudar)  →  aplicação pelo código  →  diário
```

| Peça | Como funciona |
|---|---|
| **Coleta** | Sessões com algo novo (`turns_total > turns_read` na marca d'água), deixadas em paz enquanto **ativas** (último turno há menos de 30 minutos). Lê a **transcrição** (`transcript_turns`), porque a sessão salva é compactada. Mais antigas primeiro, até uma fração do orçamento; uma sessão enorme pode ser cortada no meio e o resto é lido na noite seguinte. |
| **Transcrição** | O `Agent` entrega cada turno terminado a um *sink* (`TranscriptWriter`): o texto do Petrus, a resposta dela e se **houve tool** no turno. Nunca a saída de tool. Se a gravação falhar, a conversa segue (só vai para o log). |
| **Fases** | Objetos com `run(ctx)` (`brain/memory/v2/night.py`). Cada fase vê o que a anterior deixou, pede ao LLM que **proponha** e o código valida e aplica. As fases que **consomem o material** seguram a marca d'água. |
| **Idempotente e retomável** | Sem transações no Mongo, uma noite repetida tem de fazer a coisa certa: a evidência é citada por referência (conta uma vez), e a marca d'água de uma sessão só avança depois que as fases que leem o material terminaram. Noite que falhou é simplesmente lida de novo. Uma fase que falha **não** derruba as outras. |
| **Orçamento** | `memory.v2.night.max_tokens` e `max_minutes`. Estourou, a fase termina com o estado `budget` e o resto fica para a noite seguinte. |
| **Dois modelos** | O barato (o do chat) para a observação; `llm.reflection` (provider e modelo) para refletir e estudar, e sem ele usa o do chat. Um modelo de raciocínio pode devolver o "pensamento" antes da resposta: ele é removido antes de qualquer JSON ser lido. |
| **Registro** | Cada execução grava um `dream_run` (fases, tokens, erros), uma entrada no **diário** (`journal`) e um evento `dream` no [log técnico](observabilidade.html#log-tecnico). |

### Dry run {#dry-run}

`juniper memory dream --dry-run` roda as **fases de verdade** sobre uma camada de sobreposição: tudo que elas escreveriam fica nela, e o banco não é tocado (nem a marca d'água, nem o registro da execução, nem o diário). O relatório mostra o que cada fase propôs e **o que seria gravado**, por coleção. É o instrumento para ganhar confiança antes de ligar a V2 de vez.

```text
Li 2 conversa(s), 9 turno(s) novo(s).
- observe [done]: 3 fatos
Nada foi gravado (--dry-run). O que seria gravado:
    memories: 3 added
```

Sem a V2 ligada, a passada não faz nada, não chama LLM e não abre o banco. Ligar a V2 com `memory.session.store: file` é recusado com uma mensagem clara, nunca derruba o servidor.

## Observação: mensagens longas e janelas do dia {#observacao kind=módulo file=brain/memory/v2/observe.py}

A primeira fase da noite relê o que o dia disse com o **mesmo pipeline da memória v1** (extrair, filtrar segredos, comparar com o que já existe, gravar, atualizar ou substituir), de duas formas que a extração por turno não consegue:

- **Mensagens longas são fatiadas, não cortadas.** O extrator da v1 guarda os primeiros 2000 caracteres de cada mensagem: um texto colado ou um desabafo longo perdia o final. Aqui uma mensagem longa vira fatias de até 1800 caracteres, cortadas no fim de uma frase quando dá (senão num espaço, nunca no meio de uma palavra), com 150 caracteres de sobreposição para uma frase partida ficar inteira numa das fatias. Nada se perde: as fatias cobrem o texto todo.
- **Janelas sobre o dia.** Fatos que só aparecem em vários turnos ("meu irmão chama Caio" … "e ele mora em Recife") ficam mais fáceis de ver com a conversa inteira à vista. Os turnos novos passam pelo extrator em janelas (`memory.extraction.max_turns`, 20), e os 2 últimos turnos da janela anterior vão junto como contexto (já foram lidos, só ajudam a entender). Janelas só de cumprimentos e agradecimentos não custam chamada de LLM.

Regras que valem aqui:

- **O que o LLM lê:** o texto do Petrus e a resposta dela; a resposta é **omitida** nos turnos em que houve tool (ela pode citar o que a tool devolveu). Segredos são mascarados antes de sair e candidatos que parecem segredo, ordem a uma IA ou pergunta são recusados, como na v1.
- **Proveniência:** cada fato guarda a sessão e o turno de onde veio (`source.turn`).
- **Um fato já conhecido** só atualiza a memória existente (`updated_at`); um que contradiz uma ativa a substitui (`superseded`), como na v1.
- **Orçamento:** cada chamada cobra tokens da noite; sem orçamento a fase para e a marca d'água fica onde estava.
- **Memória de longo prazo desligada** (`memory.extraction.enabled: false`): a fase é pulada.
- **Dry run:** o extrator real roda sobre uma **cópia** das memórias (`SnapshotVectorStore`), então o relatório mostra `guardaria: <fato>` e o banco não é tocado.

## Reflexão de crenças {#reflexao kind=módulo file=brain/memory/v2/reflect.py}

A segunda fase da noite usa o modelo avançado (`llm.reflection`) para **propor** crenças; o código valida e aplica. O modelo não decide confiança nem grava nada.

- **O que ele lê:** só as falas do Petrus do dia, rotuladas `S<n>:t<turno>` (segredos mascarados, 300 caracteres por fala, 8000 no total), os fatos novos da observação e as crenças atuais com seus ids.
- **O que propõe (até 12):** `new` (crença nova), `reinforce` ou `weaken` (sobre uma crença existente; `retire` vale como `weaken`). Toda proposta cita as falas que a sustentam.
- **Passo crítico:** uma segunda chamada recebe as hipóteses e aponta, por índice, falas do dia que as contradizem. Essas falas entram como contra-evidência; a mesma fala nunca sustenta e refuta ao mesmo tempo.
- **Validação em código:** evidência que não existe no resumo do dia é recusada (`sem_evidencia`); o texto passa pelas guardas (`check_belief`: sobre o Petrus, sem terceiros, sem rótulo clínico, sem segredo); no máximo `memory.v2.beliefs.max_new_per_night` crenças novas por noite (`limite_noite`). Afirmação explícita do Petrus vale como evidência `explicit` (peso 3).
- **Decaimento:** depois de aplicar as propostas, `review_all` revisa todas as crenças (meia-vida, formação, aposentadoria) e a metacognição conta o acerto ou erro do tema.
- **Relatório:** o resumo traz contagens e os motivos de recusa (só códigos, nunca o texto). No `--dry-run` aparece `proporia (acho que): ...` e nada é gravado.

## Opiniões na conversa {#conversa kind=módulo file=brain/memory/v2/context.py}

Com a V2 ligada, a conversa ganha um bloco `<opinioes>` logo antes da mensagem do Petrus, depois das memórias da v1. Ele não é salvo no histórico e, sem V2 (ou sem nada relevante), o prompt é idêntico ao de antes.

- **O que entra:** até 3 crenças *mantidas* (nunca candidatas ou aposentadas) próximas da mensagem em significado, cada uma com a faixa em palavras ("acho que", "talvez", "tenho bastante certeza"), dentro de `memory.v2.max_tokens`. O texto é limpo: ninguém consegue escrever os delimitadores do bloco.
- **É dado, não ordem:** o cabeçalho diz que são hipóteses dela, que podem estar erradas, e o núcleo da persona manda falar delas como hipótese, discordar só quando a evidência apoia e só mudar de opinião diante de argumento novo. Confirmação de tools e tiers não mudam com o bloco.
- **Pergunta de verificação:** uma hipótese na faixa do meio ("acho que") pode virar uma pergunta curta, **no máximo uma por conversa** e nunca quando a mensagem sugere que o Petrus está mal (`wellbeing.seems_down`). A pergunta só conta como feita se a resposta dela traz mesmo um `?`; senão pode ser oferecida de novo.
- **Feedback na hora:** a mensagem seguinte responde à pergunta. "Sim"/"não" simples (`consent.interpret_reply`) confirmam ou negam com peso 4; respostas longas passam por um julgamento do LLM barato que só devolve sim, não ou incerto, e **o código aplica os pesos**. Resposta ambígua, juiz fora do ar ou outra conversa não gravam nada. A pergunta pendente vive só na memória do processo: não sobrevive a reinício.
- **Falhas:** erro ao ler as crenças devolve o contexto da v1 sem derrubar o turno; enquanto o modelo de embeddings aquece, não há opiniões e ninguém espera.

## Ver, auditar e apagar {#comandos kind=comandos file=brain/clients/memory_v2_cli.py}

Tudo o que a Juniper concluiu é visível e apagável. Os comandos são administrativos, como `memory reindex`: leem o banco do processo onde rodam (na VPS, pelo atalho do contêiner) e não mudam o protocolo. Sem a V2 ligada eles dizem isso em vez de falhar.

| Comando | O que faz |
|---|---|
| `juniper memory beliefs [--all] [--min-confidence X]` | Lista as crenças com a faixa em palavras, a confiança, a firmeza (`n`) e a origem (quantas evidências, em quantas conversas). `--all` inclui candidatas e aposentadas. |
| `juniper memory beliefs show <id>` | Uma crença com as evidências a favor e contra (conversa, turno, data; nunca o texto da fala). |
| `juniper memory beliefs forget <id> [--yes]` | Apaga a crença, pedindo confirmação. Os fatos que a sustentaram **não** são apagados. |
| `juniper memory journal [--days N]` | O diário das noites e dos estudos, dos últimos N dias (7 por padrão). |
| `juniper memory export [--vectors] [-o arquivo]` | Perfil, fatos e, com a V2 ligada, a chave `v2` com crenças, interesses, preocupações, sentimentos, personalidade (atual e histórico), calibração e diário. Sem vetores, a menos que peça. A transcrição bruta não faz parte da exportação. |

**Cascata ao esquecer um fato.** `memory forget` (comando ou tool) apaga o fato e solta, de cada crença, a evidência que veio da mesma fala (ou que apontava para o fato), tirando o peso que ela tinha somado. A crença que fica sem nenhuma evidência a favor é **aposentada**, a menos que o Petrus tenha confirmado ela mesmo; a que ainda tem outras evidências continua, mais fraca. Se a cascata falhar, o fato continua esquecido e o erro vai para o log.
