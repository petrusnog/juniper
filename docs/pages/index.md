---
title: Livro de bolso da Juniper
group: Introdução
order: 0
description: Documentação da Juniper, organizada por trilhas de leitura.
tags: início documentação home
---

```html-raw
<div class="hero"><img class="logo" src="assets/logo.png" alt="Logo da Juniper: silhueta em pixel art verde neon de uma mulher de óculos, de cabelo comprido, com um ramo de zimbro" width="84" height="84"><h1 class="brand-name">Juniper</h1></div>
```

A Juniper é uma agente autônoma pessoal: você conversa em linguagem natural (pelo terminal, pelo Telegram, por um app Android ou por voz) e ela decide quais ferramentas usar para resolver o pedido, como a agenda do Google, a música do YouTube, comandos git e a própria memória. Este livro reúne tudo o que é preciso para entender, desenvolver, integrar e implantar a Juniper.

> **Dica:** aperte <kbd>Ctrl</kbd>+<kbd>K</kbd> (ou <kbd>/</kbd>) em qualquer página para buscar por termo: nomes de classes (`Dispatcher`), opções do `config.yaml` (`retrieval.threshold`), comandos (`juniper memory`), mensagens do protocolo (`chat.send`) ou assuntos (`deploy`).

## Por onde começar {#comecar}

- **Primeiro contato:** a [Introdução](introducao.md) explica o que a Juniper é, o que ela faz e quais trilhas seguir.
- **Entender o produto, sem código:** a trilha [Produto](produto.md) explica, para qualquer pessoa, como ela conversa, age, lembra e protege os seus dados.
- **Desenvolver uma feature:** a trilha [Ambiente Local](primeiros-passos.md) monta o ambiente completo e termina com a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
- **Entender a arquitetura:** a [Documentação Técnica](architecture.md) cobre cada camada: núcleo, memória, clientes e integrações.
- **Consultar contratos e decisões:** a [Referência](adr.html) traz os ADRs, a [API WebSocket](protocolo.md), a [API HTTP](api-http.md) e todas as [opções de configuração](configuracao.md).
- **Colocar no ar:** a trilha [Produção](producao.md) ensina a implantar o cérebro numa VPS qualquer, ligar as integrações e conectar os clientes.

## Todas as páginas {#paginas}

```html-raw
<div id="feature-cards"></div>
```

## Como esta documentação é feita {#como-e-feita}

As páginas são escritas em Markdown em `docs/pages/` e os ADRs em `docs/decisions/`. O script `scripts/build_docs.py` gera o HTML em `docs/html/`, o menu, o índice de entidades e o índice da busca. Depois de editar uma fonte, rode:

```bash
python scripts/build_docs.py          # regenera tudo
python scripts/build_docs.py --check  # só confere se está em dia (o CI roda isto)
```

Para criar uma página nova, copie o cabeçalho de outra em `docs/pages/` (`title`, `group`, `section`, `order`, `description`) e escreva. Um título marcado com `{#id kind=classe file=caminho}` entra no índice de Entidades. O comando `juniper docs` serve esta pasta em [http://localhost:47831](http://localhost:47831). Detalhes em [Como contribuir](contribuindo.md#documentacao). O visual (terminal, verde e preto, tema único e a marca animada com o cursor piscando) é o mesmo do [dashboard](dashboard.md#visual) e do app; as cores vêm de `mobile/src/theme/tokens.ts` e os estilos estão em `docs/html/assets/style.css`.
