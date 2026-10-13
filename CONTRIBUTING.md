# Contribuindo com a Juniper

Fluxo de desenvolvimento e de merge para `master`. Todo trabalho nasce de uma
issue do GitHub.

## Fluxo

1. **Issue.** Cada task tem uma issue com ID no título (`[T-051] ...`), escopo e
   critérios de aceite. Use o template de feature.
2. **Branch.** Crie a branch a partir de `master`, no padrão `feature/<TASK>`:

   ```bash
   git checkout master && git pull
   git checkout -b feature/T051
   ```

3. **Commits.** Prefixe com o ID da task, no mesmo estilo do histórico:
   `T-051: adiciona truncamento de contexto`.
4. **Testes.** Escreva os testes `pytest` junto com o código (veja abaixo).
5. **Pull Request** direto para `master`:
   - Título: `T-051: <descrição>`.
   - A descrição explica **o que foi alterado** (o template já traz as seções).
   - Inclua `Closes #<número da issue>` para fechar a issue ao mergear.
6. **Merge.** *Squash merge*, mantendo o título `T-051: <descrição>`. Um commit por
   task em `master`. Apague a branch depois do merge.

## Testes

Todo PR que altera código precisa trazer **testes automatizados funcionais** em
`pytest`, cobrindo os critérios de aceite da issue. PRs que alteram apenas `docs/`
são isentos.

```bash
pip install -e ".[dev]"
pytest                       # tudo
pytest -m "not integration"  # o que roda no CI
```

- Testes que dependem de infraestrutura externa (MongoDB, download de modelo etc.)
  recebem o marcador `@pytest.mark.integration` e ficam fora do CI padrão.
- Testes não podem depender de rede, de chaves de API reais ou do `HOME` do
  desenvolvedor: use *fixtures* e *mocks* (veja `tests/conftest.py`).

## CI e proteção do `master`

O workflow `.github/workflows/tests.yml` roda o `pytest` em todo PR. Para que o
merge só aconteça com testes passando, a proteção do `master` deve exigir:

- Pull request antes de mergear (sem push direto);
- O check **pytest** aprovado.

Configuração em *Settings → Branches → Branch protection rules* (ou *Rulesets*).

## Decisões de arquitetura

Mudanças que afetam a arquitetura são registradas como ADR em `docs/decisions/`
(`ADR-NNN.md`), com status, contexto, decisão e consequências.
