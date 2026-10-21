---
title: Livro de bolso da Juniper
group: Começando
order: 0
description: Documentação técnica da Juniper, organizada por tópico.
tags: início documentação home
---

```html-raw
<div class="hero"><img class="logo" src="assets/juniper.png" alt="Juniper: ilustração em preto e branco de uma mulher de óculos, com rabo de cavalo e blazer, sorrindo" width="128" height="128"><h1>Livro de bolso da Juniper</h1></div>
```

A Juniper é uma assistente autônoma pessoal: você fala em linguagem natural e ela decide quais ferramentas usar (agenda do Google, comandos git, memória), com failover entre provedores de LLM. Este livro é a referência técnica: o que cada peça é, como se encaixa e como usar.

> **Dica:** aperte <kbd>Ctrl</kbd>+<kbd>K</kbd> (ou <kbd>/</kbd>) em qualquer página para buscar por termo: nomes de classes (`Dispatcher`), opções do `config.yaml` (`retrieval.threshold`), comandos (`juniper memory`) ou assuntos (`deploy`).

## Por onde começar {#comecar}

- **Instalar e conversar:** [Instalação](install.html), depois o [CLI](cli.html).
- **Entender o desenho:** [Arquitetura](architecture.html) e o [Agente](agente.html).
- **Procurar uma classe ou tool:** o [índice de Entidades](entidades.html).
- **Saber o porquê de uma escolha:** as [Decisões (ADRs)](adr.html).
- **O que vem a seguir (produção, protocolo, desktop):** as páginas do grupo *Produção*, marcadas como `proposto`.

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

Para criar uma página nova, copie o cabeçalho de outra em `docs/pages/` (`title`, `group`, `order`, `description`) e escreva. Um título marcado com `{#id kind=classe file=caminho}` entra no índice de Entidades. O `juniper docs` serve esta pasta em [http://localhost:47831](http://localhost:47831).
