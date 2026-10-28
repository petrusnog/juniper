---
title: Introdução
group: Introdução
order: 1
description: O que é a Juniper, o que ela sabe fazer hoje, como é construída e quais trilhas seguir para aprender mais.
tags: introdução visão geral agente assistente capacidades próximos passos trilhas
---

## O que é a Juniper {#o-que-e}

A Juniper é uma **agente autônoma pessoal**. Você escreve (ou fala) em linguagem natural, como falaria com uma pessoa, e ela entende o pedido, decide se precisa de alguma ferramenta, age e responde. "Marca dentista amanhã às 16h" vira um evento na agenda do Google; "toca Oceano do Djavan" abre a música no celular; "do que a gente falou ontem?" é respondido com a memória dela.

Três ideias guiam o projeto:

- **Um cérebro, vários corpos.** Um único cérebro (o *brain*) pensa, lembra e decide. Os clientes (terminal, bot do Telegram, app Android, painel web) são finos: levam o que você diz e mostram a resposta. Por isso a mesma Juniper, com a mesma memória, atende em qualquer lugar.
- **Agir com permissão.** A Juniper age no mundo real, mas o que altera algo só acontece depois de um "sim" seu, dado na própria conversa. O que é irreversível vem com aviso. O que é proibido nunca executa.
- **Memória que é sua.** Ela lembra de preferências, projetos e fatos sobre você entre conversas, e tudo o que ela guarda pode ser visto, exportado e apagado.

## O que ela sabe fazer hoje {#capacidades}

| Área | O que ela faz | Saiba mais |
|---|---|---|
| Conversa | Responde em português, com personalidade própria, em texto (com a resposta chegando aos poucos) ou por voz. | [Como funciona](produto.html) |
| Agenda | Lê, marca, altera e desmarca compromissos no Google Calendar, checando conflitos antes de perguntar. | [Integrações](produto-integracoes.html#agenda) |
| Música | Procura e toca músicas no YouTube Music do celular, cria playlists e anota o seu gosto musical. | [Integrações](produto-integracoes.html#musica) |
| Git | Busca commits e faz o deploy de uma feature (cherry-pick em `develop` e `stage`) no repositório local. | [Integrações](produto-integracoes.html#git) |
| Memória | Lembra da conversa atual, de fatos duráveis sobre você e do seu perfil; opcionalmente forma opiniões, sentimentos, interesses e uma personalidade que evolui. | [Memória](produto-memoria.html), [Personalidade](produto-personalidade.html) |
| Canais | Terminal (`juniper`), bot do Telegram, app Android com chat de texto e de voz, e um painel web somente leitura. | [Onde conversar](produto-canais.html) |
| Autoconhecimento | Responde sobre a própria arquitetura e funcionamento consultando esta documentação, com uma busca local que não gasta tokens à toa. | [Como funciona](produto.html#autoconhecimento) |
| Resiliência | Usa vários provedores de modelo de linguagem: se um falhar, o próximo assume sem interromper a conversa. | [Modelos de linguagem](produto-llm.html) |

## Como ela é construída, em um minuto {#construcao}

- O **cérebro** é escrito em Python (pasta `brain/`). Ele roda o *agente* (o laço que conversa com o modelo de linguagem e chama ferramentas), guarda a memória no **MongoDB** e expõe um **servidor WebSocket** para os clientes.
- Os **modelos de linguagem** vêm de provedores intercambiáveis (DeepInfra, Groq e Ollama), com troca automática em caso de falha.
- As **ferramentas** (*tools*) são plugins: cada capacidade nova é um arquivo novo, com um esquema e um nível de segurança.
- Os **clientes** falam com o cérebro por um protocolo único e versionado. O app Android é escrito em React Native.
- Em **produção**, o cérebro roda em contêineres Docker numa VPS, sem nenhuma porta aberta para a internet: os clientes chegam por uma rede privada.

O desenho completo está em [Arquitetura](architecture.html) e cada escolha tem o seu registro nas [Decisões (ADRs)](adr.html).

## Próximos passos {#proximos-passos}

Escolha a trilha que combina com o seu objetivo:

| Se você quer... | Siga a trilha | Comece por |
|---|---|---|
| Entender o produto, sem precisar ler código | **Produto** | [Como a Juniper funciona](produto.html) |
| Desenvolver features e contribuir | **Ambiente Local** | [Como contribuir](contribuindo.html) e [Primeiros passos](primeiros-passos.html) |
| Entender como cada componente funciona por dentro | **Documentação Técnica** | [Visão geral da arquitetura](architecture.html) |
| Consultar contratos, opções e o porquê das decisões | **Referência** | [API WebSocket](protocolo.html), [Configuração](configuracao.html), [ADRs](adr.html) |
| Implantar a Juniper no seu próprio servidor | **Produção** | [Visão geral da produção](producao.html) |
| Escrever um cliente ou integrar outro sistema ao cérebro | **Produção** e **Referência** | [Integrar com o cérebro](integrar-cliente.html) |

> **Nota:** a Juniper é um sistema de **um único usuário**: um cérebro serve a uma pessoa, em vários dispositivos. Não há contas nem separação de dados entre pessoas diferentes.
