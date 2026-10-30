---
title: Login com MFA
group: Documentação Técnica
section: Núcleo
order: 7
description: O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
status: proposto
tags: login mfa totp autenticação sessão token recuperação telegram dashboard segurança identidade auth
---

> **Nota:** esta página resume o [ADR-021](../decisions/ADR-021.md) (proposto). **Nada aqui liga o MFA por padrão**; ainda é desenho.

## Por que {#por-que}

Hoje o WebSocket e o dashboard confiam em um token estático, e o Telegram só em uma lista de IDs. Quem controlar a conta do Telegram fala com a Juniper com todas as tools. A proposta mantém o Telegram completo e fortalece a **garantia de que é o dono**: uma identidade única, com segundo fator, para **todo** cliente. Junto com a rede privada ([Visão geral da produção](producao.md)), são duas camadas independentes.

## Como fica {#como-fica}

- **Serviço único** `brain/auth/`: decide quem está autenticado; o cliente só leva a credencial; o LLM nunca vê segredos.
- **TOTP** (app autenticador), tolerância de ±1 passo, **código usado não vale de novo**.
- **A credencial** (`client_id` + `client_secret`, [Usuários e credenciais](usuarios.md); antes, o token estático) significa "este dispositivo é meu"; com o MFA ligado ela sozinha não basta.
- **Sessão** com prazo, ligada ao dispositivo e ao canal, **revogável**, guardada só como hash em arquivos sob `~/.juniper/auth/` (sem depender do MongoDB).
- **Recuperação:** 10 códigos de uso único e, em emergência, `juniper auth reset` por SSH na VPS. Não existe "esqueci o código" remoto.

| Cliente | Autenticação |
|---|---|
| WebSocket (CLI, app) | Bearer no handshake + `auth.login` com o código; até lá a conexão fica "locked". |
| Telegram | `/login <código>` (a mensagem é apagada), sessão por usuário e chat, expira por inatividade. A lista de IDs vira filtro prévio. |
| Dashboard | Token **e** código; o cookie leva o token de sessão. |
| Voz | Herda a conexão; a voz nunca é fator ([Voz por áudio](voz-audio.md)). |

## Usuários e credenciais {#usuarios}

O [ADR-027](../decisions/ADR-027.md) **está implementado** e é a base deste desenho: o usuário (`owner` ou `tester`) e uma credencial `client_id` + `client_secret` por dispositivo, com o segredo visto uma vez e só o hash no MongoDB. Tudo está em [Usuários e credenciais](usuarios.md). O MFA continua proposto e ficará **por cima** da credencial: ela diz «este dispositivo é desta pessoa», e o código TOTP, «e é ela agora».

## Regras que não mudam {#regras}

O MFA **não substitui a confirmação** ([Agente](agente.md#confirmacao)). Falha de login é resultado tipado e nunca derruba o servidor. Códigos e tokens de sessão são mascarados no log, com auditoria sem segredos.

## Migração {#migracao}

`auth.mfa.enabled` começa **desligado**; o usuário cadastra o autenticador e confere tudo com o MFA ainda desligado; só então liga. Desligar é editar o `config.yaml` por SSH.

## Para saber mais {#para-saber-mais}

- [Usuários e credenciais](usuarios.md) (Documentação Técnica): O cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash) e a migração do token estático.
- [Privacidade e segurança](produto-privacidade.md) (Produto): Para onde vão os seus dados, o que fica só no seu servidor, como a Juniper se protege e o que você controla.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
