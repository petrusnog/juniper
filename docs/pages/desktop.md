---
title: App desktop
group: Documentação Técnica
section: Clientes
order: 25
description: O app desktop da Juniper (Electron, Windows): conexão e login, chat de texto, executor open_url e as tools de arquivo, com a fronteira de plataforma e as proteções de caminho.
status: proposto
tags: desktop electron windows executor arquivos fs_list fs_read fs_write fs_trash lixeira caminhos junção plataforma PlatformRules safeStorage dpapi open_url chat
---

> **Nota:** esta página detalha o [ADR-033](../decisions/ADR-033.md), que reabre o desktop descartado no [ADR-015](../decisions/ADR-015.md). Código em `desktop/` (veja o `README.md` de lá para rodar e testar). **Voz e empacotamento ainda não existem** (ver [Limites](#limites)).

## Visão geral {#visao-geral}

O desktop é um cliente **fino** como o [app mobile](mobile.md): captura a entrada, mostra a saída e cumpre as ordens do cérebro como [executor](executor.md). Não tem lógica de agente, não toca no MongoDB e a confirmação de ações é conversa, interpretada no cérebro ([ADR-010](../decisions/ADR-010.md)); o "sim" é uma mensagem comum.

Para a web, o cérebro continua lendo pelas suas tools (`web_search`, `web_fetch`, `learn_topic`, [ADR-031](../decisions/ADR-031.md)). O desktop só **mostra**: o `open_url` abre a URL no navegador padrão. Não há navegador embutido nem automação de página.

## Camadas {#camadas}

```text
src/core/      TypeScript puro: conexão, chat, executor, política de URL e de caminhos
src/main/      Electron: janela, IPC, cofre, tools de arquivo, platform/ (o único que conhece o SO)
src/preload/   a ponte mínima
src/renderer/  a janela (texto puro)
```

O `core` não importa Electron nem `fs`/`os`/`path`, e só `src/main/platform/` olha o sistema operacional: o ESLint do `desktop/` falha o CI se alguém violar. O que depende do SO fica atrás de **`PlatformRules`** (`src/core/platform.ts`): pasta do usuário, lista de bloqueio, regras de nome e de comparação de caminhos e a canonização real. A v1 tem uma implementação, `win32.ts`, que usa `path.win32` explicitamente; acrescentar Linux ou macOS é implementar a mesma interface e rodar os mesmos casos hostis.

## Conexão e login {#conexao}

`BrainConnection` abre o WebSocket com `Authorization: Bearer client_id.client_secret` ([ADR-027](../decisions/ADR-027.md)), manda `hello` com `kind = "desktop"` e `capabilities = {"fs": true}`, responde ao `ping`, reconecta com recuo e mostra o estado (conectando, online, offline, credencial recusada, versão incompatível). `ws://` só vale na faixa do Tailscale (`100.64.0.0/10`) ou em `*.ts.net`; fora dela é `wss://` com validação de certificado. A credencial fica criptografada com `safeStorage` (DPAPI); sem criptografia disponível o app **recusa o login** em vez de guardar em texto. A DPAPI não protege contra um programa malicioso rodando como o mesmo usuário (limite aceito).

## Janela e segurança do Electron {#janela}

`contextIsolation`, `sandbox`, sem `nodeIntegration`, `webSecurity`, CSP sem `unsafe-inline`, `unsafe-eval` nem rede (`connect-src 'none'`), navegação e janelas bloqueadas, permissões negadas, instância única que ignora argumentos, IPC só de quem for a janela principal. O renderer escreve tudo com `textContent`. Os testes de `desktop/tests/main.test.ts` conferem essas linhas no código-fonte, porque o Electron não sobe no CI de Linux.

## Executor {#executor}

O `Executor` registra as tools a cada `welcome` e, em cada `invoke`, revalida nesta ordem ([Executor](executor.md#revalidacao)): `unknown_tool`, `invalid_args`, `tier_mismatch` e, dentro da tool, `policy_denied`. Toda recusa é um `result` tipado. Um `call_id` já respondido não é executado de novo por dez minutos. O botão «pausar arquivos» tira as `fs_*` do catálogo; o cérebro não o controla. A atividade (tool, resultado, código; nunca caminho nem conteúdo) vai para a tela e para `activity.log`.

### open_url {#open-url kind=tool file=desktop/src/core/executor/openUrl.ts}

Aplica a mesma [política de URL](executor.md#politica-url) do cérebro e do celular (os três rodam o mesmo corpus, `tests/fixtures/url_policy_cases.json`) e abre sempre a URL **normalizada** com `shell.openExternal`.

## Tools de arquivo {#arquivos file=desktop/src/main/executor/fs/tools.ts}

| Tool | Tier | O que faz |
|---|---|---|
| `fs_list`, `fs_search`, `fs_read` | `READ` | listar (500), buscar por nome (200, 5 s), ler texto (1 MB por chamada) |
| `fs_write`, `fs_mkdir`, `fs_move`, `fs_copy` | `WRITE` | criam; **nunca sobrescrevem** (`destination_exists`) |
| `fs_replace` | `DESTRUCTIVE` | substitui; o original vai antes para a Lixeira |
| `fs_trash` | `DESTRUCTIVE` | Lixeira; pasta só com `recursive` e até 200 itens |

As tools são declaradas no cérebro (`brain/tools/files.py`): o schema e o tier vêm de lá, e o servidor só aceita `fs_*` de um desktop com a capacidade `fs`, com o mesmo tier. Do lado do cérebro, o texto lido volta em `<conteudo_arquivo>` (dado, não instrução), não vai para o log (só tamanho e `sha256`), contamina o turno (depois de ler, `web_fetch`, `web_search` e `open_url` pedem confirmação e a memória recusa guardar) e segredos detectados não são devolvidos. Um turno que não veio do desktop pede confirmação com o nome do PC, mesmo para ler. Veja o [ADR-033](../decisions/ADR-033.md) §4.

### Política de caminhos {#caminhos file=desktop/src/core/policy/paths.ts}

O caminho aprovado é o caminho usado. O cérebro mostra o caminho como será enviado (não o canoniza, porque roda em Linux); o desktop recusa a sintaxe perigosa (UNC, `\\?\`, `/`, `..`, variáveis, fluxos `:`, nomes reservados, ponto ou espaço final, nomes 8.3) e depois pede ao SO o caminho **real**. Para ler, o caminho real precisa estar na pasta do usuário e fora das listas fixas. Para alterar, precisa ser **exatamente** o aprovado, sem junção, link ou *reparse point* no meio (`path_changed`). As listas (pastas bloqueadas, nomes sensíveis como `.env`, `*.kdbx`, `id_rsa`, e nomes que executam código como `.exe`, `.bat`, `.lnk`, além de `.git/hooks` e `.vscode`) são do código do desktop, nunca vêm do cérebro.

## Limites {#limites}

- **Não existe ainda:** voz no desktop (T-201) e o instalador com atualização assinada (T-202).
- **Não verificado em um Windows real nesta etapa:** os testes de NTFS (junção, *hard link*, ADS) só rodam no job `windows-latest`, e a janela do Electron nunca foi aberta neste ambiente. O que foi exercitado é o núcleo, as tools contra um disco real em Linux e o corpus de caminhos do Windows com uma sonda falsa.
- A conferência do caminho depois de abrir o arquivo usa a canonização do SO; o Node não expõe o caminho final do *handle*, então uma troca de junção entre as duas conferências é reduzida, não eliminada.
- O limite de leitura por «turno» é uma janela de dois minutos, porque o executor não vê os turnos.

## Para saber mais {#para-saber-mais}

- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Tools](tools.md) (Documentação Técnica): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
- [Usuários e credenciais](usuarios.md) (Documentação Técnica): Como a Juniper sabe quem fala com ela: o cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash), a migração do token estático e o que ainda não existe (isolamento por usuário).
