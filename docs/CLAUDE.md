# Documentação: convenções para escrever

Carregado quando você mexe em `docs/`. Para *ler* a documentação, veja a seção "Consultar a
documentação" do `CLAUDE.md` da raiz (mapa → busca → seção).

## Estrutura

- `pages/*.md`: o livro de bolso (como o sistema é **hoje**). Cabeçalho obrigatório: `title`,
  `group`, `order`, `description` (uma frase: aparece no mapa e na busca); opcionais `section`,
  `status`, `tags`.
- `decisions/ADR-NNN.md`: o histórico de cada decisão. Não reescreva um ADR aceito; acrescente uma
  atualização datada no topo ou crie outro.
- `MAPA.md`, `html/`: **gerados** por `python scripts/build_docs.py`. Não edite à mão.
- `.obsidian/`: configuração do Obsidian (visualizador do grafo); só `app.json` e `graph.json`
  vão para o git.

## Links (é o que forma o grafo)

- **Sempre links Markdown relativos para o `.md`**, nunca `[[wikilink]]` nem `.html`:
  `[Agente](agente.md#dispatcher)`, de um ADR para uma página `[Agente](../pages/agente.md)`, de
  uma página para um ADR `[ADR-007](../decisions/ADR-007.md)`. O Obsidian resolve esses caminhos
  e o gerador os converte em `.html`. As únicas exceções são as páginas geradas: `adr.html`,
  `entidades.html`, `status.html`.
- **Um conceito, um lugar.** Explique numa nota e *aponte* das outras; não copie o parágrafo.
- Cite o ADR que fundamenta um comportamento na primeira vez em que ele aparece na página.
- Toda página termina com `## Para saber mais {#para-saber-mais}`: 3–6 notas vizinhas, uma por
  linha, `- [Título](arquivo.md) (Grupo): descrição`. Página nova entra nas listas das vizinhas.
- Uma nota sem nenhum link entrando ou saindo (órfã) quebra o teste de docs.
- Âncoras: `## Título {#id}`. Títulos técnicos levam `kind=` e `file=` (`{#id kind=classe
  file=brain/x.py}`), que viram a lista de entidades e o atalho para o código.

## Escrita que economiza tokens de quem lê

- Abra a página com a visão geral (3–5 linhas) e deixe o detalhe em seções curtas e nomeadas.
- A `description` diz **o que a nota responde**, não o assunto genérico.
- Prefira tabela a parágrafo para comparar; prefira link a repetição.
- Nada de datas, nomes de pessoas ou "foi decidido por": a documentação é expositiva.

## Antes de commitar

```bash
python scripts/build_docs.py && pytest tests/test_docs.py tests/test_metadocs.py -q
```
