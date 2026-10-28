---
title: Login com MFA
group: Documentação Técnica
section: Núcleo
order: 7
description: O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
status: proposto
tags: login mfa totp autenticação sessão token recuperação telegram dashboard segurança identidade auth
---

> **Nota:** esta página resume o [ADR-021](adr-021.html) (proposto). **Nada aqui liga o MFA por padrão**; ainda é desenho.

## Por que {#por-que}

Hoje o WebSocket e o dashboard confiam em um token estático, e o Telegram só em uma lista de IDs. Quem controlar a conta do Telegram fala com a Juniper com todas as tools. A proposta mantém o Telegram completo e fortalece a **garantia de que é o dono**: uma identidade única, com segundo fator, para **todo** cliente. Junto com a rede privada ([Visão geral da produção](producao.html)), são duas camadas independentes.

## Como fica {#como-fica}

- **Serviço único** `brain/auth/`: decide quem está autenticado; o cliente só leva a credencial; o LLM nunca vê segredos.
- **TOTP** (app autenticador), tolerância de ±1 passo, **código usado não vale de novo**.
- **O token estático** passa a significar "este dispositivo é meu"; com o MFA ligado ele sozinho não basta.
- **Sessão** com prazo, ligada ao dispositivo e ao canal, **revogável**, guardada só como hash em arquivos sob `~/.juniper/auth/` (sem depender do MongoDB).
- **Recuperação:** 10 códigos de uso único e, em emergência, `juniper auth reset` por SSH na VPS. Não existe "esqueci o código" remoto.

| Cliente | Autenticação |
|---|---|
| WebSocket (CLI, app) | Bearer no handshake + `auth.login` com o código; até lá a conexão fica "locked". |
| Telegram | `/login <código>` (a mensagem é apagada), sessão por usuário e chat, expira por inatividade. A lista de IDs vira filtro prévio. |
| Dashboard | Token **e** código; o cookie leva o token de sessão. |
| Voz | Herda a conexão; a voz nunca é fator ([Voz por áudio](voz-audio.html)). |

## Regras que não mudam {#regras}

O MFA **não substitui a confirmação** ([Agente](agente.html#confirmacao)). Falha de login é resultado tipado e nunca derruba o servidor. Códigos e tokens de sessão são mascarados no log, com auditoria sem segredos.

## Migração {#migracao}

`auth.mfa.enabled` começa **desligado**; o usuário cadastra o autenticador e confere tudo com o MFA ainda desligado; só então liga. Desligar é editar o `config.yaml` por SSH.

