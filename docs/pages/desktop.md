---
title: App desktop
group: Documentação Técnica
section: Clientes
order: 25
description: O app desktop da Juniper (Electron, Windows): conexão e login, chat de texto e de voz, executor open_url e as tools de arquivo, atualização assinada, com a fronteira de plataforma e as proteções de caminho.
status: proposto
tags: desktop electron windows executor arquivos fs_list fs_read fs_write fs_trash lixeira caminhos junção plataforma PlatformRules safeStorage dpapi open_url chat
---

> **Nota:** esta página detalha o [ADR-033](../decisions/ADR-033.md), que reabre o desktop descartado no [ADR-015](../decisions/ADR-015.md). Código em `desktop/` (veja o `README.md` de lá para rodar e testar). A voz e a atualização estão implementadas, mas **nada do app foi aberto num Windows real** (ver [Limites](#limites)).

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

`contextIsolation`, `sandbox`, sem `nodeIntegration`, `webSecurity`, CSP sem `unsafe-inline`, `unsafe-eval` nem rede (`connect-src 'none'`), navegação e janelas bloqueadas, permissões negadas, instância única que ignora argumentos, IPC só de quem for a janela principal. A página é servida por um protocolo próprio, `app://juniper/` (só a pasta do renderer, só `.html`, `.js` e `.css`, sem sair dela), e não por `file://`: com o *fuse* `GrantFileProtocolExtraPrivileges` desligado, uma página `file://` dentro do `asar` simplesmente não carrega (a janela fica preta). O renderer escreve tudo com `textContent`. Os testes de `desktop/tests/main.test.ts` conferem essas linhas no código-fonte. O app empacotado (com os *fuses*) foi aberto sob Xvfb e inspecionado por CDP: a página carrega, a ponte existe e o `AudioWorklet` abre por `app://`; **só o app de Windows real continua sem teste**.

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

As tools são declaradas no cérebro (`brain/tools/files.py`): o schema e o tier vêm de lá, e o servidor só aceita `fs_*` de um desktop com a capacidade `fs`, com o mesmo tier. Do lado do cérebro, o texto lido volta em `<conteudo_arquivo>` (dado, não instrução), não vai para o log (só tamanho e `sha256`), contamina a conversa (depois de ler, `web_fetch`, `web_search`, `open_url` e a busca de música pedem confirmação, e a memória, os lembretes e o gosto musical recusam guardar) e segredos detectados não são devolvidos. Um turno que não veio do desktop pede confirmação com o nome do PC, mesmo para ler, e a ação aprovada só vai para esse PC. O *wire log* dos provedores de LLM também omite o texto dos arquivos. Veja o [ADR-033](../decisions/ADR-033.md) §4.

### Política de caminhos {#caminhos file=desktop/src/core/policy/paths.ts}

O caminho aprovado é o caminho usado. O cérebro mostra o caminho como será enviado (não o canoniza, porque roda em Linux); o desktop recusa a sintaxe perigosa (UNC, `\\?\`, `/`, `..`, variáveis, fluxos `:`, nomes reservados, ponto ou espaço final, nomes 8.3) e depois pede ao SO o caminho **real**. Para ler, o caminho real precisa estar na pasta do usuário e fora das listas fixas. Para alterar, precisa ser **exatamente** o aprovado, sem junção, link ou *reparse point* no meio (`path_changed`). `fs_write` e o texto novo de `fs_replace` só gravam `.txt`, `.md`, `.csv` e `.json`. As listas (pastas bloqueadas, nomes sensíveis como `.env`, `*.kdbx`, `id_rsa`, e nomes que executam código como `.exe`, `.bat`, `.lnk`, além de `.git/hooks` e `.vscode`) são do código do desktop, nunca vêm do cérebro.

## Chat de voz {#voz}

O botão «voz» abre a mesma sessão contínua do celular ([Voz por áudio](voz-audio.md), [ADR-023](../decisions/ADR-023.md)): o cérebro detecta o fim da fala, transcreve, responde e fala. O `VoiceController` (`src/core/voice/`) é o do app mobile portado e roda no processo principal; o microfone e o alto-falante ficam na janela (`src/renderer/audio.ts`), ligados por IPC (`RemoteAudio`). O microfone usa o cancelamento de eco e a supressão de ruído do Chromium e manda PCM 16 bits mono a 16 kHz em quadros de 100 ms; a fala dela é reproduzida na taxa que o cérebro declara. O desktop não sabe se há fone, então a caixa «uso fone» diz isso ao cérebro: sem fone o microfone fica fechado enquanto ela fala (e por 1 s depois), com fone dá para interrompê-la falando. Não há STT nem TTS do sistema como reserva (o cérebro cuida disso). A permissão de microfone é concedida só à página do app e só para áudio; câmera, tela e o resto seguem negados. O processo principal descarta qualquer evento de áudio da janela fora do formato, e a janela não enfileira mais de um minuto de fala à frente.

## Atualização {#atualizacao}

O cérebro serve o instalador pela rede privada, no molde do [ADR-026](../decisions/ADR-026.md): `GET /desktop/update` (versão, tamanho, SHA-256) e `GET /desktop/update/installer`, ambos com a credencial, a partir de `Juniper-Setup.exe` e `desktop-latest.json` na pasta do app (`scripts/publish_desktop.sh`, chamado pelo workflow «Desktop release»; até 300 MB, porque o arquivo é lido inteiro). O app pergunta ao conectar e a cada seis horas; havendo versão mais nova, mostra o botão «Atualizar» (nunca instala sozinho).

O download vai para uma pasta privada do app (não para `%TEMP%`), com nome aleatório e criação exclusiva, sem seguir redirecionamentos e só se o tamanho anunciado bater; um download que falha não deixa arquivo. Antes de executar, o app confere, nesta ordem: o tamanho, o SHA-256, uma **assinatura Authenticode válida cujo nome simples de assinante (o do Windows, não um trecho de texto) seja exatamente o do certificado** com que esta versão foi feita (`JUNIPER_PUBLISHER`, gravado na compilação) e a **versão lida do próprio arquivo assinado**, que precisa ser igual à anunciada e maior que a instalada (um instalador antigo, ainda assinado, não passa por versão nova). Logo antes de executar, o SHA-256 é calculado de novo. Qualquer falha apaga o arquivo e não instala nada. O app só fecha depois de o instalador realmente iniciar; um erro ao iniciá-lo aparece na tela. Uma compilação sem o nome do assinante (a de desenvolvimento) **nunca se atualiza**. O instalador é um NSIS por usuário, com *fuses* do Electron fechados (inclusive os privilégios extras de `file://`) e `asar` com validação de integridade.

## Limites {#limites}

- **Nada disto foi aberto num Windows real:** a voz (microfone, alto-falante, eco), o instalador NSIS, a assinatura e a atualização foram escritos e testados com dublês e com o corpus de unidade, mas o `electron-builder`, o `Get-AuthenticodeSignature` e o workflow «Desktop release» nunca executaram. O primeiro run (o push do merge) parou na trava do certificado, que era o esperado; agora um push sem certificado só pula o release, com um aviso, e o disparo manual sem certificado falha de propósito. Os passos de compilar, assinar e publicar seguem sem ter rodado. A configuração do `electron-builder` foi aceita pelo validador dele; só isso foi conferido.
- **Riscos da atualização que ficam:** quem já roda código como o mesmo usuário ainda pode trocar o arquivo na janela entre o último SHA-256 e o início do instalador (estreita, não fechada); o assinante é identificado pelo nome, sem fixar o *thumbprint* do certificado; a revogação do certificado depende do que o `Get-AuthenticodeSignature` do Windows fizer (não conferido); o certificado vai como `.pfx` em segredo do repositório (o ideal seria um *environment* protegido ou assinatura em nuvem) e as ações de terceiros do workflow estão fixadas por tag, não por SHA.
- A atualização depende de um certificado de assinatura de código que ainda não existe: sem os segredos `WIN_CSC_*` e a variável `JUNIPER_PUBLISHER` o workflow para, e o app instalado não se atualiza.
- **Não verificado em um Windows real nesta etapa:** os testes de NTFS (junção, *hard link*, ADS) só rodam no job `windows-latest`, e a janela do Electron nunca foi aberta neste ambiente. O que foi exercitado é o núcleo, as tools contra um disco real em Linux, o corpus de caminhos do Windows com uma sonda falsa, a ponte de áudio com dublês e a verificação do instalador (tamanho, SHA-256, assinante) com portas falsas.
- A conferência do caminho depois de abrir o arquivo usa a canonização do SO; o Node não expõe o caminho final do *handle*, então uma troca de junção entre as duas conferências é reduzida, não eliminada.
- O limite de leitura por «turno» é uma janela de dois minutos, porque o executor não vê os turnos.
- **`kind` e `capabilities` são declarados pelo próprio cliente.** Uma credencial roubada de outro aparelho pode se declarar desktop com `fs`; hoje isso só rende algo se o PC verdadeiro estiver offline (com dois candidatos o `invoke` falha por ambiguidade). Fechar isso exige ligar o tipo do dispositivo à credencial no cadastro (ADR-027).
- Só links simbólicos e junções contam como «atalho» (`lstat`); outros *reparse points* (OneDrive, WSL) não são detectados, mas um caminho real diferente ainda barra as alterações.
- A leitura por *hard link* e a cópia de um arquivo com vários nomes não são bloqueadas; só alterar, mover e apagar são.
- O botão «pausar arquivos» volta a «ligado» quando o app reinicia, e o `activity.log` não tem rotação.
- Um «sim» pode cobrir mais de cinco ações destrutivas se elas vierem em mensagens diferentes do modelo no mesmo turno (o teto é por mensagem).

## Para saber mais {#para-saber-mais}

- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Tools](tools.md) (Documentação Técnica): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
- [Usuários e credenciais](usuarios.md) (Documentação Técnica): Como a Juniper sabe quem fala com ela: o cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash), a migração do token estático e o que ainda não existe (isolamento por usuário).
