---
title: Memória
group: Produto
order: 4
description: Como a Juniper lembra: a conversa atual, os fatos duráveis sobre você, o seu perfil, a busca por significado e como ver e apagar tudo.
status: guia
tags: produto memória lembrar esquecer curto prazo longo prazo perfil busca por significado embeddings rag privacidade apagar exportar
---

## Por que memória importa {#por-que}

Um modelo de linguagem não lembra de nada entre uma pergunta e outra. Sem memória, toda conversa começaria do zero e você precisaria repetir que prefere o terminal ZSH ou em que projeto está trabalhando. A Juniper resolve isso com **duas camadas de memória**, como as pessoas: a de curto prazo (a conversa de agora) e a de longo prazo (o que vale a pena guardar) ([ADR-011](../decisions/ADR-011.md)).

## Memória de curto prazo: a conversa {#curto-prazo}

É o que foi dito na conversa atual. A Juniper salva a conversa a cada resposta, então você pode fechar o aplicativo e **retomar** depois de onde parou, até em outro aparelho.

Uma conversa muito longa não cabe inteira no modelo. Por isso ela mantém só as mensagens mais recentes; o que saiu da janela não se perde de verdade, porque o que importava já foi para a memória de longo prazo.

## Memória de longo prazo: fatos sobre você {#longo-prazo}

Depois de cada resposta, em segundo plano, a Juniper relê o que foi dito e separa **fatos duráveis**: preferências ("prefere ZSH"), projetos ("o projeto X usa Laravel 11"), coisas da sua vida ditas de passagem ("tem um gato chamado Bidu"). Pedidos pontuais ("marca dentista amanhã") não viram memória: o compromisso já está na agenda.

Quando você fala de algo, ela **busca por significado**, não por palavra exata: uma pergunta sobre "framework PHP" encontra a memória sobre "Laravel". Para isso, cada memória é transformada numa lista de números que representa o seu sentido (um *embedding*), e a busca compara esses números. Essa transformação roda **no próprio servidor**, sem enviar as memórias a terceiros. As memórias mais parecidas com o assunto entram no contexto da resposta, técnica conhecida como RAG (*Retrieval-Augmented Generation*, geração aumentada por recuperação).

Regras que valem sempre:

- **Sem duplicar, sem apagar à força.** Se um fato mudou ("migrei o projeto X para o Laravel 12"), a memória nova **substitui** a antiga, que fica arquivada para auditoria, nunca editada.
- **Segredos nunca são guardados.** Senhas, chaves e números de cartão são filtrados antes de qualquer gravação.
- **Memória é dado, não ordem.** O que está na memória informa a Juniper, mas nunca a comanda.
- **Uma falha de memória nunca derruba a conversa.** Se o banco estiver fora do ar, ela conversa sem lembrar.

## O seu perfil {#perfil}

Além dos fatos, a Juniper guarda um **perfil** curto: nome, data de nascimento, nacionalidade (e gênero, só se você disser). Ele entra em toda mensagem, então ela sabe quem você é desde o "bom dia". A idade é calculada na hora a partir da data de nascimento. Documentos (CPF, RG, passaporte) nunca entram no perfil.

## Você no controle {#controle}

Guardar é silencioso, para a conversa fluir: no máximo ela diz "anotado". Já **esquecer sempre pergunta**, porque não dá para desfazer. Você pode:

| Quero... | Como |
|---|---|
| Ver o que ela sabe | Perguntar "o que você sabe sobre mim?", ou `juniper memory list` no terminal, ou o [painel web](produto-canais.md#painel). |
| Pedir para lembrar | "Lembra que eu uso ZSH." |
| Pedir para esquecer | "Esquece o que eu disse sobre o projeto X." Ela mostra o que vai apagar e pede o "sim". |
| Levar tudo embora | `juniper memory export` gera um arquivo com o perfil e as memórias. |

## Além dos fatos {#alem}

A memória pode ir além de fatos: com a **Memória V2** ligada, a Juniper forma opiniões sobre você com grau de certeza calculado, tem sentimentos, desenvolve interesses e uma personalidade que evolui, com um processo noturno parecido com o sono. Isso é opcional e está em [Personalidade, opiniões e sentimentos](produto-personalidade.md).

## Para saber mais {#mais}

- Técnico: [Memória](memoria.md), [Memória de sessão](memoria-sessao.md) e [Memória de longo prazo](memoria-longo-prazo.md).
- Decisão: [ADR-011](../decisions/ADR-011.md) (arquitetura de memória) e [ADR-018](../decisions/ADR-018.md) (memória refinada).
