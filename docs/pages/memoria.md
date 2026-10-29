---
title: Memória
group: Documentação Técnica
section: Memória
order: 10
description: Visão geral das duas camadas de memória, como elas se encaixam e como validar tudo na sua máquina.
status: pronta
tags: memória hipocampo rag sessão longo prazo mongodb embeddings perfil aceite roteiro
---

## Visão geral {#visao-geral}

A Juniper lembra em duas camadas ([ADR-011](../decisions/ADR-011.md)):

| Camada | O que guarda | Onde | Página |
|---|---|---|---|
| **Memória de sessão** (curto prazo) | A conversa atual, dentro de um orçamento de tokens; pode ser retomada com `--resume`. | Arquivos JSON ou MongoDB | [Memória de sessão](memoria-sessao.md) |
| **Memória de longo prazo** | Fatos duráveis sobre você (preferências, projetos, fatos) e o seu **perfil** (quem você é), achados por significado. | MongoDB (`memories` e `profile`) | [Memória de longo prazo](memoria-longo-prazo.md) |

O objetivo: a Juniper lembra que você prefere ZSH, que odeia tabulação com espaço e que o projeto X usa Laravel 11, **entre sessões**, e deixa de ser estranha.

## Como as peças se encaixam {#fluxo}

```text
sua mensagem ──► recuperação ──► o prompt ganha <perfil> e <memorias> ──► LLM ──► resposta
                      ▲                                                            │
            busca por significado                                                  ▼
            (embeddings + cosseno)                              fim do turno: extração em segundo plano
                      ▲                                                            │
                 VectorStore  ◄──── grava / atualiza / substitui ◄──── LLM de extração + filtro de segredos
```

