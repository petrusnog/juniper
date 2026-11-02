---
title: Memória V3
group: Documentação Técnica
section: Memória
order: 14
description: A Juniper com vontade própria e vida interior: fala livre e ação protegida, humor que se sente na fala, personalidade e crenças que evoluem, conhecimento só da memória com a web ensinando, e o sonho como ponto de maior evolução.
status: pronta
tags: memória v3 livre arbítrio humor pad big five personalidade crenças conhecimento web sonho voz mood search adr-029 adr-030 adr-031
---

> **Nota:** esta página é o mapa da V3. Cada assunto tem a sua explicação em um lugar só (os links abaixo); as decisões e o que foi descartado estão nos ADRs [029](../decisions/ADR-029.md), [030](../decisions/ADR-030.md) e [031](../decisions/ADR-031.md). A V3 continua dentro da [Memória V2](memoria-v2.md) (`memory.v2.*`): é a mesma camada de dados, com outro comportamento.

## O que mudou, em uma tabela {#o-que-mudou}

| Antes (V2) | Agora (V3) | Onde está explicado |
|---|---|---|
| Ela raramente discordava e o modo "acolher" a calava | **Fala livre, ação protegida:** opina sobre qualquer tema, discorda de frente, fala de si; o mal-estar do usuário é um *sinal*, não uma trava. Tiers, confirmação e segredos não mudam | [Agente](agente.md), [ADR-029](../decisions/ADR-029.md) |
| O humor quase não aparecia na fala | **Humor no espaço PAD** que toda mensagem empurra, e um bloco `<voz>` com ordens de tom (o humor muda a fala, nunca a ação); `juniper mood set` para sentir a diferença | [Afeto](memoria-v2.md#afeto), [A voz](memoria-v2.md#voz) |
| Seis traços que quase não andavam | **Big Five em 10 aspectos + ironia**; o traço anda pela média dos estados que ela expressa, e o jeito de sempre amadurece | [Personalidade viva](memoria-v2.md#personalidade) |
| Crenças só sobre o usuário | Crenças **sobre ele, sobre ela mesma e sobre o mundo**, com erro de previsão e curva de confiança | [Crenças e confiança](memoria-v2.md#crencas) |
| O modelo respondia com o que sabia | **O modelo é só o motor cognitivo:** fatos vêm das notas dela; sem nota, ela aprende na web (`learn_topic`) e responde pelo que gravou | [Responder pelas notas](memoria-v2.md#conhecimento), [Aprender na hora](memoria-v2.md#aprender) |
| Busca na web não existia | `web_search` e `web_fetch` com Tavily → SearXNG → DuckDuckGo, cota e proteção contra SSRF | [Configuração](configuracao.md#web-config), [Segurança](seguranca.md#conteudo-externo), [Produção](producao.md#searxng) |
| O sonho refletia sobre o que o modelo sabia | **Sonho V2** em fases do sono: estuda pela web, forma crenças do mundo e de si, esfria a emoção das lembranças e poda | [Sonho V2](memoria-v2.md#sonho-v2) |
| Evolução só em números no terminal | Página **Evolução** do dashboard, com "por que mudei" | [Dashboard](dashboard.md#paginas) |

## Ligar, testar e conferir {#testar}

1. **Ligar:** `memory.v2.enabled: true` (a V3 vem com os padrões novos). A busca na web usa `JUNIPER_TAVILY_API_KEY` no `.env`; sem chave, cai para o SearXNG (opcional) e o DuckDuckGo ([Produção](producao.md#searxng)).
2. **Sentir o humor:** `juniper mood set hostil --grau forte --por 1`, converse e depois `juniper mood clear`. `juniper memory prompt "oi"` mostra o `<estado>` e a `<voz>` que ela leria.
3. **Ver o que ela estudou na web:** `juniper memory knowledge`, e a página Estudos do dashboard (cada nota diz de onde veio).
4. **Ver como ela mudou:** `juniper memory dream --dry-run` (as fases, sem gravar), o diário (`juniper memory journal`, bloco "Como mudei") e a página **Evolução**.
5. **Medir o fundamento:** o [teste de fundamento](memoria-v2.md#fundamento).

## O que é verificado e o que não é {#limites}

- **Verificado por teste:** a conta de cada peça (PAD e humor, tom, traços e maturação, crenças e curva, notas da web, failover da busca, proteção contra SSRF, fases do sonho), a *semana simulada* de personalidade, e o aceite de ponta a ponta com modelos roteirizados.
- **Não verificado (precisa do seu ambiente):** a busca e a leitura de páginas **reais** (o Tavily, o DuckDuckGo e o SearXNG nunca foram chamados nesta implementação; o plano gratuito do Tavily não foi conferido na página oficial), o **contêiner do SearXNG** (o CI só valida o arquivo do compose), o comportamento do **modelo de verdade** com as ordens de tom (se ele as segue, e se recusa algum tema por política do provedor) e o número do teste de fundamento contra um modelo real.
- **Limite honesto:** não há como apagar o que o modelo aprendeu no treino; o modo estrito instrui, fundamenta e mede.
- **Valores a calibrar** com uso real: `alpha`, `max_step`, `max_band`, `maturation`, a meia-vida do humor, os tetos por hora da conversa e as validades das notas da web.

## Para saber mais {#para-saber-mais}

- [Memória V2](memoria-v2.md) (Documentação Técnica): As camadas de dados, os comandos e o algoritmo de sonho, onde cada parte da V3 está detalhada.
- [Configuração](configuracao.md) (Documentação Técnica): Todas as opções, incluindo `web.*` e as novas de `memory.v2.*`.
- [Dashboard](dashboard.md) (Documentação Técnica): A página Evolução e as demais.
- [Segurança](seguranca.md) (Documentação Técnica): O conteúdo da web como dado e a política do `web_fetch`.
- [Personalidade, opiniões e sentimentos](produto-personalidade.md) (Produto): A mesma ideia sem código.
