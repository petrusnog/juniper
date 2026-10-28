---
title: App mobile
group: Clientes
order: 6
description: O app da Juniper no celular Android (React Native): chat de texto, chat de voz contínuo, links do YouTube Music abertos no aparelho.
status: proposto
tags: mobile android react native app celular chat texto voz tailscale keystore token executor open_url autoplay kotlin foreground service
---

> **Nota:** esta página resume o [ADR-022](adr-022.html) (proposto), que **substitui** o [cliente desktop](desktop.html) em Electron. O app ainda está em planejamento: o que está descrito aqui é o desenho aprovado, não o que já roda.

## Visão geral {#visao-geral}

O app é um **cliente fino** ([Arquitetura](architecture.html#regras)): captura entrada e mostra saída, sem lógica de agente e sem acesso ao MongoDB. Ele fala com o cérebro por **WebSocket** dentro do Tailscale ([Produção](producao.html)), então o celular precisa do app do Tailscale ligado.

| Tela | O que faz |
|---|---|
| **Texto** | Lista de conversas, retomar, nova conversa, resposta em *streaming*, cancelar. O "sim" de uma confirmação é uma mensagem comum ([Agente](agente.html#confirmacao)). |
| **Voz** | Um botão abre uma conversa contínua: o microfone fica aberto, a Juniper entende, responde e fala, com legenda na tela e interrupção ([Voz por áudio](voz-audio.html)). |

## Decisões {#decisoes}

- **Só Android**, `minSdk` 26. **React Native puro** com TypeScript e módulos nativos em Kotlin; pasta `mobile/` no mesmo repositório.
- **Conexão:** `Authorization: Bearer` no handshake; o token vai para o **Android Keystore**, nunca em código ou log. Reconexão automática, estado da conexão sempre visível. Quando o [login com MFA](login.html) existir, o app abre sessão com o código sem refazer a conexão.
- **Executor:** o app registra `open_url` e **revalida a política de URL** com os mesmos casos de teste do cérebro ([Executor](executor.html#politica-url)).
- **Autoplay:** depois de abrir o link do YouTube Music, um módulo nativo manda "play" à sessão de mídia. Se falhar, o link já abriu e o Petrus aperta play ([Música](musica.html)).
- **Voz em segundo plano:** serviço em primeiro plano com notificação ("Juniper está ouvindo"); áudio de entrada com a fonte `VOICE_COMMUNICATION` (cancelamento de eco do aparelho; fone recomendado).
- **Tipos do protocolo gerados** de `docs/protocol/schema.json`; um teste falha se estiverem velhos.
- **Aparência:** os tokens do livro de bolso (paleta clara e escura, fonte monoespaçada para código).
- **Build local** de APK, instalado à mão na v1.

## O que os testes não provam {#limites}

Jest cobre a lógica (conexão, conversas, política de URL, tipos). Áudio, serviço em primeiro plano, autoplay e o comportamento do Android só se verificam **no aparelho**, pelo roteiro de aceite manual.

## Tasks {#tasks}

T-143 (base do app), T-144 (chat de texto), T-145 (executor e autoplay), T-146 (chat de voz), T-147 (documentação e aceite). Épica #178.
