---
title: Glossário
group: Produto
order: 9
description: Os termos usados nesta documentação, explicados em uma ou duas frases.
status: guia
tags: glossário termos definições vocabulário llm token tool tier executor embedding rag websocket vps adr failover
---

| Termo | O que é |
|---|---|
| **ADR** | *Architecture Decision Record*: um documento curto que registra uma decisão de arquitetura, o contexto e as alternativas. Os da Juniper estão em [Decisões](adr.html). |
| **Agente** | Um programa que usa um modelo de linguagem para decidir ações, executá-las e reavaliar o resultado, em vez de só responder texto ([Agente](agente.md)). |
| **Cérebro** (*brain*) | O programa central da Juniper: conversa com o modelo, executa ferramentas e guarda a memória. |
| **Cliente** | Um programa por onde você conversa (terminal, Telegram, app, painel). É "fino": não decide nada. |
| **Confirmação** | O "sim" que você dá na conversa para uma ação que altera algo ([Ferramentas e permissões](produto-ferramentas.md#permissao)). |
| **Crença** | Uma opinião da Juniper sobre você, com grau de certeza calculado por evidência ([Personalidade](produto-personalidade.md#opinioes)). |
| **Embedding** | Uma lista de números que representa o sentido de um texto; textos de sentido parecido têm números parecidos. Usado na busca por significado. |
| **Executor** | Um dispositivo (celular, computador) que cumpre ferramentas que precisam rodar nele, como abrir um link ([Executor](executor.md)). |
| **Failover** | Trocar automaticamente para o próximo provedor quando um falha ([Modelos de linguagem](produto-llm.md#failover)). |
| **LLM** | *Large Language Model*, modelo de linguagem: a inteligência que lê e escreve texto ([Modelos de linguagem](produto-llm.md)). |
| **Memória V2** | O conjunto opcional de opiniões, sentimentos, interesses e personalidade viva ([Personalidade](produto-personalidade.md)). |
| **MongoDB** | O banco de dados onde ficam as conversas e as memórias. |
| **Prompt** | Todo o texto entregue ao modelo para uma resposta: instruções, histórico, memória e a sua mensagem. |
| ***Prompt injection*** | Tentativa de manipular uma IA escondendo ordens em conteúdo que ela lê. |
| **Provedor** | A empresa ou o programa que roda um modelo de linguagem (DeepInfra, Groq, Ollama). |
| **RAG** | *Retrieval-Augmented Generation*: buscar informação relevante (aqui, memórias) e entregá-la ao modelo junto com a pergunta. |
| **Rede privada** | Uma rede entre os seus dispositivos, cifrada e inacessível de fora; o servidor só é alcançável por ela. |
| **Sessão** | Uma conversa, com o seu histórico; pode ser retomada. |
| **Sonho** | O processo noturno da Memória V2 que relê o dia e atualiza opiniões, sentimentos e personalidade ([O sonho](produto-personalidade.md#sonho)). |
| **STT / TTS** | *Speech-to-Text* (transcrever fala em texto) e *Text-to-Speech* (sintetizar texto em fala). |
| **Tier** | O nível de risco de uma ferramenta: leitura, escrita, destrutiva ou proibida ([Ferramentas e permissões](produto-ferramentas.md#niveis)). |
| **Token (de modelo)** | Um pedaço de palavra, a unidade em que os modelos leem, escrevem e cobram. |
| **Token (de acesso)** | Uma senha longa que os clientes apresentam ao cérebro para conectar. |
| **Tool** | Ferramenta: uma capacidade que o modelo pode usar, como mexer na agenda ([Tools](tools.md)). |
| **VPS** | *Virtual Private Server*: um servidor alugado na nuvem, onde o cérebro roda em produção. |
| **WebSocket** | Uma conexão de rede que fica aberta nos dois sentidos, usada entre os clientes e o cérebro ([API WebSocket](protocolo.md)). |

## Para saber mais {#para-saber-mais}

- [Introdução](introducao.md) (Introdução): O que é a Juniper, o que ela sabe fazer hoje, como é construída e quais trilhas seguir para aprender mais.
- [Como a Juniper funciona](produto.md) (Produto): A Juniper explicada sem código: o cérebro e os corpos, o caminho de uma mensagem e o que acontece quando ela precisa agir.
- [Modelos de linguagem (LLM)](produto-llm.md) (Produto): O que é um modelo de linguagem, quais a Juniper usa, por que ela tem mais de um e o que isso significa para custo, velocidade e privacidade.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Tools](tools.md) (Documentação Técnica): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [Metadocumentação](metadocs.md) (Documentação Técnica): Como a Juniper responde sobre a própria arquitetura: detector de perguntas por palavras-chave, busca BM25 local na documentação, contexto automático e a tool docs_search.
- [Memória](memoria.md) (Documentação Técnica): Visão geral das duas camadas de memória, como elas se encaixam e como validar tudo na sua máquina.
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
