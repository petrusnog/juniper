---
title: Cliente desktop
group: Produção
order: 4
description: O app Electron: stack, estrutura, segurança do renderer, token no cofre do sistema, conexão e empacotamento.
status: proposto
tags: electron typescript desktop app renderer preload safestorage vitest electron-builder react vite csp token tailscale interface
---

> **Nota:** esta página resume o [ADR-015](adr-015.html) (proposto). A Sprint 3 entrega só o **chat por texto** e o executor com `open_url`; voz, wake word, bandeja e atalhos globais ficam para a Sprint 4.

## Visão geral {#visao-geral}

O cliente desktop fala com o cérebro remoto e replica o que o CLI faz hoje, com uma interface gráfica. É um cliente **fino** ([Arquitetura](architecture.html#regras)): captura entrada, exibe saída, nunca executa lógica de agente nem acessa o MongoDB. O mesmo app é o **executor** do PC ([Executor](executor.html)).

## Stack {#stack}

- **Electron + TypeScript** (o usuário domina JavaScript; o executor precisa do sistema: abrir o navegador, cofre de credenciais).
- Interface do renderer: React com Vite (`electron-vite`) *(a confirmar)*. WebSocket no processo principal com `ws`; validação dos argumentos do executor com `ajv`.
- Interface em PT-BR; código, comentários e logs em inglês.
- Descartados: Tauri (lado nativo em Rust), Qt/Python (menos natural para JavaScript) e PWA (não abre o navegador nem guarda credenciais com segurança no sistema).

## Estrutura {#estrutura}

```text
desktop/
├── package.json, tsconfig.json, electron-vite.config.ts, eslint.config.js
├── src/
│   ├── main/       processo principal: janela, conexão WebSocket, executor, cofre do token
│   ├── preload/    a ponte mínima (contextBridge) entre main e renderer
│   ├── renderer/   a interface (React)
│   └── shared/     tipos do protocolo (gerados), constantes, política de URL
├── tests/          testes (vitest)
└── README.md
```

O renderer **não** toca no WebSocket, no token nem no sistema.

## Segurança do app {#seguranca}

O app mostra texto vindo de um LLM, então o renderer é tratado como **não confiável**:

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`; sem o módulo `remote`.
- **CSP** restritiva (`default-src 'self'`, sem `unsafe-eval`, sem origens remotas); nenhum conteúdo remoto na janela principal.
- **Navegação bloqueada** (`will-navigate`, `setWindowOpenHandler`); um clique num link passa por `shell.openExternal` só depois da mesma [política de URL](executor.html#politica-url).
- **Respostas da IA sempre como texto:** nunca `innerHTML` com conteúdo do LLM; formatação passa por um renderizador que sanitiza.
- **Ponte mínima**, com validação de entrada no main: `connection.getState()`, `chat.send(text)`, `chat.cancel()`, `session.list()/resume()/new()`, `onEvent(cb)`, `settings.get()/set()`.
- Instância única (`app.requestSingleInstanceLock()`); dependências fixadas por lockfile.

## Token e configurações {#token}

- O **token** fica no cofre do sistema (`safeStorage` do Electron: DPAPI no Windows, Keychain no macOS, o chaveiro no Linux), cifrado em `userData`. Nunca em texto puro, nunca no renderer, nunca em log. Sem chaveiro disponível, o app **não grava** o token e pede a cada execução *(a confirmar)*.
- Na primeira execução, uma tela pede o **endereço do servidor** (o nome da VPS na tailnet) e o **token**, testa a conexão e só então grava.
- **Nenhum dado de conversa** fica no cliente: o histórico vem do servidor (`session.resume`, [Protocolo](protocolo.html#sessoes)).
- Pré-requisito: o **Tailscale** instalado e logado nessa máquina ([Produção](producao.html#tailscale)). Se o servidor não for alcançável, o app mostra essa dica.

## Conexão {#conexao}

O processo principal é dono do WebSocket e implementa o [Protocolo](protocolo.html): handshake com token, `hello`/`welcome`, heartbeat, reconexão com backoff e jitter, retomada da conversa e os códigos de fechamento. Estados mostrados: `conectando`, `online`, `offline` (reconectando), `não autorizado` e `versão incompatível`. Offline, o app mostra "Juniper indisponível no momento" e **preserva o texto digitado**.

## Funcionalidades da Sprint 3 {#funcionalidades}

| Task | O que o app faz |
|---|---|
| T-088 | Abre, conecta, guarda o token e mostra o estado; CI do app. |
| T-089 | Chat por texto com streaming, confirmação na conversa (o "sim" é uma mensagem normal, sem botões), sessões (nova, retomar, listar) e cancelar a resposta. |
| T-090 | Executor com `open_url` e revalidação local, e o registro do que foi aberto. |

## Tipos, testes e CI {#testes}

- **Tipos do protocolo** gerados do JSON Schema da T-074 (`npm run gen:protocol`); o CI falha se o arquivo estiver desatualizado.
- **Testes** com `vitest` contra um servidor WebSocket de teste que simula o brain; a política de URL usa o mesmo conjunto de casos do teste Python. `eslint`, `prettier` e `tsc --noEmit` no CI.
- Um job próprio de CI (`desktop`) roda quando `desktop/**` ou `docs/protocol/**` muda; o `pytest` continua intocado.

## Empacotamento {#empacotamento}

`electron-builder`, alvos iniciais **Windows (NSIS)** e **Linux (AppImage)** *(macOS fora por enquanto, a confirmar)*. **Sem auto-update** na Sprint 3 e sem assinatura de código (o Windows pode mostrar o aviso de editor desconhecido). O artefato sai de um workflow manual ou localmente; não faz parte do deploy do brain.
