---
title: Dashboard
group: Clientes
order: 5
description: O painel web da Juniper: memórias, afetos, estudos e notas, memória central e pesquisa, somente leitura, em qualquer dispositivo da tailnet.
status: proposto
tags: dashboard web painel tailnet memória afetos estudos notas pesquisa responsivo adr-020
---

> **Nota:** o dashboard está sendo construído por fases (épica #159). Esta página descreve o que já existe e o que vem a seguir. A decisão está no [ADR-020](adr-020.html).

## Visão geral {#visao-geral}

Um painel web, **somente leitura**, para ver a memória da Juniper sem o terminal. Roda dentro do servidor (`juniper serve`), na porta **8766**, e abre em qualquer navegador de qualquer dispositivo da tailnet:

```text
http://<IP-da-tailnet-da-VPS>:8766/
```

O navegador nunca fala com o MongoDB: tudo passa pela API do próprio cérebro. Nada neste painel escreve no banco.

## O que já existe {#hoje}

- **Servidor e login (T-130):** a tela de login pede o token do servidor (o mesmo dos clientes, `~/.juniper/token`). O token **nunca vai na URL**: vai num cabeçalho, e o servidor devolve um cookie `HttpOnly` que vale `dashboard.session_hours` (12 horas). Cinco tentativas erradas em cinco minutos bloqueiam aquele cliente por um tempo.
- **Defesas:** o cabeçalho `Host` precisa ser um IP, `localhost` ou um nome `*.ts.net` (ou um host de `dashboard.allowed_hosts`); CSP restritiva (nada inline nem externo); sem CORS; só `GET`.
- **Isolamento:** se a porta estiver ocupada ou uma rota falhar, o servidor e a conversa seguem funcionando; o problema vai para o log.

## O que vem a seguir {#depois}

Visão geral e Afetos (com rastreio até a fala), Memórias, Memória central (núcleo, personalidade, crenças e o simulador do prompt), Estudos (com cada nota), Sonhos, Conversas e Pesquisa.

## Configuração {#configuracao}

Veja as chaves `dashboard.*` em [Configuração](configuracao.html).

## Na VPS {#vps}

O compose de produção publica `${TAILSCALE_IP}:8766`, nunca em `0.0.0.0`. **A ACL do Tailscale precisa liberar a porta 8766** para os seus dispositivos (hoje ela só libera a `8765` e o SSH). Para HTTPS (instalar como app no celular, por exemplo), use `tailscale serve` na VPS; o código não muda.
