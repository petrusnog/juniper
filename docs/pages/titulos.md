---
title: Título das conversas
group: Documentação Técnica
section: Núcleo
order: 11
description: Como cada conversa ganha um título (a Juniper nomeia, você renomeia), onde ele aparece em cada cliente e os comandos /conversas, /renomear e /retomar.
status: pronta
tags: título conversas sessão renomear nomear lista telegram app desktop dashboard retomar conversas titulo title
---

## Visão geral {#visao-geral}

Cada conversa tem um **título**: um nome curto («Receita de bolo de cenoura», «Plano de viagem a Lisboa») que a própria Juniper dá a partir do começo da conversa e que você pode trocar quando quiser. É esse nome, e não mais a primeira mensagem ou um id, que as listas de conversas de todos os clientes mostram ([ADR-036](../decisions/ADR-036.md)). As conversas anteriores a este recurso continuam sem título e aparecem pela primeira mensagem, como antes.

## Como a Juniper dá o título {#nomear kind=módulo file=brain/core/title.py}

1. **Rascunho, na hora.** Terminado o primeiro turno, as primeiras palavras do que você disse viram o título provisório (`draft`) e são salvas com a conversa. Assim ela nunca fica sem nome, nem com o modelo fora do ar.
2. **O modelo, logo depois.** Em segundo plano, uma chamada curta pede de 2 a 6 palavras, no seu idioma, a partir da primeira troca. O resultado (`auto`) substitui o rascunho e os clientes recebem o aviso `session.titled`. Se o modelo falhar, demorar mais de 20 s ou devolver lixo, o rascunho fica.
3. **Um cumprimento não é assunto.** Se a primeira mensagem foi só «oi» ou «bom dia», a Juniper nomeia de novo, uma vez, depois da terceira troca.
4. **Você manda.** Um título que você escolheu (`user`) nunca é trocado pela Juniper, nem por um turno que estava sendo salvo quando você renomeou, nem pelo modelo que terminava de pensar.

Todo título, venha de onde vier, é uma linha só, sem tags nem formatação, com segredos mascarados e no máximo 80 caracteres (`clean_title`). Renomear não conta como usar a conversa: a ordem da lista não muda.

## Onde aparece e como renomear {#clientes}

| Cliente | Lista | Renomear |
|---|---|---|
| **App mobile** | **Conversas**: o título (ou a primeira mensagem, nas antigas); o da conversa aberta fica sob a linha de estado | **Renomear** em cada conversa da lista |
| **Desktop** | Painel **Conversas** | **Renomear** em cada linha (Enter salva, Esc cancela) |
| **Dashboard** | Página **Conversas**: o título em destaque e o id como detalhe; a Auditoria e as Ocorrências mostram o título da conversa de cada registro ([Registro de atividade](atividade.md)) | Na conversa, o campo **Título** (só o dono) |
| **Telegram** | `/conversas` | `/renomear <título>` |
| **Terminal** | `/conversas` no chat e `juniper conversas` | `/renomear <título>` no chat e `juniper renomear <número\|id> <título>` |

### Comandos de texto {#comandos kind=módulo file=brain/clients/conversations.py}

Para os clientes que só têm uma linha de texto (o bot do Telegram e o terminal, no modo local e no modo servidor):

| Comando | O que faz |
|---|---|
| `/conversas` | Lista as 10 mais recentes, numeradas: `1. Receita de bolo · há 2 h · 6 mensagens`, com «← esta» na que está aberta. |
| `/renomear <título>` (ou `/titulo`) | Renomeia a conversa aberta. |
| `/retomar <número\|id>` | Volta a uma conversa da lista (o número da lista ou o começo do id; um começo de id que serve para mais de uma pede o número). |

Não passam pelo modelo, não entram no histórico da conversa e não contam como mensagem. No Telegram valem enquanto a Juniper não está respondendo (o bot atende uma mensagem por vez).

## Protocolo {#protocolo}

Só acréscimos compatíveis ([API WebSocket](protocolo.md#sessoes)): `SessionSummary.title` e `title_source`, `SessionState.title`, `session.rename` (sem `session_id`, renomeia a conversa aberta), `session.renamed` (a resposta) e `session.titled` (empurrada à conexão que tem a conversa aberta quando a Juniper a nomeia ou outra janela a renomeia). A `protocol_version` não mudou.

## Onde fica {#armazenamento kind=módulo file=brain/memory/session.py}

`title` e `title_source` são campos da sessão, nos dois stores. `set_title` grava sem mexer em `updated_at`; ele recusa trocar um título do usuário por um do modelo, e `save` adota o título do usuário que encontrar gravado quando a cópia em memória ainda tem o antigo ([Memória de sessão](memoria-sessao.md#titulo)).

## Limitações {#limitacoes}

- Conversas anteriores ao recurso não ganham título em lote; ganham quando retomadas e você manda a próxima mensagem.
- Uma conversa aberta em dois lugares: o outro cliente fica sabendo na hora (`session.titled`) se estiver conectado; senão, na próxima vez que listar.
- O app mobile e o desktop só foram exercitados por testes automatizados e pela leitura do código; não foram abertos num aparelho real.

## Testes {#testes}

```bash
pytest tests/test_session_title.py tests/test_conversations.py tests/test_dashboard_session_title.py -v
cd mobile && npm test        # chat.test.ts e ChatScreen.test.tsx
cd desktop && npm test       # tests/chat.test.ts
```

Cobrem a limpeza do título, o rascunho, o cumprimento, a chamada ao modelo (feita, falha, vazia), os dois stores (inclusive o título do usuário contra um `save` atrasado), o servidor (lista, `session.titled`, renomear a aberta e uma salva, janela alheia), o Telegram, o terminal, os comandos `juniper conversas`/`renomear` e o dashboard (lista, detalhe, renomear só pelo dono e com CSRF).

## Para saber mais {#para-saber-mais}

- [Memória de sessão](memoria-sessao.md) (Documentação Técnica): Como a conversa é guardada e retomada, em arquivos ou no MongoDB.
- [API WebSocket](protocolo.md) (Documentação Técnica): As mensagens entre os clientes e o cérebro, inclusive as de sessão.
- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Telegram](telegram.md) (Clientes): O bot do Telegram como cliente do cérebro.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web: conversas, auditoria, ocorrências e mais.
- [CLI](cli.md) (Documentação Técnica): Os comandos do `juniper`, o modo remoto e os erros em português.
