---
title: Onde conversar
group: Produto
order: 6
description: Os canais da Juniper (terminal, Telegram, app Android com texto e voz, painel web) e o que cada um faz de melhor.
status: guia
tags: produto canais clientes terminal cli telegram app android celular voz painel dashboard conversa retomar
---

Todos os canais conversam com **o mesmo cérebro** e a **mesma memória**. Uma conversa começada num lugar pode ser retomada em outro; quando isso acontece, o canal novo assume a conversa e o anterior é avisado ([ADR-012](../decisions/ADR-012.md)).

| Canal | Bom para | Limites |
|---|---|---|
| [Terminal](#terminal) | Quem trabalha no computador: conversa, comandos de memória, git. | Exige o programa instalado. |
| [Telegram](#telegram) | Falar de qualquer lugar, sem instalar nada além do Telegram. | Só texto e só conversa. |
| [App Android](#app) | Texto e voz no celular; tocar música no aparelho. | Só Android. |
| [Painel web](#painel) | Ver a memória, os sentimentos e os estudos dela em gráficos. | Só leitura. |

## Terminal {#terminal}

O comando `juniper` abre uma conversa no terminal do computador. A resposta aparece palavra por palavra, e o terminal também tem comandos para ver e apagar a memória, consultar os registros e retomar a última conversa. Pelo terminal, a Juniper também pode buscar commits e publicar features no repositório em que você está ([Integrações](produto-integracoes.md#git)). Técnico: [CLI](cli.md).

## Telegram {#telegram}

Um bot do Telegram leva as suas mensagens ao cérebro. Ele só responde a quem está numa **lista de permissão** (por padrão, ninguém), só em conversa privada, e só entende texto. A resposta chega inteira (o Telegram não mostra o texto aparecendo aos poucos), e as confirmações funcionam igual: um "sim" na mensagem seguinte. Há um limite de mensagens por minuto e a opção de apagar as mensagens depois de algumas horas. As mensagens passam pelos servidores do Telegram, sem criptografia de ponta a ponta ([ADR-016](../decisions/ADR-016.md)). Técnico: [Bot do Telegram](telegram.md).

## App Android {#app}

O app tem duas abas ([ADR-022](../decisions/ADR-022.md)):

- **Texto:** a conversa com a resposta chegando aos poucos, a lista de conversas para retomar e um botão para interromper.
- **Voz:** uma conversa contínua, de mãos livres. O microfone fica aberto, a Juniper entende, responde em voz alta e mostra legendas. Dá para interrompê-la falando por cima (com fone) ou com um botão, e a conversa termina com um botão, com "tchau, Juniper" ou depois de um minuto em silêncio. Funciona com a tela apagada.

Pelo app, um pedido como "toca Oceano do Djavan" **abre o YouTube Music no celular e dá o play**. A voz é sintetizada pelo Google e a fala é transcrita pela Groq; se um desses serviços falhar ou o orçamento mensal acabar, o próprio celular assume e a conversa continua ([ADR-023](../decisions/ADR-023.md)). Técnico: [App mobile](mobile.md) e [Voz por áudio](voz-audio.md).

## Painel web {#painel}

Um painel no navegador mostra o que a Juniper sabe e sente: as memórias e as lembranças, as opiniões com as evidências, o humor ao longo dos dias, os temas que ela estuda, os sonhos noturnos e as conversas. Ele **só lê**: nada ali altera a memória. O acesso pede o mesmo token dos outros clientes e funciona em qualquer dispositivo da rede privada ([ADR-020](../decisions/ADR-020.md)). Técnico: [Dashboard](dashboard.md).

## Em todos os canais {#todos}

- A **permissão** é sempre um "sim" na conversa ([Ferramentas e permissões](produto-ferramentas.md)).
- Se a conexão cair no meio de uma resposta, o cérebro termina a resposta e a guarda; ao reconectar, ela aparece.
- Sem conexão, o canal avisa que a Juniper está indisponível e não acumula mensagens para enviar depois.
- A Juniper sabe por onde o pedido chegou: no celular ela abre a música; no terminal ou no Telegram, manda o link.

## Para saber mais {#para-saber-mais}

- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [CLI](cli.md) (Documentação Técnica): O comando juniper: chat, mensagem única, retomar conversa, comandos de memória, git e utilitários.
- [Bot do Telegram](telegram.md) (Documentação Técnica): O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Voz por áudio](voz-audio.md) (Documentação Técnica): Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do protocolo.
