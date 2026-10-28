---
title: Integrações
group: Produto
order: 7
description: O que a Juniper faz com a agenda do Google, a música do YouTube e os repositórios git, e o que cada integração precisa.
status: guia
tags: produto integrações agenda google calendar música youtube music playlist gosto git commits deploy
---

## Agenda do Google {#agenda}

A Juniper lê, marca, altera e desmarca compromissos no Google Calendar ([ADR-008](../decisions/ADR-008.md), [ADR-010](../decisions/ADR-010.md)).

- **Marcar:** "marca dentista amanhã às 16h". Ela calcula a data, confere conflitos em todas as agendas e pergunta antes de criar. Se houver algo no horário, conta o que é e pergunta o que fazer.
- **Consultar:** "o que tenho na sexta?". Ela conta de forma natural, sem despejar uma lista.
- **Alterar e desmarcar:** "passa o dentista para as 17h", "desmarca o café de amanhã". Apagar avisa que é irreversível.
- **Quais agendas:** ela tem uma agenda própria e pode gerenciar as suas agendas que você compartilhar com ela; outras agendas podem ser só lidas, para checar conflitos.

Cada evento criado leva um lembrete e a assinatura "Agendado pela Juniper". Eventos de dia inteiro (feriados, aniversários) não bloqueiam horários. Os títulos e horários lidos vão ao modelo de linguagem como texto.

O que precisa: um projeto no Google Cloud com uma **conta de serviço** e as agendas compartilhadas com ela ([Integrações Google Cloud e Groq](integracoes-producao.md#calendar)). Técnico: [Agenda Google](google-calendar.md).

## Música {#musica}

A Juniper procura músicas pela API oficial do YouTube e toca no **YouTube Music do celular** ([ADR-024](../decisions/ADR-024.md)).

- **Tocar:** "toca Oceano do Djavan". Ela busca, escolhe a versão oficial e, se o pedido veio do app, abre e dá o play sozinha. De outros canais, manda o link.
- **Mostrar opções:** "procura músicas do Djavan" devolve uma lista com links.
- **Playlists:** "cria uma playlist Foco com essas músicas". Ela pergunta uma vez e cria a playlist sempre **privada**. Precisa de um login no Google, feito uma vez.
- **Gosto musical:** quando você diz "gostei dessa" ou "pula essa", ela anota em silêncio. Por enquanto o gosto só é registrado; ainda não muda as sugestões.

A API do YouTube tem uma cota diária gratuita; a Juniper conta o que gasta e avisa quando acaba, sem inventar resultados. Técnico: [Música](musica.md).

## Git {#git}

Para quem desenvolve, a Juniper usa dois comandos sobre o repositório em que o terminal está:

- **Buscar commits:** "busca os commits da feature 1339".
- **Publicar uma feature:** "faz o deploy da 4911" leva os commits da feature para as branches `develop` e `stage` (cherry-pick e push). Como envia código para o servidor, é uma ação **destrutiva**: ela sempre pergunta, com aviso.

Essas ações rodam **no seu computador**, não no servidor. Hoje funcionam com a Juniper instalada no próprio computador (modo local); com o cérebro num servidor, dependem de o terminal se registrar como executor, o que está planejado ([ADR-014](../decisions/ADR-014.md)). Técnico: [Comandos git](git.md).

## Integrações futuras {#futuras}

Cada integração nova é uma ou mais ferramentas, com nível de risco, testes e, quando muda a arquitetura, um ADR. Busca na web, comandos no computador e abrir aplicativos estão previstos no modelo de segurança ([ADR-006](../decisions/ADR-006.md)), mas ainda não existem.

## Para saber mais {#para-saber-mais}

- [Ferramentas e permissões](produto-ferramentas.md) (Produto): Como a Juniper age no mundo real por meio de ferramentas, os quatro níveis de risco e como a permissão é dada na própria conversa.
- [Agenda Google](google-calendar.md) (Documentação Técnica): Ler, marcar, alterar e desmarcar compromissos no Google Calendar, com checagem de conflitos.
- [Música](musica.md) (Documentação Técnica): Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
- [Comandos git](git.md) (Documentação Técnica): search e deploy no terminal e pelo agente (git_search e git_deploy).
- [Integrações Google Cloud e Groq](integracoes-producao.md) (Produção): Como ligar no servidor a agenda do Google, a música do YouTube e a voz (Google Text-to-Speech e Groq), com as credenciais, a configuração e a verificação de cada uma.
