---
title: Música
group: Integrações
order: 5
description: Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
status: proposto
tags: musica youtube music data api playlist cota quota oauth gosto musical open_url autoplay music_search videoId ytmusicapi
---

> **Nota:** esta página resume o [ADR-024](adr-024.html) (proposto). Ainda não está implementada.

## Como funciona {#como-funciona}

1. O LLM chama **`music_search`** (`READ`): a YouTube Data API v3 devolve até 5 faixas, com preferência pelas oficiais ("Artista - Topic").
2. O LLM escolhe e chama [`open_url`](executor.html#open-url) com `https://music.youtube.com/watch?v=<videoId>`. O domínio `music.youtube.com` está na lista de confiança, então abre **sem perguntar**.
3. O celular redireciona para o app do YouTube Music e o [app mobile](mobile.html) dá o play automaticamente; se o autoplay falhar, a faixa aparece pronta e basta apertar play.

Tocar **não exige login**: basta a chave de API (`YOUTUBE_API_KEY`).

## Tools {#tools}

| Tool | Tier | O que faz |
|---|---|---|
| `music_search` | READ | Busca faixas e devolve links (modo `radio` devolve o link de mix). |
| `music_playlists` | READ | Lista as playlists do Petrus. |
| `music_playlist_create` | WRITE | Cria uma playlist **privada**, com confirmação em palavras. |
| `music_playlist_add` | WRITE | Adiciona faixas a uma playlist, com confirmação. |
| `music_feedback` | WRITE (sem confirmar) | Registra "gostei", "não gostei" ou "pulei". |

Apagar playlist não existe na v1.

## Cota e erros {#cota}

A cota padrão é de 10.000 unidades por dia (busca 100, escrita 50). O cérebro conta o que gasta e recusa antes do Google, com o erro tipado `quota_exhausted`. Os demais códigos: `auth_required`, `auth_expired`, `api_error`, `not_configured`, `network_error`.

## Playlists e login {#login}

Criar playlists exige o login do Petrus no Google, pelo fluxo de **código de dispositivo** (`juniper music login`); o token de renovação fica em `~/.juniper/music/oauth.json` (modo 600). Se o app OAuth estiver em modo de teste, o Google pode expirar o token a cada 7 dias *(a confirmar)*.

## Gosto musical {#gosto}

A API oficial não dá o histórico de reprodução. O gosto vem do que o Petrus **pede e comenta**; resultados de busca nunca alimentam a memória subjetiva ([Memória V2](memoria-v2.html)). A v1 só registra o feedback e mostra com `juniper music taste`.

## Por que não a `ytmusicapi` {#ytmusicapi}

Uma POC com a biblioteca não oficial autenticou, mas recebeu HTTP 403 em tudo com um token OAuth próprio. Ela fica fora do caminho crítico e pode voltar atrás da mesma interface.

## Tasks {#tasks}

T-154 (provider, cota e busca), T-155 (login OAuth), T-156 (playlists), T-157 (gosto musical), T-158 (documentação e aceite). Épica #180.