1. **A cada mensagem**, o [perfil](memoria-longo-prazo.md#perfil) entra sempre; as memórias mais parecidas com o assunto entram dentro de um orçamento de tokens ([recuperação](memoria-longo-prazo.md#recuperacao)).
2. **Depois de cada resposta**, um trabalhador em segundo plano ([extração](memoria-longo-prazo.md#extracao)) separa o que vale lembrar, sem duplicar e sem segredos.
3. **Você controla**: `juniper memory list|search|forget|export|profile` ([CLI](cli.md#memory)) e as tools `remember`, `recall`, `forget` ([tools de memória](memoria-longo-prazo.md#tools-de-memoria)).

Princípios do [ADR-011](../decisions/ADR-011.md): **memória nunca é editada, só substituída** (a antiga fica `superseded`, para auditoria); **segredos nunca são gravados**; um registro ruim não esconde os bons; uma falha de memória **nunca derruba o chat**.

## Ligar a memória {#ligar}

A memória de sessão funciona com arquivos, sem instalar nada. A de longo prazo precisa do MongoDB:

```bash
juniper install --memory=mongo     # confere o Docker, sobe o banco, cria usuário e senha e liga o store
```

Detalhes (e como fazer à mão) em [Memória de sessão](memoria-sessao.md#mongodb). Para desligar a de longo prazo sem desligar o banco: `memory.extraction.enabled: false`.

## Validar na sua máquina {#validar}

O comportamento esperado da memória tem um teste de aceite automatizado (`tests/test_acceptance_sprint2.py`, roda no CI) e um roteiro manual com o LLM, o modelo e o banco **reais**.

### O teste automatizado {#teste-automatizado}

Abre o chat de verdade (`cli._chat`) várias vezes, como várias sessões na mesma máquina e no mesmo banco:

| Sessão | O que acontece |
|---|---|
| 1 | Você diz "Prefiro ZSH", "Odeio tabulação com espaço" e "O projeto X usa Laravel 11". A Juniper avisa "Guardei 3 coisas sobre você". |
| 2 (nova) | "Qual shell eu uso?" → ZSH. "Como devo indentar?" → tabulação. "Que framework PHP o projeto X usa?" → Laravel 11 (associação por significado). Só a memória relevante entra no prompt de cada pergunta. |
| 3 | "Migrei o X para o Laravel 12": a memória nova substitui a antiga, que fica `superseded`. A pergunta seguinte responde Laravel 12. |
| 4 | "Esquece o projeto X": a Juniper pergunta antes e nada some; depois do "sim", o projeto X some do banco (inclusive a versão antiga). ZSH e tabulação continuam. |

**O que é real:** chat, agente, sessão, extração, deduplicação e atualização, filtro de segredos, `VectorStore`, recuperação, injeção no prompt, tools e confirmação. **O que é simulado:** o LLM (regras fixas), o modelo de embeddings (um mapa de conceitos) e o MongoDB (coleção em memória). O teste prova que o encanamento funciona; **não** prova a qualidade do LLM nem do modelo reais.

### Roteiro manual {#roteiro}

Pré-requisitos: Docker rodando, `juniper install --memory=mongo` feito e chaves de LLM no `.env`. Na primeira conversa com memórias, a Juniper baixa o modelo de embeddings (~120 MB).

```bash
# 0. comece limpo (opcional)
juniper memory list

# 1. sessão 1: diga e feche com /sair
juniper
 > prefiro ZSH
 > odeio tabulação com espaço
 > o projeto X usa Laravel 11
 > /sair                      # deve dizer "Guardei N coisas sobre você"
juniper memory list           # confira o que foi guardado (e o que NÃO foi)

# 2. sessão 2 (nova, SEM -r)
juniper
 > qual shell eu uso?                    # espera: ZSH
 > como devo indentar?                   # espera: tabulação
 > que framework PHP o projeto X usa?    # espera: Laravel 11 (por significado)
 > /sair

# 3. atualização
juniper
 > migrei o X para o Laravel 12
 > /sair
juniper memory list --all                 # a de Laravel 11 deve estar "superseded"
juniper "que framework o projeto X usa?"  # espera: Laravel 12

# 4. esquecer
juniper "esquece o que eu disse sobre o projeto X"   # ela lista e pergunta; responda "sim"
juniper memory list --all                            # nada do projeto X, nem a versão antiga
juniper "que framework o projeto X usa?"             # espera: não lembra
```

### Se algo falhar {#diagnostico}

| Sintoma | Onde olhar |
|---|---|
| Não guardou nada, ou guardou lixo | A extração depende do LLM. `juniper memory list` mostra o que entrou; o prompt está em `brain/memory/extraction.py`. Apague o que não presta com `juniper memory forget <id>`. |
| Guardou, mas não lembra na sessão 2 | Quase sempre o `retrieval.threshold` (0.80, um palpite). Rode `juniper memory search "que framework PHP o projeto X usa" --threshold -1`: ele mostra a nota de cada memória. Ponha o corte entre a menor nota do que é relevante e a maior do que não é. |
| Traz memória que não tem a ver | O mesmo: suba o `threshold`. |
| Duplicou ou não substituiu a versão antiga | É o "juiz" do LLM (`duplicate`/`supersede`) ou o `extraction.neighbor_threshold` (0.75) que não considerou as duas parecidas. Veja a nota com `memory search`. |
| Nada foi extraído | Confira `memory.session.store: mongo` e `memory.extraction.enabled: true`. No modo `exit`, Ctrl+C não extrai: use `/sair` ou Ctrl+D. |

Há também um teste com o modelo real de embeddings que imprime as notas das perguntas do roteiro: `pytest -m integration -s tests/test_acceptance_sprint2.py` (baixa ~120 MB).

> **Atenção:** o roteiro manual é o que prova a memória com os componentes reais. Os parâmetros `retrieval.threshold` (0.80) e `extraction.neighbor_threshold` (0.75), e os prompts de extração e de julgamento, ainda não foram calibrados com uso real; a latência real sob carga também não foi medida.

## Além dos fatos {#futuro}

A [Memória V2](memoria-v2.md) ([ADR-017](../decisions/ADR-017.md)), opcional, constrói sobre esta base opiniões, sentimentos, interesses, uma personalidade viva e o refinamento das memórias em lembranças ([ADR-018](../decisions/ADR-018.md)).

## Para saber mais {#para-saber-mais}

- [Memória](produto-memoria.md) (Produto): Como a Juniper lembra: a conversa atual, os fatos duráveis sobre você, o seu perfil, a busca por significado e como ver e apagar tudo.
- [Glossário](glossario.md) (Produto): Os termos usados nesta documentação, explicados em uma ou duas frases.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Memória de sessão](memoria-sessao.md) (Documentação Técnica): A conversa atual, o orçamento de tokens, retomar com --resume e onde as conversas ficam (arquivos ou MongoDB).
- [Memória de longo prazo](memoria-longo-prazo.md) (Documentação Técnica): Embeddings locais, VectorStore, extração em segundo plano, recuperação (RAG), perfil do usuário e as tools de memória.
- [Memória V2](memoria-v2.md) (Documentação Técnica): A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com o algoritmo de sonho.
- [Backup da memória](backup.md) (Produção): Como a memória da Juniper (o banco MongoDB) é copiada, criptografada e versionada num repositório privado do GitHub, e como restaurar.
