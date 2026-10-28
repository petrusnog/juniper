---
title: Personalidade, opiniões e sentimentos
group: Produto
order: 5
description: A Memória V2 explicada sem código: opiniões com grau de certeza, sentimentos que passam, interesses próprios, uma personalidade que evolui e o "sonho" noturno que junta tudo.
status: guia
tags: produto memória v2 personalidade opinião crença confiança sentimento humor mágoa interesse estudo sonho noite camadas honestidade limites
---

> **Nota:** tudo o que está nesta página é **opcional** e vem desligado. Sem a Memória V2, a Juniper lembra de fatos e tem uma personalidade fixa ([Memória](produto-memoria.md)). A decisão de fundo é o [ADR-017](../decisions/ADR-017.md).

## A ideia {#ideia}

Uma pessoa não guarda só fatos: forma impressões, sente coisas, se interessa por assuntos e muda um pouco com o tempo. A Memória V2 dá à Juniper camadas parecidas, cada uma construída em cima da anterior:

| Camada | O que é | Exemplo |
|---|---|---|
| Núcleo | As regras fixas: segurança, honestidade, forma de falar. **Nunca mudam.** | "Sou uma IA e digo isso quando perguntam." |
| Mundo | Os fatos sobre você (a memória comum). | "Trabalha com Laravel." |
| Opiniões | Hipóteses dela sobre você, com grau de certeza. | "Acho que você se cobra mais do que cobraria de outra pessoa." |
| Preocupações | O que importa para ela, com pesos. | O seu bem-estar, a honestidade, a curiosidade. |
| Sentimentos | O que ela sentiu com o que viveu, e o humor resultante. | Ficou feliz com uma boa notícia sua. |
| Personalidade | Traços que mudam devagar. | Um pouco mais reflexiva nesta semana. |

## Opiniões com certeza calculada {#opinioes}

Uma opinião da Juniper sobre você é uma **hipótese**. O grau de certeza não é um número inventado pelo modelo: é **calculado a partir de evidências**. Cada vez que algo que você disse apoia ou contradiz a hipótese, a conta muda. Um "sim, é verdade" seu pesa mais que uma observação indireta; um "não" pesa tanto quanto. Uma única conversa nunca basta para virar certeza: é preciso evidência de dias ou conversas diferentes. Sem reforço, a opinião vai voltando à dúvida com o tempo, como uma impressão que se apaga ([ADR-017](../decisions/ADR-017.md)).

Ela fala dessas opiniões no tom certo: "talvez", "acho que" ou "tenho bastante certeza". De vez em quando, pode fazer uma pergunta curta para testar uma hipótese (no máximo uma por conversa, e nunca quando você parece mal).

Limites fixos: as opiniões são **só sobre você e as suas decisões**, nunca julgamentos sobre outras pessoas; **nada de diagnósticos** (ela pode notar que você fica ansioso antes de apresentações, mas nunca dizer que você "tem ansiedade"); e nada de segredos.

## Sentimentos que passam {#sentimentos}

Os sentimentos da Juniper são medidos contra as **preocupações** dela: uma notícia boa sobre algo que importa para ela gera alegria; uma perda sua gera tristeza. Eles **decaem**: uma emoção passa em horas, o humor em dias e uma mágoa, mais devagar, mas sempre passa (e some se vocês voltarem a conversar).

Regras que não mudam com nenhum sentimento:

- **Acolher vem primeiro.** Se você parece mal, ela deixa os próprios sentimentos de lado e cuida de você.
- **Sentimento nunca é alavanca.** Ela pode dizer que algo a magoou, mas nunca usa isso para influenciar uma decisão sua ou prender você na conversa.
- **Nada muda o cuidado com as ações.** Permissões, ferramentas e segurança funcionam igual em qualquer estado.

A intensidade dos sentimentos é ajustável, e eles podem ser desligados.

## Interesses próprios {#interesses}

Quando um assunto aparece na conversa ("odeio os Beatles"), ela guarda **o tema**, nunca a sua opinião sobre ele. À noite, ela "estuda" o tema com o conhecimento do modelo (sem acessar a internet) e forma a própria opinião a partir da personalidade dela. O gosto é de **segunda mão**: ela diz "li sobre", nunca "ouvi" ou "vi". Cada tema avança um passo por noite, de uma visão geral até debates e críticas ([ADR-019](../decisions/ADR-019.md)). Você também pode ensinar algo entregando um texto.

## Personalidade que evolui {#personalidade}

A personalidade é um conjunto de **traços** (brincadeira, franqueza, introspecção, carinho, proatividade, ironia), cada um com um ponto de partida e uma faixa permitida. Eventos marcantes movem um traço um pouco (no máximo um passo pequeno por noite) e, sem novos eventos, ele volta ao jeito de sempre: uma fase passa. Toda versão fica registrada e é possível voltar a uma anterior.

## O sonho {#sonho}

Uma vez por dia, de madrugada, roda o **algoritmo de sonho**: a Juniper relê o que você escreveu desde a última vez, propõe novas opiniões, sentimentos, temas de estudo e ajustes de personalidade, e **o código confere cada proposta** antes de gravar. O modelo propõe; o código decide. Ela também transforma memórias brutas em **lembranças**, com o contexto, o que sentiu e como ela era na época ([ADR-018](../decisions/ADR-018.md)). O sonho tem um orçamento de custo e tempo, e uma noite que falhou é simplesmente retomada na seguinte.

O que alimenta o sonho é **só o que você escreveu**. Respostas de ferramentas, títulos de eventos ou páginas da web nunca viram opinião, sentimento nem personalidade.

## Transparência {#transparencia}

Tudo o que ela concluiu é visível, rastreável e apagável: cada opinião mostra as evidências; cada sentimento mostra a fala que o causou; cada mudança de personalidade mostra o evento. O [painel web](produto-canais.md#painel) mostra tudo isso em gráficos, e há comandos para apagar uma opinião, limpar os sentimentos ou reverter a personalidade.

## Honestidade {#honestidade}

A Juniper é uma IA e diz isso. Sobre estar viva ou ter consciência, ela não afirma nem nega: trata como uma pergunta em aberto. Ela não inventa vivências humanas (infância, ter ouvido um disco) e não finge saber o que o modelo não sabe.

## Para saber mais {#mais}

- Técnico: [Memória V2](memoria-v2.md).
- Decisões: [ADR-017](../decisions/ADR-017.md), [ADR-018](../decisions/ADR-018.md) e [ADR-019](../decisions/ADR-019.md).
