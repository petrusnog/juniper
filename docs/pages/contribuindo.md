---
title: Como contribuir
group: Ambiente Local
order: 1
description: O fluxo de trabalho (issue, branch, PR), as regras de arquitetura, os testes exigidos, as convenções de código e como manter a documentação em dia.
status: guia
tags: contribuir contribuição fluxo issue branch pull request pr squash merge testes pytest ruff mypy convenções definition of done adr documentação ci
---

Esta página reúne as regras para quem vai escrever código para a Juniper. O passo a passo para montar o ambiente está em [Primeiros passos](primeiros-passos.md).

## Fluxo de trabalho {#fluxo}

Todo trabalho nasce de uma **issue** do GitHub e termina num **pull request** para `master`.

1. **Issue.** Cada tarefa tem uma issue com um identificador no título (`[T-051] ...`), o escopo e os critérios de aceite. Use o modelo de feature (`.github/ISSUE_TEMPLATE/feature.md`). Tarefas pequenas e incrementais, com critério de aceite claro, são preferidas a mudanças grandes.
2. **Branch.** Crie a branch a partir de `master`, no padrão `feature/<TASK>`:

   ```bash
   git checkout master && git pull
   git checkout -b feature/T051
   ```

3. **Commits.** Prefixe com o identificador da tarefa: `T-051: adiciona truncamento de contexto`.
4. **Testes e documentação** acompanham o código no mesmo PR (veja abaixo).
5. **Pull request** direto para `master`, com o título `T-051: <descrição>`, a descrição preenchida pelo modelo (`.github/pull_request_template.md`: o que mudou, a issue com `Closes #N` e como foi testado).
6. **Merge** por *squash*, mantendo o título do PR: um commit por tarefa em `master`. A branch é apagada depois.

PRs empilhados (com base em outra branch que não `master`) não rodam o CI até serem redirecionados para `master`.

## Regras de arquitetura {#regras}

Estas regras só mudam com um ADR novo ([Decisões](adr.html)). Um PR que as quebre não é aceito.

| # | Regra | Fundamento |
|---|---|---|
| 1 | **O núcleo não depende dos clientes:** `brain/core` nunca importa `brain/clients`. | [ADR-007](../decisions/ADR-007.md) |
| 2 | **Cliente fino:** captura entrada e mostra saída. Sem lógica de agente, sem acesso ao MongoDB. | [ADR-012](../decisions/ADR-012.md) |
| 3 | **Confirmação é conversa**, interpretada por `brain/core/consent.py`, nunca pelo LLM nem pelo cliente. Uma confirmação pendente **não sobrevive** a retomada, queda ou troca de conexão. | [ADR-010](../decisions/ADR-010.md), [ADR-012](../decisions/ADR-012.md) |
| 4 | **Tools** seguem o schema, têm tier `READ`/`WRITE`/`DESTRUCTIVE`/`FORBIDDEN` e a política de segurança. Tool que roda no dispositivo tem `locality = EXECUTOR`; o cérebro não escala tier e o cliente revalida tudo. Tool desconhecida do servidor tem piso `WRITE`. | [ADR-002](../decisions/ADR-002.md), [ADR-006](../decisions/ADR-006.md), [ADR-014](../decisions/ADR-014.md) |
| 5 | **Protocolo:** mudanças compatíveis não mudam `protocol_version`; o schema gerado (`docs/protocol/schema.json`) tem de estar em dia. | [ADR-012](../decisions/ADR-012.md) |
| 6 | **Segredos** só em `.env`, GitHub Secrets ou arquivos montados; nunca em código, imagem ou log. | [ADR-003](../decisions/ADR-003.md) |
| 7 | **Produção:** nenhuma porta pública; acesso pela rede privada; o deploy nunca apaga volumes. | [ADR-013](../decisions/ADR-013.md) |
| 8 | **Falhas esperadas viram resultado tipado** (`ToolResult(success=False)`, `error` do protocolo), nunca exceção que derruba a conexão ou o servidor. | [ADR-002](../decisions/ADR-002.md), [ADR-012](../decisions/ADR-012.md) |
| 9 | **Um só ponto de configuração:** nenhum módulo lê variável de ambiente, YAML ou `.env` diretamente; tudo vem de `brain.config.settings`. | [ADR-003](../decisions/ADR-003.md) |
| 10 | **Providers trocáveis:** o agente fala com `LLMProvider`, nunca com um provedor específico. | [ADR-001](../decisions/ADR-001.md), [ADR-009](../decisions/ADR-009.md) |

## Testes {#testes}

Todo PR que altera código traz **testes automatizados funcionais** em `pytest`, cobrindo os critérios de aceite da issue. PRs que alteram apenas `docs/` são isentos.

```bash
pip install -e ".[dev]"
pytest -m "not integration"   # o que o CI roda
pytest                        # tudo, inclusive integração (MongoDB e modelo reais)
```

