---
title: Usuários e credenciais
group: Documentação Técnica
section: Núcleo
order: 10
description: Como a Juniper sabe quem fala com ela: o cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash), a migração do token estático e o que ainda não existe (isolamento por usuário).
status: pronta
tags: usuários credenciais client_id client_secret login hash pepper owner tester dono beta cadastro rotação revogar dispositivos legacy_token cli dashboard telegram adr-027
---

> **Nota:** esta página detalha o [ADR-027](../decisions/ADR-027.md). Código: `brain/auth/`, `brain/clients/users_cli.py`, `brain/dashboard/admin.py`. O segundo fator (MFA) é outro assunto: [Login com MFA](login.md).

## Visão geral {#visao-geral}

Cada pessoa é um **usuário** (`owner` ou `tester`, ativo ou desativado). Cada usuário tem uma ou mais **credenciais**, uma por dispositivo, e cada credencial é um par:

| Parte | O que é |
|---|---|
| `client_id` | Público, `jc_` e 16 caracteres. Identifica a credencial. |
| `client_secret` | Sorteado (256 bits), `jsk_` e o resto. Prova que quem fala é o dono da credencial. |

Para entrar, o cliente apresenta os **dois**. No WebSocket e na API do dashboard o cabeçalho é `Authorization: Bearer <client_id>.<client_secret>`.

## O segredo aparece uma vez {#segredo kind=conceito file=brain/auth/credentials.py}

- No cadastro (CLI ou dashboard) e a cada rotação, o `client_secret` sai **uma única vez**, na resposta. Depois disso ele não existe em lugar nenhum, nem para o dono.
- No MongoDB fica só `HMAC-SHA256(pepper, client_secret)`. O *pepper* é um arquivo do servidor (`deployment.auth.pepper_file`, modo 600), fora do banco; um vazamento só do banco não permite nem testar palpites. Em produção o arquivo fica no volume de estado, junto do token: **perder o pepper invalida todos os segredos**.
- Um hash rápido basta porque o segredo é sorteado, não escolhido por gente.
- Perdeu? **Rotacione**: sai um segredo novo para o mesmo `client_id`, e o antigo, as conexões e as sessões dele deixam de valer.

## Cadastrar pela CLI {#cli kind=comando file=brain/clients/users_cli.py}

Roda onde o servidor está (na VPS, dentro do contêiner; veja [Operação](operacao.md#atalho)):

```bash
juniper users add Petrus --role owner     # o primeiro: o dono
juniper users add Ana --role tester       # exige deployment.auth.allow_testers: true
juniper users list
juniper users credentials add Petrus --label tablet   # outra credencial, outro aparelho
juniper users credentials list
juniper users credentials rotate jc_...   # segredo novo, o antigo morre
juniper users credentials revoke jc_...   # aparelho perdido
juniper users disable Ana                 # e `enable`
```

No computador (ou celular) que vai **falar** com o servidor, `juniper login` guarda a credencial num arquivo de modo 600 (`deployment.auth.credential_file`) e `juniper logout` a apaga. Sem esse arquivo, o cliente cai para o token estático.

## Cadastrar pelo dashboard {#dashboard kind=conceito file=brain/dashboard/admin.py}

A página **Usuários** (só para o dono) lista usuários e credenciais (com último uso), cadastra, gera outra credencial, rotaciona, revoga e desativa. É a única parte do [dashboard](dashboard.md) que escreve, e tem defesas próprias:

- só `POST`, só do dono, com **token anti-CSRF** da sessão (cabeçalho `X-CSRF-Token`) e `Origin`, quando enviado, igual ao `Host`; um Bearer sozinho, sem cookie, não escreve;
- **desativar, rotacionar e revogar** exigem digitar o nome (ou o `client_id`); o servidor confere, não só a tela;
- os parâmetros vão na *query* do `POST` (o servidor HTTP do dashboard lê cabeçalhos, não corpo) e nenhum é segredo;
- o `client_secret` volta uma vez na resposta e a tela o esconde ao fechar o aviso;
- cada ação vai ao log (`dashboard`, `op`, `actor`, `target`), nunca com o segredo.

Rotas em [API HTTP](api-http.md#usuarios).

## Quem pode entrar {#entrada}

| Cliente | Como |
|---|---|
| Servidor WebSocket (CLI remota, app) | Bearer no handshake. O `welcome` traz `user` (`id`, `name`, `role`), só para mostrar. Credencial revogada ou usuário desativado: a conexão cai em segundos (código `4401`). |
| Dashboard | A tela pede o `client_id` e o `client_secret`. O cookie (12 h) é ligado à credencial: rotacionar ou revogar derruba a sessão. |
| Telegram | A lista de IDs continua como filtro prévio. Com `telegram.require_login: true`, o bot só conversa depois de `/login <client_id> <client_secret>` (ele **apaga a mensagem** na hora) e a sessão vale 12 h de inatividade; `/sair` encerra. O segredo fica só na memória do bot. |
| App Android | A tela de configuração tem *Client ID* e *Client secret*. Deixar o *Client ID* vazio mantém o jeito antigo (token estático). |

Erros de login são sempre o mesmo texto e o mesmo tempo, para não revelar se o `client_id` existe. Cinco erros em cinco minutos bloqueiam o cliente (e o `client_id`) por um tempo. Consequência conhecida: quem alcança a rede privada e sabe um `client_id` pode travar esse login por alguns minutos errando de propósito; ele não entra, mas também não deixa o dono entrar por aquele `client_id` (use outra credencial ou espere).

## O banco fora do ar {#fallback}

Cada cadastro, rotação ou revogação do dono regrava um **espelho** (`deployment.auth.owner_mirror_file`, modo 600) com o hash das credenciais ativas dele. Se o MongoDB estiver fora, só o **dono** entra, por esse espelho; os demais voltam com o banco.

## Migração {#migracao}

1. O token estático (`deployment.auth.token_file`) segue valendo como o do dono enquanto `deployment.auth.legacy_token: true` (o padrão).
2. Cadastre o dono, guarde o `client_secret`, atualize cada cliente (`juniper login`, app, Telegram) e confira.
3. Ligue `telegram.require_login: true` (o bot deixa de depender do arquivo do token).
4. Só então `legacy_token: false` e reinicie: o token deixa de valer em todos os clientes, e o do arquivo vira lixo.

## O que ainda não existe {#limites}

- **Isolamento por usuário.** Um `tester` enxerga o mesmo cérebro que o dono: memória, conversas, agenda, música. Por isso cadastrar `tester` fica atrás de `deployment.auth.allow_testers` (desligado) e a tela avisa. Registros novos já levam `owner_id`; falta filtrar as leituras ([ADR-027](../decisions/ADR-027.md), item 9).
- **MFA:** é o [ADR-021](../decisions/ADR-021.md), ainda não implementado; vai por cima da credencial.

## Testes {#testes}

```bash
pytest tests/test_auth_service.py tests/test_users_cli.py tests/test_server_credentials.py tests/test_dashboard_users.py tests/test_telegram_login.py -v
```

## Para saber mais {#para-saber-mais}

- [Ocorrências e ações do dashboard](ocorrencias.md) (Documentação Técnica): Registrar qualquer bug (pelo dashboard ou por fala, com a tool report_issue), acompanhar cada ocorrência, as ações de memória e as tarefas do dashboard.
- [Login com MFA](login.md) (Documentação Técnica): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, servido pelo próprio cérebro.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor e a API do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões e desligamento.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Clientes em produção](clientes-producao.md) (Produção): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android e o dashboard.
