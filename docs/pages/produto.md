---
title: Como a Juniper funciona
group: Produto
order: 1
description: A Juniper explicada sem código: o cérebro e os corpos, o caminho de uma mensagem e o que acontece quando ela precisa agir.
status: guia
tags: produto visão geral leigo como funciona cérebro corpos mensagem agente conversa analogia
---

Esta trilha explica a Juniper para quem não programa: o que ela é, como pensa, como age, como lembra e como protege você. Os termos técnicos aparecem só quando ajudam, e o [Glossário](glossario.html) reúne todos.

## Um cérebro, vários corpos {#cerebro-corpos}

Pense num restaurante. O **balcão** recebe o pedido e entrega o prato; a **cozinha** decide como prepará-lo; os **equipamentos** fazem o trabalho; e o **caderno de receitas** guarda o que já se aprendeu. A Juniper é organizada assim:

| No restaurante | Na Juniper | O que é |
|---|---|---|
| O balcão | Os **clientes** | Onde você conversa: o terminal do computador, o Telegram, o app no celular. Só levam a mensagem e mostram a resposta. |
| O chef | O **cérebro** | O programa que entende o pedido, decide o que fazer e escreve a resposta. Roda num servidor, sempre ligado. |
| O conhecimento do chef | O **modelo de linguagem** | A inteligência que lê e escreve texto ([Modelos de linguagem](produto-llm.html)). |
| Os equipamentos | As **ferramentas** | Capacidades concretas: mexer na agenda, tocar uma música, buscar no histórico de um projeto ([Ferramentas e permissões](produto-ferramentas.html)). |
| O caderno | A **memória** | O que ela sabe sobre você e sobre as conversas anteriores ([Memória](produto-memoria.html)). |

Como existe **um cérebro só**, a Juniper é a mesma em qualquer lugar: você pode começar uma conversa no terminal e continuar no celular, e ela lembra das mesmas coisas. O balcão nunca cozinha: nenhum app guarda as suas conversas ou toma decisões, tudo isso fica no cérebro ([ADR-007](adr-007.html)).

## O caminho de uma mensagem {#caminho}

Exemplo: você escreve *"amanhã quero uma ida ao dentista, às 16h"*.

1. **O cliente entrega a frase ao cérebro.** O app ou o terminal só repassa o texto.
2. **O cérebro monta o contexto.** Junta a sua mensagem com a data e a hora de agora (é isso que faz "amanhã" virar uma data), o que ela sabe sobre você e a lista de ferramentas disponíveis.
3. **O modelo de linguagem pensa.** Ele lê tudo e conclui que precisa da ferramenta de agenda, com o título "Dentista" e o horário já calculado.
4. **A ferramenta checa antes de agir.** A agenda confere se há conflito às 16h. Se houver, a Juniper conta e pergunta o que você quer fazer.
5. **A Juniper pede permissão.** Criar um evento altera a sua agenda, então ela pergunta em palavras: *"Posso marcar «Dentista», segunda das 16h às 17h?"*.
6. **Você responde "sim"** e o evento é criado. A resposta chega aos poucos, palavra por palavra, como alguém digitando.
7. **Ela anota e lembra.** A conversa é salva e, em segundo plano, o que vale lembrar para o futuro vai para a memória.

Se o pedido não precisa de ferramenta ("como foi o seu dia?"), os passos 4 a 6 simplesmente não acontecem. Se precisa de várias ("vê minha agenda de amanhã e desmarca o que estiver depois das 18h"), ela repete o ciclo pensar → agir → reavaliar algumas vezes, com um limite para não ficar em círculos.

## Ela age, mas pede licença {#licenca}

A Juniper divide o que pode fazer em níveis de risco. Consultar algo (ler a agenda, buscar uma música) acontece direto. Alterar algo (criar um evento, criar uma playlist) só acontece depois de um "sim" seu. Destruir algo (apagar um evento) também pede, com o aviso de que não dá para desfazer. E algumas coisas são proibidas e nunca acontecem. A permissão é dada **na própria conversa**, sem botões: um "sim" claro executa, e qualquer dúvida ("sim, mas só o primeiro") cancela. Os detalhes estão em [Ferramentas e permissões](produto-ferramentas.html).

## Personalidade {#personalidade}

A Juniper tem um jeito próprio: fala português do dia a dia, em frases corridas (nunca em listas ou tabelas), com humor leve e curiosidade pela pessoa. Ela é honesta sobre o que é: uma IA, e diz isso quando perguntam com sinceridade. Opcionalmente, a personalidade dela pode **evoluir** com o tempo, formando opiniões, sentimentos e interesses próprios, sempre dentro de regras fixas que não mudam ([Personalidade, opiniões e sentimentos](produto-personalidade.html)).

## Ela sabe explicar como funciona {#autoconhecimento}

Pergunte "como funciona a sua memória?" ou "por que você pede permissão antes de apagar?" e a Juniper responde com base nesta própria documentação. Ela reconhece a pergunta por palavras-chave e busca os trechos certos no próprio servidor, sem gastar uma chamada extra ao modelo, e nunca revela senhas ou chaves: a documentação só diz onde elas ficam ([Metadocumentação](metadocs.html), [ADR-025](adr-025.html)).

## Onde ela roda {#onde-roda}

O cérebro roda num **servidor** (uma VPS, um computador alugado na nuvem, ou qualquer máquina sempre ligada). Esse servidor **não fica exposto na internet**: os seus dispositivos chegam até ele por uma rede privada, como uma linha direta que só eles conhecem ([ADR-013](adr-013.html)). Para uso individual, a Juniper também pode rodar inteira dentro do terminal de um computador, sem servidor.

## Para saber mais {#mais}

- O desenho técnico completo: [Visão geral da arquitetura](architecture.html).
- O porquê de cada escolha: [Decisões (ADRs)](adr.html), em especial o [ADR-007](adr-007.html) (cérebro e corpos) e o [ADR-010](adr-010.html) (confirmação na conversa).
