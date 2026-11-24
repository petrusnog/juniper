---
title: API HTTP
group: Referência
order: 3
description: As rotas HTTP do cérebro: a saúde do servidor, o download do app Android e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
status: pronta
tags: api http rest health dashboard rotas json get login cookie paginação app update apk limit offset memories beliefs affects persona interests notes dreams journal sessions search prompt
---

O cérebro expõe dois servidores HTTP no mesmo processo (`juniper serve`): o do WebSocket, que também responde `/health`, e o do [dashboard](dashboard.md), com uma API JSON **somente leitura**. Para conversar, tocar ferramentas ou mexer na memória, use a [API WebSocket](protocolo.md).

| Servidor | Porta padrão | Configuração |
|---|---|---|
| WebSocket + saúde | 8765 | `deployment.server.*` |
| Dashboard | 8766 | `dashboard.*` ([Configuração](configuracao.md#trace)) |

## Saúde do cérebro {#health}

`GET /health` na porta do WebSocket, **sem autenticação**. É o que o *healthcheck* do Docker e o deploy usam.

```bash
curl -s http://127.0.0.1:8765/health
```

```json
{ "status": "ok", "memory": "ready", "version": "4.0.0" }
```

| `status` / `memory` | Significado |
|---|---|
| `ok` / `ready` | Tudo pronto. |
| `ok` / `off` | Pronto, sem memória de longo prazo. |
| `degraded` / `warming_up` | O modelo de embeddings ainda está carregando (ou baixando, na primeira vez). A conversa funciona sem memória. Continua HTTP 200, para o contêiner não ser reiniciado. |

Qualquer outro caminho que não `/health`, `/v1/ws`, `/app/update` ou `/app/update/apk` responde 404.

## Atualização do app {#app-update}

Na porta do WebSocket, o cérebro serve o APK do [app Android](mobile.md#atualizacao) ([ADR-026](../decisions/ADR-026.md)). As duas rotas exigem `Authorization: Bearer <token dos clientes>` (sem ele ou com token errado: 401) e só leem a pasta `app_update.dir` ([Configuração](configuracao.md#app-update-config)).

| Rota | Resposta |
|---|---|
| `GET /app/update` | 200 com a versão publicada, ou `{"available": false}` se não há nada válido. |
| `GET /app/update/apk` | 200 com o arquivo (`application/vnd.android.package-archive`); 404 se não há versão publicada. Cada download vira um evento `app_update` no log. |

```bash
curl -s -H "Authorization: Bearer $(cat .juniper-dev/state/token)" http://127.0.0.1:8765/app/update
```

```json
{ "available": true, "version_code": 321, "version_name": "0.1.321", "sha256": "…", "size": 61234567, "released_at": "2026-10-29T12:00:00Z" }
```

Uma publicação só é oferecida se o `latest.json` e o APK existem e o tamanho do arquivo bate com o `size` (um envio pela metade nunca é servido). Como publicar: [Clientes em produção](clientes-producao.md#app-atualizar).

## Dashboard {#dashboard}

Todas as rotas aceitam só `GET` (outro método: 405). O cabeçalho `Host` precisa ser um IP, `localhost`, um nome `*.ts.net` ou um host de `dashboard.allowed_hosts`; senão, 421.

### Autenticação {#autenticacao}

| Rota | Autenticação | O que faz |
|---|---|---|
| `GET /health` | nenhuma | `{"ok": true, "service": "dashboard"}` |
| `GET /login` | nenhuma | A página de login. |
| `GET /api/login` | `Authorization: Bearer <client_id>.<client_secret>` (ou o token estático, se `legacy_token`) | Confere a credencial e devolve o cookie de sessão (`HttpOnly`, `SameSite=Strict`, `Secure` se `X-Forwarded-Proto: https`), válido por `dashboard.session_hours`. Token errado: 401; cinco erros em cinco minutos: 429. |
| `GET /api/logout` | nenhuma | Apaga o cookie. |
| `GET /api/me` | cookie de sessão | Quem está logado (`user`, `device`) e o `csrf` da sessão, que as escritas devolvem no cabeçalho `X-CSRF-Token`. |
| `GET /api/...` | cookie de sessão **ou** `Authorization: Bearer <credencial>` | As rotas de dados abaixo. Sem credencial: 401 `{"error": "faça login"}`. |

Para scripts, o cabeçalho `Authorization` basta:

```bash
curl -s -H "Authorization: Bearer $(cat .juniper-dev/state/token)" http://127.0.0.1:8766/api/overview
```

### Usuários e credenciais {#usuarios}

As **únicas** rotas que escrevem ([ADR-027](../decisions/ADR-027.md)). Só o dono; os `POST` exigem o cookie de sessão, o cabeçalho `X-CSRF-Token` (de `/api/me`) e, se houver `Origin`, que ele seja o do próprio `Host`. Os parâmetros vão na *query* (nenhum é segredo). Detalhes em [Usuários e credenciais](usuarios.md#dashboard).

| Rota | Parâmetros | O que faz |
|---|---|---|
| `GET /api/users` | — | Usuários e credenciais (sem hash nem segredo), e se `allow_testers` está ligado. |
| `POST /api/users` | `name`, `role` (`tester`), `label` | Cadastra e devolve o `client_secret` **uma vez**. |
| `POST /api/users/<id>/credentials` | `label` | Outra credencial (um aparelho novo); devolve o segredo uma vez. |
| `POST /api/users/<id>/disable` · `enable` | `confirm` = o nome (só para `disable`) | Desativa ou reativa. O dono não pode ser desativado. |
| `POST /api/credentials/<client_id>/rotate` | `confirm` = o `client_id` | Segredo novo (devolvido uma vez); o antigo e as sessões dele morrem. |
| `POST /api/credentials/<client_id>/revoke` | `confirm` = o `client_id` | Revoga. A última credencial do dono não pode ser revogada. |

Erros: 400 (`{"error": ...}` com o motivo ou o texto a confirmar), 403 (não é o dono, ou CSRF/origem inválidos).

### Ações do dono {#acoes}

Também só `POST` do dono, com as mesmas defesas ([ADR-028](../decisions/ADR-028.md)). Ações destrutivas pedem `confirm=` igual ao id (ou à palavra indicada), conferido no servidor. Detalhes em [Ocorrências e ações](ocorrencias.md).

| Rota | Parâmetros | O que faz |
|---|---|---|
| `POST /api/issues` | `title`, `description`, `type`, `severity` | Registra uma ocorrência. |
| `POST /api/issues/<id>/state` · `severity` | `value` | Muda o estado (`aberta`, `investigando`, `resolvida`, `descartada`) ou a gravidade. |
| `POST /api/issues/<id>/note` | `text` | Anota no histórico. |
| `POST /api/issues/<id>/delete` | `confirm` = o id | Apaga. |
| `POST /api/memory/facts/<id>/forget` | `confirm` = o id | Esquece a memória e as versões antigas que ela substituiu. |
| `POST /api/memory/beliefs/<id>/forget` · `interests/<id>/forget` · `notes/<id>/forget` | `confirm` = o id | Apaga a crença, o interesse ou a nota. |
| `POST /api/memory/affects/clear` | `confirm=sentimentos` | Apaga todos os sentimentos. |
| `POST /api/memory/concerns/<id>/weight` | `value` (0 a 1), `reason` | Muda o peso (fica no histórico). |
| `POST /api/memory/persona/revert` | `to` (versão, opcional) | Volta à versão anterior como uma versão nova. |
| `POST /api/jobs/dream` · `reindex` | `dry=1` (sonho), `force=1` (reindexação) | Inicia a tarefa em segundo plano; `409` se outra estiver rodando. |

Leituras novas, sem escrita:

| Rota | Parâmetros | O que entrega |
|---|---|---|
| `GET /api/logs` | `days` (1 a 7), `errors=1`, `event` (lista), `session`, `q`, `limit` (até 500) | O log técnico, do mais novo ao mais antigo, com nível e resumo de uma linha. |
| `GET /api/costs` | — | Voz (uso e limite do mês), música (cota do dia), tokens do sonho e trocas de provedor. |
| `GET /api/health` | — | Estado do servidor, versão, tempo no ar, camadas ligadas, acesso e tools. |
| `GET /api/jobs` | — | A tarefa em curso e as últimas 10 execuções. |

Em `GET /api/occurrences`, `kind=registro` lista os registros (com `state` e `type` como filtros).

### Convenções {#convencoes}

- Respostas em JSON UTF-8, `Cache-Control: no-store`.
- **Paginação** nas listas: `?limit=` (padrão 50, máximo 200) e `?offset=`; a resposta traz `{"total", "offset", "limit", "items"}`.
- **Segredos mascarados e vetores removidos** de tudo o que sai.
- **Erros:** `{"error": "<motivo>"}` com 404 (rota ou id inexistente) ou 503 (a camada está desligada: memória de longo prazo ou Memória V2, com o motivo).
- Nenhuma rota escreve no banco.

### Rotas {#rotas}

| Rota | Filtros e parâmetros | O que entrega |
|---|---|---|
| `/api/session` | — | Quais camadas estão ligadas: `{"ok", "v1", "v2"}`. |
| `/api/overview` | — | Contadores de cada camada, humor e sentimentos de agora, traços e o último sonho. |
| `/api/memories` | `type`, `status` (`active` padrão, `superseded`, `all`), `reflexao` (`bruta`, `refinada`), `q`, paginação | Os fatos da memória de longo prazo. |
| `/api/memories/<id>` | — | Um fato, com a lembrança e a linhagem bruta → refinada. |
| `/api/beliefs` | `status`, `topic`, `q`, `order=confidence`, paginação | Crenças com confiança, faixa, fontes. |
| `/api/beliefs/<id>` | — | Uma crença com cada evidência. |
| `/api/affects` | `origin` (`conversa`, `sonho`), `kind`, paginação | Sentimentos. |
| `/api/affects/<id>` | — | Um sentimento e o rastreio até a fala que o causou. |
| `/api/mood` | `days` (1 a 90, padrão 14) | A série do humor. |
| `/api/persona` | — | A personalidade atual e o histórico de versões. |
| `/api/concerns` | — | As preocupações e os pesos. |
| `/api/core` | — | O núcleo da persona e o perfil. |
| `/api/interests` | paginação | Temas de estudo com passo, posição e intensidade. |
| `/api/interests/<id>` | — | Um tema, as notas por passo e as perguntas em aberto. |
| `/api/notes` | `interest`, `uncertainty`, `source`, `kind`, `q`, paginação | As notas de estudo. |
| `/api/dreams` | paginação | As execuções do sonho: fases, tokens, erros. |
| `/api/journal` | `days` (1 a 3650, padrão 30), `kind` | O diário. |
| `/api/sessions` | paginação | As conversas (503 com `dashboard.transcripts: false`). |
| `/api/sessions/<id>` | — | Os turnos de uma conversa; a resposta depois de ferramentas fica de fora. |
| `/api/search` | `q`, `type` (repetível), `limit` (1 a 20, padrão 8) | Pesquisa em memórias, crenças, temas, notas, sentimentos e conversas, por palavras e por significado. |
| `/api/prompt` | `message`, `previous?` | O simulador: os blocos que a Memória V2 acrescentaria ao prompt para aquela mensagem, com a estimativa de tokens. Não grava nada. |

As rotas `beliefs`, `affects`, `mood`, `persona`, `concerns`, `interests`, `notes`, `dreams` e `journal` dependem da [Memória V2](memoria-v2.md); `memories` depende da [memória de longo prazo](memoria-longo-prazo.md).

## Para saber mais {#para-saber-mais}

- [Ocorrências e ações do dashboard](ocorrencias.md) (Documentação Técnica): Registrar qualquer bug (pelo dashboard ou por fala, com a tool report_issue), acompanhar cada ocorrência, as ações de memória e as tarefas do dashboard.
- [Usuários e credenciais](usuarios.md) (Documentação Técnica): O cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash) e a migração do token estático.
- [Login com MFA](login.md) (Documentação Técnica): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
