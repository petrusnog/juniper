---
title: Modelos de linguagem (LLM)
group: Produto
order: 2
description: O que é um modelo de linguagem, quais a Juniper usa, por que ela tem mais de um e o que isso significa para custo, velocidade e privacidade.
status: guia
tags: produto llm modelo de linguagem inteligência artificial ia provedor deepinfra groq ollama failover token custo privacidade
---

## O que é um modelo de linguagem {#o-que-e}

Um **modelo de linguagem** (em inglês, *Large Language Model*, ou **LLM**) é um programa treinado com uma quantidade enorme de texto para prever qual palavra vem a seguir. Isso parece pouco, mas, feito muito bem, permite entender pedidos, resumir, explicar e escrever respostas naturais. É a mesma família de tecnologia por trás dos assistentes de IA mais conhecidos.

Duas coisas importantes sobre um LLM:

- **Ele não tem memória própria entre conversas.** A cada pergunta, a Juniper entrega ao modelo tudo o que ele precisa saber naquele momento: quem ela é, o que vocês conversaram, a hora atual, o que ela lembra sobre você. A memória é da Juniper, não do modelo ([Memória](produto-memoria.html)).
- **Ele não age sozinho.** O modelo só escreve texto. Quando ele "decide" usar uma ferramenta, na verdade escreve um pedido estruturado ("chame a agenda com estes dados"), e é o cérebro da Juniper que confere, pede a sua permissão e executa ([Ferramentas e permissões](produto-ferramentas.html)).

## Tokens: a unidade de medida {#tokens}

Os modelos leem e escrevem em **tokens**, pedaços de palavra (em média, uns quatro caracteres). Os provedores cobram e limitam o uso por token, e a velocidade de resposta também se mede em tokens por segundo. Por isso a Juniper cuida do tamanho do que manda ao modelo: mantém só as conversas recentes, resume os dados das ferramentas e escolhe só as memórias relevantes para cada mensagem.

## Os provedores que a Juniper usa {#provedores}

A Juniper fala com modelos de três provedores, em ordem de preferência:

| Provedor | Onde roda | Papel |
|---|---|---|
| **DeepInfra** | Na nuvem | O principal: modelos capazes, cobrados por uso ([ADR-009](adr-009.html)). |
| **Groq** | Na nuvem | A segunda opção: rápido e com um plano gratuito (com limite de uso). |
| **Ollama** | No seu computador | A reserva: roda localmente, sem internet, mais lento e menos capaz. |

## Por que mais de um: failover {#failover}

Serviços na nuvem às vezes ficam fora do ar, atingem o limite de uso ou recusam uma chave. Em vez de parar, a Juniper tenta o próximo da fila automaticamente, com a mesma conversa. Isso se chama **failover** (troca por falha). Se a troca acontecer, ela avisa ("A DeepInfra recusou as credenciais. Vou tentar a Groq."). Uma regra protege a conversa: se o modelo já começou a responder, ela não troca no meio, para não repetir o começo da resposta ([ADR-001](adr-001.html), [ADR-009](adr-009.html)).

A ordem é configurável, e trocar de modelo ou de provedor não muda nada no resto da Juniper: o cérebro conversa com "um modelo", e uma peça chamada roteador escolhe qual ([LLM](llm.html)).

## Modelos diferentes para tarefas diferentes {#papeis}

Nem toda tarefa precisa do modelo mais caro. A conversa do dia a dia usa o modelo principal. A [personalidade viva](produto-personalidade.html), quando ligada, faz uma reflexão uma vez por noite e pode usar um modelo mais capaz para isso, porque roda pouco e o custo fica baixo.

## Privacidade {#privacidade}

O que você escreve é enviado ao provedor do modelo para ser processado, junto com o contexto necessário (trechos de memória, títulos de eventos da agenda que ela leu). As **chaves de acesso** aos provedores ficam só no servidor e nunca aparecem em mensagens de erro nem em registros. A busca na memória é feita por um modelo pequeno que roda **no próprio servidor**: o texto das memórias não sai dali para ser indexado. Mais em [Privacidade e segurança](produto-privacidade.html).

## Limites {#limites}

- Modelos podem **errar**: entender mal um pedido ou inventar um detalhe. É por isso que toda ação que altera algo pede confirmação, mostrando exatamente o que será feito.
- Não há controle automático de gasto nos provedores: o consumo é acompanhado no painel de cada um.
- O modelo local (Ollama) é bem mais lento num computador sem placa de vídeo e segue pior as instruções; por isso fica por último na fila.