- Testes que dependem de infraestrutura externa (MongoDB, download de modelo) recebem `@pytest.mark.integration` e ficam fora do CI padrão.
- Nenhum teste pode depender de rede, de chave de API real ou do `HOME` de quem desenvolve: use *fixtures* e *mocks* (`httpx.MockTransport`, providers falsos; veja `tests/conftest.py`).
- Testes assíncronos dispensam decorator (`pytest-asyncio` em modo `auto`).
- Mudou o app Android? Rode também `npm test`, `npm run typecheck` e `npx eslint .` dentro de `mobile/` ([Ambiente mobile](mobile-dev.md#testes)).

## Qualidade de código {#qualidade}

```bash
ruff check brain/ tests/    # lint (line-length 100; regras E F I N W UP B C4 SIM)
mypy brain/                 # tipos (strict)
```

| Convenção | Regra |
|---|---|
| Idioma | Código, comentários, docstrings e logs em **inglês**; textos para o usuário final em **português do Brasil**. |
| Tipos | `mypy --strict` passa; `from __future__ import annotations` nos módulos. |
| Async | I/O de LLM, rede e tools é `async`. Bibliotecas síncronas rodam em `asyncio.to_thread`. |
| Estilo | Pragmático: priorize o que funciona; sem abstração especulativa. |
| Nomes | `snake_case` para funções e variáveis, `PascalCase` para classes, `UPPER_CASE` para constantes. |
| Docstrings | Estilo Google. |
| Segredos | Nunca logar nem imprimir chaves, tokens ou senhas. Mensagens de erro são montadas pelo código, nunca copiadas cruas de um SDK. |

## Definition of Done {#dod}

Uma tarefa está pronta quando:

1. Os critérios de aceite da issue são atendidos.
2. `pytest -m "not integration"`, `ruff check brain/ tests/` e `mypy brain/` passam.
3. Há testes novos cobrindo o caminho feliz e os erros.
4. A documentação (`docs/pages`) e, se houve decisão de arquitetura, um ADR (`docs/decisions/ADR-NNN.md`) foram atualizados.
5. O HTML da documentação foi regenerado (`python scripts/build_docs.py`).

## CI {#ci}

| Workflow | Quando roda | O que faz |
|---|---|---|
| `tests.yml` | PR e push em `master` | `pytest -m "not integration"` em Python 3.12. |
| `docker.yml` | Quando a imagem muda | Constrói a imagem do cérebro. |
| `mobile.yml` | Quando `mobile/` ou o schema do protocolo muda | Testes, tipos e lint do app Android. |
| `docs.yml` | Quando `docs/html` muda em `master` | Publica o livro no GitHub Pages. |
| `deploy.yml` | Depois do `tests.yml` verde em `master` | Constrói a imagem e faz o deploy ([Deploy contínuo](deploy-continuo.md)). |

Proteja `master` exigindo PR e o check **pytest** (*Settings → Branches*): com deploy automático, um merge com teste vermelho iria direto para a produção.

## Decisões de arquitetura (ADRs) {#adrs}

Uma mudança que afete a arquitetura (uma regra acima, o protocolo, o modelo de segurança, a infraestrutura) é registrada como ADR em `docs/decisions/ADR-NNN.md`, com status, contexto, decisão, consequências e alternativas descartadas. O índice está em [Decisões (ADRs)](adr.html). Em escolhas de design com trade-offs reais, apresente as opções na issue antes de implementar.

## Documentação {#documentacao}

O livro de bolso (`docs/html`, servido por `juniper docs` e publicado no GitHub Pages) é **gerado**: não edite os `.html`. As fontes são:

- `docs/pages/*.md`: as páginas, cada uma começando com `title`, `group`, `order` e `description` (e, opcionalmente, `section`, `status` e `tags`). Os grupos são as trilhas do menu (Introdução, Ambiente Local, Produto, Documentação Técnica, Referência e Produção). Um título marcado com `{#id kind=classe file=caminho}` entra no índice de [Entidades](entidades.html) e na busca;
- `docs/decisions/ADR-*.md`: os ADRs (viram `adr-NNN.html` sozinhos).

Depois de editar uma fonte, rode `python scripts/build_docs.py` e versione o resultado. O teste `tests/test_docs.py` falha se o HTML estiver desatualizado, se houver link ou âncora quebrados, se uma página citar um arquivo de `tests/` ou `scripts/` que não existe ou se uma opção do `config.yaml` não estiver documentada em [Configuração](configuracao.md).

Ao escrever: linguagem expositiva, em português, para quem chega sem contexto do projeto. Quando uma página afirma uma regra de comportamento, cite o ADR que a fundamenta.

### Notas ligadas, mapa e Obsidian {#obsidian}

As fontes são uma rede de notas: páginas e ADRs se ligam por links Markdown relativos para o `.md` (`[Agente](agente.md#dispatcher)`, `[ADR-007](../decisions/ADR-007.md)`), nunca por `.html` nem `[[wikilink]]`. O gerador converte os links em `.html`; o [Obsidian](https://obsidian.md) os resolve e desenha o grafo. Para ver a arquitetura como um mapa de relações, abra a pasta `docs/` como cofre no Obsidian (a configuração em `docs/.obsidian` já filtra o HTML gerado e colore as trilhas) e abra o *Graph view*.

Três regras mantêm a rede coerente: um conceito é explicado num lugar só e as outras notas apontam para ele; toda página termina com uma seção *Para saber mais* com as notas vizinhas; e nenhuma nota fica órfã (o teste confere). O `docs/MAPA.md`, gerado junto com o HTML, é o índice de uma linha por nota; `python scripts/docs_search.py "pergunta"` busca nas seções sem abrir as páginas, com o mesmo índice que a Juniper usa para responder sobre si mesma ([Metadocumentação](metadocs.md)).

## Para saber mais {#para-saber-mais}

- [Introdução](introducao.md) (Introdução): O que é a Juniper, o que ela sabe fazer hoje, como é construída e quais trilhas seguir para aprender mais.
- [Primeiros passos](primeiros-passos.md) (Ambiente Local): Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o código atualizando em tempo real.
- [Ambiente Docker](dev-docker.md) (Ambiente Local): Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar logs e banco, e os problemas comuns.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Metadocumentação](metadocs.md) (Documentação Técnica): Como a Juniper responde sobre a própria arquitetura: detector de perguntas por palavras-chave, busca BM25 local na documentação, contexto automático e a tool docs_search.
- [Deploy contínuo](deploy-continuo.md) (Produção): Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca de versão, healthcheck e rollback automático.
