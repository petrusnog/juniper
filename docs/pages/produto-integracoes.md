---
title: Integrações
group: Produto
order: 7
description: O que a Juniper faz com a agenda do Google, a música do YouTube e os repositórios git, e o que cada integração precisa.
status: guia
tags: produto integrações agenda google calendar música youtube music playlist gosto git commits deploy
---

## Agenda do Google {#agenda}

A Juniper lê, marca, altera e desmarca compromissos no Google Calendar ([ADR-008](adr-008.html), [ADR-010](adr-010.html)).

- **Marcar:** "marca dentista amanhã às 16h". Ela calcula a data, confere conflitos em todas as agendas e pergunta antes de criar. Se houver algo no horário, conta o que é e pergunta o que fazer.
- **Consultar:** "o que tenho na sexta?". Ela conta de forma natural, sem despejar uma lista.
- **Alterar e desmarcar:** "passa o dentista para as 17h", "desmarca o café de amanhã". Apagar avisa que é irreversível.
- **Quais agendas:** ela tem uma agenda própria e pode gerenciar as suas agendas que você compartilhar com ela; outras agendas podem ser só lidas, para checar conflitos.

Cada evento criado leva um lembrete e a assinatura "Agendado pela Juniper". Eventos de dia inteiro (feriados, aniversários) não bloqueiam horários. Os títulos e horários lidos vão ao modelo de linguagem como texto.

O que precisa: um projeto no Google Cloud com uma **conta de serviço** e as agendas compartilhadas com ela ([Integrações Google Cloud e Groq](integracoes-producao.html#calendar)). Técnico: [Agenda Google](google-calendar.html).

## Música {#musica}

A Juniper procura músicas pela API oficial do YouTube e toca no **YouTube Music do celular** ([ADR-024](adr-024.html)).

- **Tocar:** "toca Oceano do Djavan". Ela busca, escolhe a versão oficial e, se o pedido veio do app, abre e dá o play sozinha. De outros canais, manda o link.
- **Mostrar opções:** "procura músicas do Djavan" devolve uma lista com links.
- **Playlists:** "cria uma playlist Foco com essas músicas". Ela pergunta uma vez e cria a playlist sempre **privada**. Precisa de um login no Google, feito uma vez.
- **Gosto musical:** quando você diz "gostei dessa" ou "pula essa", ela anota em silêncio. Por enquanto o gosto só é registrado; ainda não muda as sugestões.

A API do YouTube tem uma cota diária gratuita; a Juniper conta o que gasta e avisa quando acaba, sem inventar resultados. Técnico: [Música](musica.html).

## Git {#git}

Para quem desenvolve, a Juniper usa dois comandos sobre o repositório em que o terminal está:

- **Buscar commits:** "busca os commits da feature 1339".
- **Publicar uma feature:** "faz o deploy da 4911" leva os commits da feature para as branches `develop` e `stage` (cherry-pick e push). Como envia código para o servidor, é uma ação **destrutiva**: ela sempre pergunta, com aviso.

Essas ações rodam **no seu computador**, não no servidor. Hoje funcionam com a Juniper instalada no próprio computador (modo local); com o cérebro num servidor, dependem de o terminal se registrar como executor, o que está planejado ([ADR-014](adr-014.html)). Técnico: [Comandos git](git.html).

## Integrações futuras {#futuras}

Cada integração nova é uma ou mais ferramentas, com nível de risco, testes e, quando muda a arquitetura, um ADR. Busca na web, comandos no computador e abrir aplicativos estão previstos no modelo de segurança ([ADR-006](adr-006.html)), mas ainda não existem.
