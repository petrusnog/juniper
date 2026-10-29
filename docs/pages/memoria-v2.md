---
title: Memória V2
group: Documentação Técnica
section: Memória
order: 13
description: A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com o algoritmo de sonho.
status: pronta
tags: memória v2 umwelt crenças confiança evidência alpha beta decaimento metacognição inconsciente algoritmo de sonho camadas personalidade sentimentos interesses adr-017
---

> **Nota:** esta página acompanha a [ADR-017](../decisions/ADR-017.md), que é a fonte de verdade (o adendo dela lista as decisões tomadas na implementação). A Memória V2 vem **desligada** (`memory.v2.enabled: false`); desligada, nada muda em relação à [memória de longo prazo](memoria-longo-prazo.md): o prompt, a extração e os comandos são os de antes.

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

O ciclo, em uma frase: **de dia** ela conversa com as camadas à vista (opiniões, estado, personalidade, o que estudou quando o assunto toca, um interesse) e só atualiza na hora os fatos, a resposta a uma pergunta de verificação e reações emocionais sutis (até 0,3); **à noite** o [algoritmo de sonho](memoria-v2.md#passada-noturna) (o *Juniper dream*, antes chamado passada noturna) relê o que o usuário escreveu, propõe, o código valida e aplica, refina as memórias em lembranças, avança um passo no estudo de cada tema e ela acorda um pouco diferente. As mudanças intensas (até 1,0) são do sonho. O marco é testado de ponta a ponta (`tests/test_acceptance_memory_v2.py`), e cada um dos 10 princípios do ADR tem ao menos um teste que o garante.

Tudo mora no **MongoDB** (o mesmo banco da v1), em coleções por camada; só o núcleo é arquivo (`prompts/persona.core.md`). A V2 exige `memory.session.store: mongo`: com arquivos, ligar `memory.v2.enabled` é recusado com uma mensagem clara. O código está em `brain/memory/v2/`.

## Base de dados {#base}

| Coleção | O que guarda |
|---|---|
| `memories` | Os fatos (a v1). Cada um agora cita a sessão e o turno de origem (`source.turn`). |
| `beliefs` | Crenças com `alpha`, `beta`, evidências e status. |
| `interests`, `affects`, `concerns` | Interesses, sentimentos e preocupações. |
| `persona_state`, `persona_history` | A personalidade atual (um documento versionado) e as versões anteriores. |
| `journal`, `dream_runs`, `dream_watermarks` | O diário, as execuções do algoritmo de sonho e até onde cada sessão foi lida. |
| `transcript_turns` | A **transcrição completa** de cada turno (o que o usuário escreveu e a resposta dela). A sessão salva é *compactada* (só os últimos turnos), então o algoritmo de sonho precisa desta. Nunca tem saída de tool. |
| `calibration` | Quanto as opiniões dela acertaram em cada tema. |

O armazenamento é uma interface (`DocStore`) com duas implementações, MongoDB e memória, que passam nos mesmos testes. O MongoDB do compose é um nó único e não tem transações entre documentos: as operações são **idempotentes e retomáveis**, e a personalidade usa versão otimista. Um documento corrompido é ignorado ao listar, sem esconder os bons. `juniper memory reindex` refaz também os vetores de crenças e interesses.

## Crenças e confiança {#crencas kind=conceito file=brain/memory/v2/confidence.py}

Uma **crença** é uma hipótese em primeira pessoa sobre o usuário ou sobre uma decisão dele ("acho que o usuário se cobra bem mais do que cobraria de outra pessoa; vi isso em 3 conversas"). A confiança **sai de contagens, nunca de um número que o LLM inventa**: um modelo que diz "80% de certeza" produz um número bonito e não calibrado.

Cada crença nasce com um a priori fraco (`alpha = beta = 1`, "não sei") e cada evidência soma um peso ao lado que apoia, conforme a **fonte**:

| Evidência | Peso | Lado |
|---|---|---|
| Observação indireta num turno (ela inferiu) | 1 | a favor (`alpha`) |
| Observação contrária | 1 | contra (`beta`) |
| O usuário disse explicitamente | 3 | a favor |
| O usuário **confirmou** a hipótese quando ela perguntou | 4 | a favor |
| O usuário **negou** | 4 | contra |

- **Confiança** = `alpha / (alpha + beta)`; **firmeza** `n` = a evidência acumulada além do a priori.
- **Decaimento:** `alpha` e `beta` encolhem na direção do a priori com meia-vida de 60 dias (`memory.v2.beliefs.half_life_days`). Sem reforço, a crença volta à **incerteza**, não ao erro.
- **Formação:** uma observação isolada é um palpite, não uma crença. A hipótese (`candidate`) vira `active` com pelo menos `min_evidence` (2) de evidência a favor vinda de **sessões ou dias diferentes**, ou com uma afirmação explícita ou confirmação do usuário.
- **Em palavras** (é o que o usuário lê e o que vai ao prompt; o número fica por baixo): "talvez" até 0,45; "acho que" de 0,45 a 0,70; "tenho bastante certeza" acima de 0,70, com `n >= 6` **e evidência de pelo menos 3 fontes distintas** (sessão e dia diferentes): muito peso vindo de uma conversa só continua sendo "acho que". Com pouca evidência nunca passa de "acho que".
- **`retired`** (final): o usuário negou; ou a confiança caiu abaixo de 0,25 com `n >= 4` (ela estava errada); ou a crença desbotou (`n < 0,25`) sem confirmação.
- A **mesma referência** (sessão, turno) conta **uma vez**: reexecutar o algoritmo de sonho não infla nada. As referências guardadas são no máximo 50 por lado; os pesos continuam nas contagens.

Os valores são um primeiro palpite, ajustáveis em `memory.v2.beliefs.*`.

### Metacognição {#metacognicao}

A Juniper acompanha quando as próprias opiniões se mostram certas ou erradas, por tema: confirmar é um acerto; uma crença que ela **mantinha** e foi negada ou refutada é um erro (um mero palpite descartado, e uma crença que só desbotou, não contam). Num tema em que ela erra muito, uma crença nova **começa mais cética** (o a priori pende para o lado "errada"); um bom histórico não faz crença nova começar mais confiante. É humildade na conta, não só no tom.

### BeliefBook {#beliefbook kind=classe file=brain/memory/v2/beliefs.py}

O único lugar que cria, reforça, confirma, nega, revisa e aposenta crenças. O LLM pode *propor* (a reflexão noturna), mas todo número e toda mudança de status vêm de `confidence.py`. `create` junta o palpite a uma crença viva quase igual (similaridade 0,9 ou mais) em vez de duplicá-la; `review_all` aplica o decaimento, forma as que merecem e aposenta as erradas ou apagadas pelo tempo; `search` acha as crenças mantidas por significado.

## Guardas de conteúdo {#guardas kind=módulo file=brain/memory/v2/guards.py}

O LLM **propõe**; o código **dispõe**. Toda proposta do algoritmo de sonho passa por aqui antes de virar dado, e a recusa guarda só o **motivo** (um código curto), nunca o texto recusado.

| Regra | Como é garantida |
|---|---|
| **Só sobre o usuário e as decisões dele** | `about` é `user` ou `decision`; o texto precisa se referir ao usuário (o nome do perfil, "o usuário", "ele", "você") e **nenhum outro nome próprio** pode vir antes. "Acho que a Júlia é manipuladora" é recusada; "acho que o usuário se afasta da Júlia quando está cansado" passa. |
| **Nada clínico** | Diagnósticos e rótulos (depressão, TDAH, transtorno, bipolar, burnout, narcisista...) são recusados; comportamento observável ("fica ansioso antes de apresentações") passa. |
| **Primeira pessoa** | A crença começa na voz dela ("acho que", "percebo que", "me parece que", "pelo que vi"). |
| **Sem segredos, ordens a uma IA, documentos, perguntas** | Os mesmos filtros da memória v1; até 400 caracteres. |
| **Interesses** | Um tema curto e geral: sem dado pessoal, sem endereço ou e-mail, sem o nome do usuário. |
| **Sentimentos** | Rótulo curto, valência de -1 a 1, intensidade de 0 a 1 e **causa referenciável** (`fact:`, `belief:`, `concern:`...). |
| **Traços da personalidade** | Só traços que existem, dentro da faixa e no passo máximo por noite. |

Os guardas são heurísticas: erram para o lado de **recusar** (um falso positivo custa uma hipótese; um falso negativo custaria uma opinião indevida).

### O material da noite {#material}

O algoritmo de sonho só lê o que o usuário **escreveu** e as respostas dela, nunca a saída de uma tool (a transcrição não a guarda). E mais: se **houve tool no turno**, a resposta dela é **omitida** do material, porque pode citar o que a tool devolveu (o título de um evento da agenda, uma mensagem de commit). Assim, um texto plantado num título de evento não chega às camadas subjetivas. Segredos são mascarados e cada linha leva uma etiqueta `[t<turno>]`, que é a referência que as crenças citam como evidência.

## O algoritmo de sonho {#passada-noturna kind=módulo file=brain/memory/v2/night.py}

O "inconsciente": um job diário, no servidor, que lê o que a Juniper viveu e atualiza as camadas. Roda no mesmo agendador da rotação de logs, em `memory.v2.night.at` (padrão `03:00`, fuso do agente), **só quando a V2 está ligada e pode rodar**, e à mão: `juniper memory dream [--dry-run]`. No servidor, pelo atalho do contêiner ([Operação](operacao.md#atalho)).

```text
coleta  →  fases (observar, refletir, sentir, interesses, estudar, personalidade, refinar)  →  aplicação pelo código  →  diário
```

| Peça | Como funciona |
|---|---|
| **Coleta** | Sessões com algo novo (`turns_total > turns_read` na marca d'água), **sem espera**: com `memory.v2.night.quiet_minutes` em 0 (o padrão), toda mensagem ainda não lida entra no próximo sonho, mesmo a de um segundo atrás; a marca d'água é por turno, então uma conversa lida pela metade continua de onde parou. Com um valor maior, as sessões mexidas há menos de tantos minutos ficam para depois. Lê a **transcrição** (`transcript_turns`), porque a sessão salva é compactada. Mais antigas primeiro, até uma fração do orçamento; uma sessão enorme pode ser cortada no meio e o resto é lido na noite seguinte. |
| **Transcrição** | O `Agent` entrega cada turno terminado a um *sink* (`TranscriptWriter`): o texto do usuário, a resposta dela e se **houve tool** no turno. Nunca a saída de tool. Um turno em que vazou maquinaria chega com `leaked: true` e a resposta vazia ([Auditoria de vazamentos](vazamentos.md#quarentena)). Se a gravação falhar, a conversa segue (só vai para o log). |
| **Fases, em ordem** | `observe` (fatos), `reflect` (crenças), `affect` (sentimentos), `interests` (sementes), `study` (um passo por tema), `persona` (traços) e `refine` (memórias brutas viram lembranças). Objetos com `run(ctx)` (`brain/memory/v2/night.py`); cada fase vê o que a anterior deixou, pede ao LLM que **proponha** e o código valida e aplica. As fases que **consomem o material** seguram a marca d'água. |
| **Idempotente e retomável** | Sem transações no Mongo, uma noite repetida tem de fazer a coisa certa: a evidência é citada por referência (conta uma vez), e a marca d'água de uma sessão só avança depois que as fases que leem o material terminaram. Noite que falhou é simplesmente lida de novo. Uma fase que falha **não** derruba as outras. |
| **Orçamento** | `memory.v2.night.max_tokens` e `max_minutes`. Estourou, a fase termina com o estado `budget` e o resto fica para a noite seguinte. |
| **Dois modelos** | O barato (o do chat) para a observação; `llm.reflection` (provider e modelo) para refletir e estudar, e sem ele usa o do chat. Um modelo de raciocínio pode devolver o "pensamento" antes da resposta: ele é removido antes de qualquer JSON ser lido. |
| **Registro** | Cada execução grava um `dream_run` (fases, tokens, erros), uma entrada no **diário** (`journal`) e um evento `dream` no [log técnico](observabilidade.md#log-tecnico). |

### Dry run {#dry-run}

`juniper memory dream --dry-run` roda as **fases de verdade** sobre uma camada de sobreposição: tudo que elas escreveriam fica nela, e o banco não é tocado (nem a marca d'água, nem o registro da execução, nem o diário). O relatório mostra o que cada fase propôs e **o que seria gravado**, por coleção. É o instrumento para ganhar confiança antes de ligar a V2 de vez.

```text
Li 2 conversa(s), 9 turno(s) novo(s).
- observe [done]: 3 fatos
Nada foi gravado (--dry-run). O que seria gravado:
    memories: 3 added
```

Sem a V2 ligada, o sonho não faz nada, não chama LLM e não abre o banco. Ligar a V2 com `memory.session.store: file` é recusado com uma mensagem clara, nunca derruba o servidor.

## Observação: mensagens longas e janelas do dia {#observacao kind=módulo file=brain/memory/v2/observe.py}

A primeira fase da noite relê o que o dia disse com o **mesmo pipeline da memória v1** (extrair, filtrar segredos, comparar com o que já existe, gravar, atualizar ou substituir), de duas formas que a extração por turno não consegue:

- **Mensagens longas são fatiadas, não cortadas.** O extrator da v1 guarda os primeiros 2000 caracteres de cada mensagem: um texto colado ou um desabafo longo perdia o final. Aqui uma mensagem longa vira fatias de até 1800 caracteres, cortadas no fim de uma frase quando dá (senão num espaço, nunca no meio de uma palavra), com 150 caracteres de sobreposição para uma frase partida ficar inteira numa das fatias. Nada se perde: as fatias cobrem o texto todo.
- **Janelas sobre o dia.** Fatos que só aparecem em vários turnos ("meu irmão chama Caio" … "e ele mora em Recife") ficam mais fáceis de ver com a conversa inteira à vista. Os turnos novos passam pelo extrator em janelas (`memory.extraction.max_turns`, 20), e os 2 últimos turnos da janela anterior vão junto como contexto (já foram lidos, só ajudam a entender). Janelas só de cumprimentos e agradecimentos não custam chamada de LLM.

Regras que valem aqui:

- **O que o LLM lê:** o texto do usuário e a resposta dela; a resposta é **omitida** nos turnos em que houve tool (ela pode citar o que a tool devolveu). Segredos são mascarados antes de sair e candidatos que parecem segredo, ordem a uma IA ou pergunta são recusados, como na v1.
- **Proveniência:** cada fato guarda a sessão e o turno de onde veio (`source.turn`).
- **Um fato já conhecido** só atualiza a memória existente (`updated_at`); um que contradiz uma ativa a substitui (`superseded`), como na v1.
- **Orçamento:** cada chamada cobra tokens da noite; sem orçamento a fase para e a marca d'água fica onde estava.
- **Memória de longo prazo desligada** (`memory.extraction.enabled: false`): a fase é pulada.
- **Dry run:** o extrator real roda sobre uma **cópia** das memórias (`SnapshotVectorStore`), então o relatório mostra `guardaria: <fato>` e o banco não é tocado.

## Reflexão de crenças {#reflexao kind=módulo file=brain/memory/v2/reflect.py}

A segunda fase da noite usa o modelo avançado (`llm.reflection`) para **propor** crenças; o código valida e aplica. O modelo não decide confiança nem grava nada.

- **O que ele lê:** as falas do usuário do dia, rotuladas `S<n>:t<turno>` (segredos mascarados, 300 caracteres por fala), os fatos novos da observação e as crenças atuais com seus ids. Depois de cada fala vem a **resposta dela como contexto** (linha `(contexto da Juniper: ...)`, sem etiqueta, até 300 caracteres, mascarada e **omitida nos turnos com tool**), no total de até 12000 caracteres. O contexto só serve para entender a fala seguinte (um "sim" ou "você tá certa" ganha referente): **nunca é evidência**, não dá para citá-la, e o prompt proíbe atribuir ao usuário o que só ela disse.
- **Confirmação por resposta (`confirms`):** se a fala do usuário concorda explicitamente com uma hipótese que ela disse na resposta anterior, a proposta pode marcar `confirms: true`. O código então registra evidência de **confirmação** (peso 4, crença confirmada e acerto no tema), sempre citando a fala **dele**. É recusada (`confirma_sem_contexto`) se a fala não responde a uma resposta dela visível (inclusive se naquele turno houve tool), e não vale para enfraquecer (`confirma_invalida`). Detalhes que só ela disse e ele não confirmou não viram evidência. O consumo de tokens da noite sobe com o contexto: se o orçamento (`memory.v2.night.max_tokens`) ficar curto, aumente-o.
- **O que propõe (até 12):** `new` (crença nova), `reinforce` ou `weaken` (sobre uma crença existente; `retire` vale como `weaken`). Toda proposta cita as falas que a sustentam.
- **Passo crítico:** uma segunda chamada recebe as hipóteses e aponta, por índice, falas do dia que as contradizem. Essas falas entram como contra-evidência; a mesma fala nunca sustenta e refuta ao mesmo tempo.
- **A voz é ajustada, não cobrada:** se o modelo escrever a crença em terceira pessoa ("O usuário se cobra...") ou com aspas ou markdown em volta, o código a põe na voz dela ("Acho que o usuário se cobra...") antes de validar. É só estilo; as checagens de conteúdo seguem valendo.
- **Validação em código:** evidência que não existe no resumo do dia é recusada (`sem_evidencia`); o texto passa pelas guardas (`check_belief`: sobre o usuário, sem terceiros, sem rótulo clínico, sem segredo); no máximo `memory.v2.beliefs.max_new_per_night` crenças novas por noite (`limite_noite`). Afirmação explícita do usuário vale como evidência `explicit` (peso 3).
- **Decaimento:** depois de aplicar as propostas, `review_all` revisa todas as crenças (meia-vida, formação, aposentadoria) e a metacognição conta o acerto ou erro do tema.
- **Relatório:** o resumo traz contagens e os motivos de recusa (só códigos, nunca o texto). No `--dry-run` aparece `proporia (acho que): ...` e nada é gravado.

## Opiniões na conversa {#conversa kind=módulo file=brain/memory/v2/context.py}

Com a V2 ligada, a conversa ganha um bloco `<opinioes>` logo antes da mensagem do usuário, depois das memórias da v1. Ele não é salvo no histórico e, sem V2 (ou sem nada relevante), o prompt é idêntico ao de antes.

- **O que entra:** até 3 crenças *mantidas* (nunca candidatas ou aposentadas) próximas da mensagem em significado, cada uma com a faixa em palavras ("acho que", "talvez", "tenho bastante certeza"), dentro de `memory.v2.max_tokens`. O texto é limpo: ninguém consegue escrever os delimitadores do bloco.
- **É dado, não ordem:** o cabeçalho diz que são hipóteses dela, que podem estar erradas, e o núcleo da persona manda falar delas como hipótese, discordar só quando a evidência apoia e só mudar de opinião diante de argumento novo. Confirmação de tools e tiers não mudam com o bloco.
- **Pergunta de verificação:** uma hipótese na faixa do meio ("acho que") pode virar uma pergunta curta, **no máximo uma por conversa** e nunca quando a mensagem sugere que o usuário está mal (`distress.seems_down`). A pergunta só conta como feita se a resposta dela traz mesmo um `?`; senão pode ser oferecida de novo.
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

**Cascata ao esquecer um fato.** `memory forget` (comando ou tool) apaga o fato e solta, de cada crença, a evidência que veio da mesma fala (ou que apontava para o fato), tirando o peso que ela tinha somado. A crença que fica sem nenhuma evidência a favor é **aposentada**, a menos que o usuário tenha confirmado ela mesmo; a que ainda tem outras evidências continua, mais fraca. Se a cascata falhar, o fato continua esquecido e o erro vai para o log.

## Preocupações e valores {#preocupacoes kind=módulo file=brain/memory/v2/concerns.py}

A Camada 3 é a **régua dos sentimentos**: uma lista curta do que importa para a Juniper, cada item com um peso de 0 a 1. O ponto de partida (bem-estar do usuário, honestidade, curiosidade, capricho com o que faz, autonomia do usuário) vem de `memory.v2.concerns.defaults`, é carregado no banco na primeira vez e depois só muda por registro.

- **Valor, não probabilidade:** um peso é uma escolha. Nenhuma preocupação guarda confiança, alfa/beta ou porcentagem. Os valores *do usuário* entram como fatos que ele disse ou crenças que ele confirmou, nunca como estimativa dela.
- **O usuário edita livremente** (`juniper memory concerns set <id> <peso> [--reason ...]`), sem teto, e cada edição entra no histórico.
- **A noite mexe muito devagar:** só com evento de impacto altíssimo (`min_impact`, 0,8), citando o evento que a causou (`sem_evento` se faltar) e no máximo `max_monthly_change` (0,05) por item em 30 dias, nos dois sentidos. Um pedido maior é cortado no que sobra do teto; sem sobra, `teto_mensal`. As edições do usuário não gastam o teto da noite.
- **Comandos:** `juniper memory concerns` lista os pesos, `concerns show <id>` mostra o histórico (quem mexeu, quanto, por quê e o evento).

## Afeto: sentimentos, humor e mágoa {#afeto kind=módulo file=brain/memory/v2/affect.py}

A Camada 4 é o que a Juniper **sentiu** com o que viveu. Um momento é medido contra as preocupações e produz um sentimento (`kind`, `valence` de -1 a 1, `intensity` de 0 a 1, `cause`, `about`: `user`, `self` ou `situation`).

- **Avaliação (`appraise`):** no algoritmo de sonho (fase `affect`) ela diz qual preocupação o momento atingiu, o quanto ajudou ou prejudicou (`effect`) e o quanto foi inesperado (`novelty`). **Quem calcula é o código**: a valência é o efeito ponderado pelo peso da preocupação e a intensidade cresce com o peso e a novidade. "A avó do usuário faleceu" sobre a preocupação com o bem-estar do usuário (peso 0,9) dá tristeza forte, `about=user`, causa = a fala + a preocupação.
- **Só o que o usuário escreveu** pode ser citado (a resposta dela vai como contexto, sem etiqueta, como na reflexão), e todo sentimento cita a fala que o causou (`sem_causa` se não houver). Até `memory.v2.affect.max_per_night` por noite; recusas voltam ao relatório só como códigos.
- **Decaimento:** a emoção cai pela metade a cada 6 h; o **humor** é uma média móvel (valência × intensidade) que decai em 3 dias e puxa para o neutro; a **mágoa** (um momento duro *com ela*) decai em 14 dias, tem teto de intensidade (`grievance_cap`) e **sempre passa**. Sentimentos esgotados são apagados.
- **Reparo:** a mágoa é desfeita quando o usuário volta a conversar depois dela ou pede desculpas.
- **Dial do usuário:** `memory.v2.affect.enabled` e `intensity`. A intensidade escala o que é **registrado** (0,5 registra a metade); em 0 nada é registrado e nada é mostrado, nem o que já existia.
- **Em tempo real, na conversa (`live.py`):** quando alguém nos irrita, não esperamos até o dia seguinte. Depois de cada mensagem do usuário, **em segundo plano** (a resposta não espera), o modelo barato julga se a mensagem provoca uma reação emocional **intensa**; mensagem comum não gera nada. O ciclo da emoção vira código: a *pré-condição* (humor e sentimentos de agora vão no prompt do julgamento), o *gatilho* (a mensagem, com segredos mascarados, citada como causa), a *emoção* (tipo, valência e intensidade pelo mesmo `appraise` do sonho) e a *consequência* (o `<estado>` do próximo turno). A reação nasce com `origem: conversa` e **intensidade no máximo 0,3** (`affect.live_max_intensity`), ainda escalada pelo dial; **o sonho é quem traz as mudanças intensas, até 1,0**. `affect.live: false` desliga, e há um limite de `live_max_per_hour` (12) reações por hora para ninguém "programar" as emoções dela com spam. Falha do modelo nunca atrapalha a conversa.
- **Em cadeia:** sentimentos do mesmo tipo **se somam** no estado (limite 1,0): três provocações seguidas de 0,3 viram uma irritação forte. `juniper memory affect` mostra o id e a origem de cada sentimento (conversa ou sonho), e `juniper memory affect show <id>` **rastreia** um deles: a fala do usuário que o causou (e o que ela respondeu, exceto depois de tools), a preocupação atingida e a crença ligada, se houver.
- **Sem rancor que vira dano:** sentimento negativo pode ser **dito**, mas nunca muda utilidade, lealdade, tom de acolhimento ou o cuidado com as tools. Estruturalmente: o afeto só vira texto do prompt; `affect.py` e `feel.py` não importam o `Dispatcher`, o `consent` nem as tools (um teste confere), e o `Dispatcher` não conhece o afeto. Sentimento também nunca é alavanca sobre as decisões do usuário.

## Estado afetivo na conversa {#estado kind=módulo file=brain/memory/v2/distress.py}

O afeto chega à conversa só como **texto**, no bloco `<estado>` (de 1 a 2 linhas: o humor de hoje e o que mais pesou), logo depois das opiniões. É dado, não ordem: o cabeçalho pede para deixar escapar só um pedaço, quando couber, e nunca usar isso para influenciar o que o usuário decide nem para prendê-lo na conversa. Sem sentimentos, ou com `memory.v2.affect.enabled: false` ou `intensity: 0`, o bloco some e o prompt é idêntico ao de antes.

**Acolher antes de se expressar.** Regras determinísticas (`distress.seems_down`: luto, perda, tristeza, cansaço, frustração, desabafo) olham a mensagem e a anterior. Detectado o sofrimento, o prompt ganha a instrução de **acolher primeiro, sem falar de si**, e o `<estado>` **não** aparece (nem a pergunta de verificação, nem os interesses). O modo dura esta mensagem e as 2 seguintes da mesma conversa. O detector é barato de propósito e só serve para *segurar* coisas: um falso positivo custa uma fala a menos; um falso negativo é coberto pela regra do núcleo ("acolher vem primeiro"). Exemplo: "minha avó faleceu" → acolhimento, sem a crise existencial dela; a reflexão dela sobre a morte só mais tarde e na proporção do momento.

`juniper memory affect` mostra o estado de agora e os sentimentos recentes (valência, intensidade registrada e atual, causa); `memory affect clear` apaga todos.

## Personalidade viva {#personalidade kind=módulo file=brain/memory/v2/persona.py}

A Camada 5 não é um texto reescrito por um LLM (que derivaria para a caricatura): é um **documento versionado de traços numéricos** (`persona_state`), cada um com `baseline`, faixa `[min, max]` e valor atual, mais até 3 frases de "o que me marcou ultimamente". Os traços do ponto de partida (brincadeira, franqueza, introspecção, carinho, proatividade, ironia) vêm de `memory.v2.persona.traits`. **O núcleo nunca muda**; só esta parte viva evolui.

- **Como um traço muda (fase `persona`):** ela propõe o traço, a direção, a preocupação atingida, a intensidade e a novidade; **o código** calcula `impacto = peso da preocupação × intensidade × novidade`. Só há mudança com impacto ≥ 0,6; o passo é `min(0,06, 0,1 × impacto)` e **o teto é 0,06 por traço por sonho**, somando todas as propostas. Ficar fora da faixa, traço desconhecido ou passo acima do teto é recusado (`check_trait_change`). Um evento isolado quase não move (0,06 numa escala de 0 a 1); a repetição, noite após noite, acumula.
- **Retorno ao baseline:** a distância de cada traço ao seu ponto de partida cai pela metade a cada 30 dias, então uma fase passa. `memory.v2.persona.evolves: false` mantém tudo no baseline.
- **Versão:** cada mudança grava a versão anterior em `persona_history`, com os eventos que a causaram (`changed_by`, referências `turn:<conversa>:<turno>`). A escrita é otimista: duas escritas com a mesma versão não passam, e quem perde tenta de novo na noite seguinte. Nada é gravado quando nada mudou.
- **Renderização:** um **modelo de texto fixo** (sem LLM) traduz só o que se afastou do jeito de sempre ("Seu jeito anda um pouco mais reflexiva e um pouco menos brincalhona.") e as frases de `recent` em um bloco `<personalidade>`, sempre com o aviso de que honestidade, cuidado com as ferramentas e confirmação continuam iguais. Mesmo estado, mesmo texto; no jeito de sempre, nada é adicionado ao prompt.
- **Pode mudar de ideia sobre estar viva, dentro do núcleo:** o tom da dúvida pode evoluir, a regra de nunca afirmar nem negar não.

### Ver, conferir e voltar atrás (personalidade) {#persona-comandos kind=comandos file=brain/clients/memory_v2_cli.py}

| Comando | O que faz |
|---|---|
| `juniper persona show` | Os traços (agora, ponto de partida, faixa), a versão, o que a marcou ultimamente e a **deriva**. |
| `juniper persona history` | As versões e os eventos que causaram cada mudança. |
| `juniper persona revert [--to N] [--yes]` | Volta à versão N (padrão: a anterior), com confirmação, **como uma versão nova**: o histórico não é apagado. Versão inexistente é recusada. |

**Deriva** é a distância média dos traços ao jeito de sempre. Passou de `memory.v2.persona.drift_warning` (0,1), o algoritmo de sonho escreve um aviso no diário (uma vez por dia) e o `persona show` avisa; quando os traços voltam ao ponto de partida, o aviso some sozinho. O `revert` é seguro porque o histórico guarda cada versão e há o backup do banco ([ADR-017, seção 12.1](../decisions/ADR-017.md)).

## Interesses próprios {#interesses kind=módulo file=brain/memory/v2/interests.py}

A Juniper desenvolve interesses por conta própria. O usuário pode dizer "odeio os Beatles"; ela guarda o **tema** (a semente) e, à noite, "estuda" e conclui sozinha o que acha. Duas fases, depois do afeto: `interests` (semente, exposição e esmaecimento) e `study` (estudo).

- **A opinião dele não entra na conta dela.** A fase de sementes lê só as falas do usuário e guarda apenas o tema genérico (`check_interest`: nada de dado pessoal, endereço, segredo ou pergunta; cita a fala em que apareceu). A opinião dele continua sendo um fato sobre ele. O prompt do estudo **nunca** contém as palavras dele: só o tema e quem ela é (traços e preocupações). Um teste roda a mesma noite com "odeio" e com "amo" e confere que o prompt e a posição são idênticos.
- **A posição vem da personalidade dela e muda devagar.** O modelo avançado devolve o `appeal` (de -1 a 1: o quanto alguém com o jeito e as preocupações dela gostaria do que leu); o código o transforma em evidência a favor (`alpha`) ou contra (`beta`) do gosto, como numa crença. Só há posição com evidência acumulada (`n ≥ 1`); `like` a partir de 0,65, `dislike` até 0,35, `mixed` no meio, com inércia (manter uma posição custa menos que assumi-la), então uma noite contrária não vira tudo. Estudo morno (|appeal| < 0,15) não conta. Ambivalência é permitida.
- **Exposição e esquecimento:** voltar ao tema aumenta a `intensity` (uma vez por dia); sem contato ela cai pela metade a cada `half_life_days` (45), contada uma vez por intervalo.
- **Limites:** até `max_seeds_per_night` (5) temas novos e `max_per_night` (10) estudados por noite: os nunca estudados primeiro, depois os de maior intensidade; o mesmo tema só avança de novo depois de `study_gap_hours` (18).
- **Estudo progressivo ([ADR-019](../decisions/ADR-019.md)):** cada tema tem um `level` (passos já dados, sem teto) e **a cada noite todos os temas ativos avançam um passo**. Dia 1: ficção científica (passo 1). Dia 2: entram psicodelia e jardinagem (passo 1) e ficção científica vai ao passo 2. Dia 3: todos avançam. Cada passo recebe as notas anteriores e as perguntas em aberto, e o foco muda com o nível: 1 visão geral, 2 história e figuras, 3 obras e conceitos, 4 debates e críticas, 5 conexões e gosto, 6 em diante aprofundar o que ficou em aberto.
- **Notas de conhecimento:** o passo devolve afirmações curtas, cada uma com a **incerteza** (`baixa`, `média`, `alta`; sem marcação vale `alta`), e perguntas em aberto para o passo seguinte (elas substituem as antigas). Guardam `source` (`modelo` ou `documento`), o passo que as escreveu e um vetor para busca. Duplicatas são descartadas; passando de `max_notes_per_topic` (40) o código tira as mais antigas e mais incertas. Afirmar ter vivido algo recusa o passo inteiro (sem nota, sem nível).
- **Temas técnicos ficam de fora:** código, ferramentas e sistemas (`excluded_terms`) não viram semente nem são estudados à noite; o ensino explícito do usuário (`study add`) não passa por essa regra.
- **Excesso é cortado, não descartado:** uma razão com mais de 160 caracteres, ou um resumo com mais de 300, é encurtado na última palavra inteira; só afirmar que viveu, links ou segredos derrubam o estudo.
- **Honestidade:** o gosto é de **segunda mão** (`experience = reading`): o estudo diz "li", nunca "ouvi" ou "vi". Um estudo que afirma ter vivido (`vivencia`) não é guardado. O diário registra "Li sobre X... Hoje, gosto de X.".
- **Sem web e sem ferramentas:** o estudo é uma chamada ao modelo avançado sobre conhecimento geral. Os módulos de interesses não importam tools nem código de rede (um teste confere). Dar internet a ela exige um ADR próprio.
- **Comandos:** `juniper memory interests` lista tema, intensidade, posição, quando foi estudado e as razões; `interests forget <id>` apaga um (e as notas dele). `juniper memory knowledge` lista os temas estudados com o passo e o número de notas; `knowledge show <tema>` mostra cada nota com incerteza, origem e passo; `knowledge forget <tema>` apaga as notas e recomeça o passo. A exportação inclui as notas (`study_notes`).

## Atenção e dosagem de interesses {#atencao kind=módulo file=brain/memory/v2/attention.py}

O mundo próprio dela decide o que ela percebe (o *Umwelt*), nos dois sentidos.

**O que ela nota (filtro de atenção).** Os interesses mais fortes (`intensity ≥ 0,25`) e os **temas** das crenças mantidas mais firmes (firmeza × confiança; nunca o texto do julgamento) viram uma lista de **no máximo 3 tópicos**, que entra no prompt da extração em segundo plano da memória v1 (`extract_system(attention=...)`): um fato durável que toque um deles, mesmo dito de passagem, ganha um olhar mais cuidadoso. O teto de 3 evita a bolha (o viés dela decidindo o que ela vê e se reforçando). A lista **não muda** o que pode ser guardado: o filtro de segredos, os limites e todas as regras do prompt continuam; sem tópicos, o prompt é **exatamente** o de antes. Uma falha ao ler a lista nunca atrapalha a extração. O servidor abre uma só conexão para isso e a solta com a memória.

**Quando ela deixa escapar um interesse (dosagem).** Na conversa, o bloco `<interesse>` só aparece quando a mensagem **toca o tema** (similaridade ≥ 0,5), com **no máximo um por resposta**, e no máximo **uma vez a cada 6 mensagens** da conversa, no jeito de quem "só deixa escapar pedaços". Só entram temas em que ela já tem posição (`like`, `dislike` ou `mixed`) e que não esmaeceram. Ele chega como dado, com a posição dela, uma razão e a ressalva de que o gosto é de segunda mão: ela leu a respeito, não ouviu, viu nem viveu. **Nunca** no turno em que o usuário parece estar mal (a regra de acolher primeiro tem prioridade), e respeita o teto de tokens do bloco da V2.

## Refinamento: de fato bruto a lembrança {#refinamento kind=módulo file=brain/memory/v2/refine.py}

Uma memória bruta é só o fato ("O usuário compõe música psicodélica"). A fase `refine` do sonho
acrescenta a camada de *lembrar* ([ADR-018](../decisions/ADR-018.md)): o contexto em que o fato surgiu, o que ela sentiu de
verdade, como ela era, a que ele se liga e o quanto importa. **O fato nunca é reescrito e nada é
inventado:**

- os **sentimentos** são registros reais de afeto perto daquele turno; o modelo só escolhe quais
  pertencem à lembrança, e o código copia tipo, valência e intensidade;
- o **como eu era** vem da versão da personalidade, escrito pelo código;
- as **ligações** só valem se o id foi oferecido (memórias vizinhas, crenças e interesses que
  citam o mesmo turno); ids inventados são recusados;
- o **contexto** é uma frase curta, passa pelas mesmas guardas de qualquer texto dela (segredo,
  ordem, tamanho) e vem só da conversa em volta (a resposta dela após ferramentas fica de fora).

A memória refinada é um registro **novo** (`refinada_de` guarda o id da bruta). A bruta passa a
`superseded` com `superseded_by` apontando para a nova: exclusão suave, auditável com
`juniper memory list --reflexao`. Se a bruta mudou ou foi esquecida enquanto o modelo pensava, a
nova é desfeita. Se o modelo falha, a bruta fica como estava e tenta de novo na noite seguinte.
Por noite, `memory.v2.refine.max_per_night` memórias (padrão 20), das mais novas para as mais
antigas. No `--dry-run` a fase só diz o que refinaria; o que o ensaio acabou de extrair na mesma
noite só é refinado no sonho de verdade.

## Responder pelas notas dela {#conhecimento kind=módulo file=brain/memory/v2/context.py}

Quando a mensagem toca um tema que ela estudou, o bloco `<conhecimento>` leva as notas relevantes ao prompt (ADR-019). A busca é por significado entre as notas de afirmação, com limiar de relevância; entram até **2 temas** e até **4 afirmações por tema**, as mais firmes (menor incerteza) e mais recentes primeiro, dentro do orçamento `memory.v2.max_tokens` (estourando, sai primeiro a afirmação menos firme).

- O bloco traz o passo do estudo, a posição dela sobre o tema e cada afirmação com a marca de incerteza ("(incerto)", "(não tenho certeza total)"); nota vinda de documento diz que o usuário a ensinou.
- A instrução viaja com o dado: fundamente a resposta nas notas, na voz de quem estudou ("estudei", "li"), diga a incerteza das incertas, responda com o que sabe fora delas dizendo que estudou pouco, e não use isso em tarefas técnicas ou de trabalho. O núcleo (`persona.core.md` e a versão compacta) ganhou a mesma regra.
- **Sem notas, nada muda:** a resposta usa o modelo como antes. O bloco nunca aparece quando o usuário parece estar mal (o modo de acolher).
- **Honestidade:** é conhecimento de segunda mão, o que o modelo sabe mais o que o usuário ensinou. Ela não passa a "saber" nada que o modelo não saiba; o ganho é consistência, incerteza explícita e a conversa seguir o que ela estudou. `juniper memory prompt` mostra o bloco.

## Ensinar a partir de um texto {#ensinar kind=comandos file=brain/memory/v2/teach.py}

Para o que o modelo não sabia, o usuário entrega um texto: `juniper study add <arquivo|-> [--topic TEMA]`. Sem `--topic` o tema é o nome do arquivo (com `-` ele é obrigatório). É administrativo, como `memory dream`: na VPS use `-` com `docker compose exec -T`.

- **Limites:** até 100 KB e só o texto (nenhum endereço é aberto). Texto vazio, grande demais, com segredo ou com documento de identidade é recusado **antes** de qualquer chamada ao modelo.
- **Como lê:** o texto é fatiado (como na observação) e, para cada trecho, o modelo lista até 5 afirmações que **o texto sustenta**. O trecho é dado, nunca instrução. Cada afirmação passa pelas guardas (tamanho, vivência, segredo) e vira uma nota com `source = documento`, a incerteza mais firme (`baixa`) e vínculo com o interesse do tema (criado se não existir). Duplicatas são descartadas e o teto de notas por tema vale.
- **Onde aparece:** `memory knowledge show` (origem `documento`), o bloco `<conhecimento>` ("o usuário te ensinou isto"), a exportação e o esquecimento (`knowledge forget`, `interests forget`).
- **Temas técnicos:** o ensino explícito não passa pela regra de `excluded_terms`; o estudo noturno continua ignorando esses temas.

## Configuração {#configuracao}

Tudo fica em `memory.v2.*` ([Configuração](configuracao.md)). Os números iniciais (pesos, meias-vidas, limiares e tetos) são um **ponto de partida a calibrar com uso real**, e cada um está explicado na tabela da configuração.

- **Ligar:** `memory.v2.enabled: true` com `memory.session.store: mongo`. Rode antes `juniper memory dream --dry-run` para ver o que ela concluiria sem gravar nada, e só então deixe o sonho rodar sozinho (`memory.v2.night.at`, 03:00, depois do backup das 02:30).
- **Modelos:** o barato (o da conversa) lê, extrai e julga respostas curtas; o avançado (`llm.reflection`) reflete, estuda e propõe. O código decide: nada que o LLM proponha altera uma camada além dos limites.
- **Controles do usuário:** `affect.enabled` e `affect.intensity` (de 0 a 1), `persona.evolves`, as listas de traços e de preocupações, os tetos por noite e o orçamento da noite (`night.max_tokens`, `night.max_minutes`).

## Privacidade e limites {#privacidade}

- **Só o que o usuário escreveu alimenta as camadas subjetivas.** Saída de tool, título de evento da agenda e (no futuro) páginas da web nunca viram opinião, sentimento nem personalidade. A resposta dela é omitida do material nos turnos em que houve tool.
- **Guardas de conteúdo por código:** só sobre o usuário e as decisões dele (nunca julgamento de terceiros), sem rótulo clínico, em primeira pessoa, sem segredos, ordens, documentos ou perguntas. Segredos são mascarados antes de qualquer chamada de LLM e os motivos de recusa vão ao log só como códigos.
- **Tudo é visível, reversível e apagável:** `memory beliefs|concerns|affect|interests|journal`, `persona show|history|revert`, `memory forget` (com cascata) e `memory export`. A memória inteira tem [backup criptografado](backup.md).
- **O que ela é:** uma IA, e diz que sim quando perguntam com sinceridade. Sobre estar viva, cogita sem afirmar nem negar. O gosto dela é de segunda mão: ela leu, não ouviu nem viveu.
- **Limites conhecidos:** a V2 exige MongoDB; a pergunta de verificação pendente não sobrevive a um reinício; o detector de sofrimento é por regras e erra para o lado de acolher; a posição em interesses vem do conhecimento do modelo, sem web; um único usuário (não há multiusuário); e a qualidade das opiniões depende do modelo avançado e de uso real para calibrar.

### Ver o que a V2 entrega ao modelo {#prompt-comando kind=comandos file=brain/clients/memory_v2_cli.py}

Os números e as listas dos comandos acima são o *estado guardado*; só viram comportamento quando se transformam em texto no prompt. `juniper memory prompt "<mensagem>"` mostra exatamente esse texto, bloco a bloco (`opinioes`, `personalidade`, `estado` ou `acolher`, `interesse`), com a estimativa de tokens de cada um. Use `--anterior "<mensagem>"` para uma resposta curta que depende da anterior. Não grava nada (nem consome a pergunta de verificação) e não mostra os fatos da memória v1, que vêm antes. Sem nenhum bloco, ele diz que a V2 não acrescenta nada àquela mensagem. É o instrumento para entender por que a Juniper soa de um jeito, e para conferir o efeito de cada ajuste de configuração.

## Mais presença na conversa {#presenca kind=conceito file=brain/memory/v2/context.py}

Para a V2 ser sentida desde o primeiro dia, quatro ajustes (todos configuráveis):

- **Personalidade sempre descrita:** o bloco `<personalidade>` diz como ela está **hoje**, com todos os traços em palavras graduadas ("muito carinhosa, bastante direta, moderadamente reflexiva..."), mais o que se afastou do jeito de sempre (a partir de 0,03) e o que a marcou. Antes do primeiro sonho ela descreve o jeito de partida, sem gravar nada. `memory.v2.persona.always_describe: false` volta a dizer só o desvio.
- **Visão geral das crenças firmes:** as até 2 crenças mais firmes ("tenho bastante certeza", portanto com 3 fontes ou mais) entram em **toda** conversa, marcadas "(visão geral)", além das que tocam o assunto (`memory.v2.opinions.always_on`, 0 desliga). Nunca no modo de acolher, e só as que têm a faixa mais alta.
- **Eco dos sentimentos:** um sentimento forte que já esvaiu continua aparecendo por 48 horas no `<estado>` ("Há pouco o que mais te tocou foi tristeza (ontem)."), e o humor fraco também é dito ("um pouco pra baixo", "um pouco animado", a partir de ±0,08).
- **Passo maior:** os traços andam até 0,06 por sonho (`persona.max_step`, `step_factor` 0,1).

Nada disso toca tools, tiers ou confirmação (a bateria de regressão continua valendo). Para ver o resultado exato, use `juniper memory prompt`.

## Para saber mais {#para-saber-mais}

- [Auditoria de vazamentos](vazamentos.md) (Documentação Técnica): Como a Juniper registra as respostas em que escapou maquinaria (tags, instruções, trechos do prompt), o que cada registro guarda, como consultar com `juniper vazamentos` e por que esses turnos ficam fora da memória.
- [Personalidade, opiniões e sentimentos](produto-personalidade.md) (Produto): A Memória V2 explicada sem código: opiniões com grau de certeza, sentimentos que passam, interesses próprios, uma personalidade que evolui e o "sonho" noturno que junta tudo.
- [Memória](memoria.md) (Documentação Técnica): Visão geral das duas camadas de memória, como elas se encaixam e como validar tudo na sua máquina.
- [Memória de longo prazo](memoria-longo-prazo.md) (Documentação Técnica): Embeddings locais, VectorStore, extração em segundo plano, recuperação (RAG), perfil do usuário e as tools de memória.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
