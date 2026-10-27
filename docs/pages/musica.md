---
title: Música
group: Integrações
order: 5
description: Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
status: proposto
tags: musica youtube music data api playlist cota quota oauth gosto musical open_url autoplay music_search videoId ytmusicapi
---

> **Nota:** esta página resume o [ADR-024](adr-024.html) (proposto). **Implementado até agora:** a busca (`music_search`), o contador de cota e a configuração (T-154) o login no YouTube (T-155) e as playlists (T-156). O gosto musical ainda é desenho.

## Como funciona {#como-funciona}

1. O LLM chama **`music_search`** (`READ`): a YouTube Data API v3 devolve até 5 faixas, com preferência pelas oficiais ("Artista - Topic").
2. O LLM escolhe e chama [`open_url`](executor.html#open-url) com `https://music.youtube.com/watch?v=<videoId>`. O domínio `music.youtube.com` está na lista de confiança, então abre **sem perguntar**.
3. O celular redireciona para o app do YouTube Music e o [app mobile](mobile.html) dá o play automaticamente; se o autoplay falhar, a faixa aparece pronta e basta apertar play.

Tocar **não exige login**: basta a chave de API (`JUNIPER_YOUTUBE_API_KEY`, no `.env`) e `music.enabled: true`. A chave viaja no cabeçalho `x-goog-api-key`, **nunca na URL**, e não aparece em erro nem em log. Sem chave ou com a música desligada, a tool nem aparece para o LLM.

## Tools {#tools}

| Tool | Tier | O que faz |
|---|---|---|
| `music_search` | READ | Busca faixas e devolve links (modo `radio` devolve o link de mix). **Pronta (T-154).** |
| `music_playlists` | READ | Lista as playlists do Petrus (nome, quantidade e id). **Pronta (T-156).** |
| `music_playlist_create` | WRITE | Cria uma playlist **privada**, com confirmação em palavras. **Pronta (T-156).** |
| `music_playlist_add` | WRITE | Adiciona faixas a uma playlist, com confirmação. **Pronta (T-156).** |
| `music_feedback` | WRITE (sem confirmar) | Registra "gostei", "não gostei" ou "pulei". |

Apagar playlist não existe na v1.

## Como a busca ranqueia {#ranqueamento}

O cérebro busca 10 resultados na categoria de música (o custo é o mesmo de buscar menos) e devolve até 5, sem repetir o mesmo vídeo: primeiro as faixas oficiais (canais "Artista - Topic"), depois as demais; versões "ao vivo", *cover*, *karaoke*, *reaction*, *remix*, *slowed*, *nightcore*, álbum completo e playlist descem na lista, **a menos que o pedido cite a palavra**. Títulos vêm com HTML escapado da API e são mostrados como texto. A escolha final é do LLM, que vê a lista. No modo `radio`, o link devolvido é o de mix (`list=RDAMVM<id>`); se o YouTube Music monta a fila e dá autoplay a partir dele ainda é **a confirmar no aparelho**.

## Cota e erros {#cota}

A cota padrão é de 10.000 unidades por dia (busca 100, escrita 50). O cérebro conta o que gasta (`music.quota_file`, por dia do **Pacífico**, sobrevive a reinício) e recusa antes do Google, com o erro tipado `quota_exhausted` e quanto falta para voltar; se o próprio Google disser que a cota acabou, o cérebro para de chamar até o dia virar. O limite padrão é 9.000 (margem sob os 10.000). Os demais códigos: `auth_required`, `auth_expired`, `api_error`, `not_configured`, `network_error`.

## Playlists e login {#login}

Criar playlists exige o login do Petrus no Google, pelo fluxo de **código de dispositivo**. Uma vez:

1. No Google Cloud, crie um **cliente OAuth** do tipo "TVs e dispositivos de entrada limitada" (no projeto da YouTube Data API) e ponha o ID e a chave no `.env` do cérebro: `JUNIPER_YOUTUBE_CLIENT_ID` e `JUNIPER_YOUTUBE_CLIENT_SECRET`. Se o app estiver em modo de teste, adicione o seu Gmail aos usuários de teste.
2. Rode **`juniper music login`**. Ele mostra um link e um código; abra o link em qualquer navegador, entre com a conta do YouTube Music e autorize. O comando espera, respeitando o intervalo do Google, e grava o token.
3. **`juniper music status`** mostra o que está configurado (sem revelar nenhuma chave ou token), a cota de hoje e se há login.

O token de renovação fica em `music.oauth_file` (`~/.juniper/music/oauth.json`, modo 600, fora do git; na VPS, um arquivo montado). O acesso **se renova sozinho**, uma vez só mesmo com várias chamadas ao mesmo tempo. Nada é apagado por conta própria.

| Situação | Código |
|---|---|
| Nunca fez login | `auth_required`: "rode `juniper music login`" |
| Acesso expirado ou revogado | `auth_expired` ("rode `juniper music login` de novo") |
| Faltam o ID ou a chave do cliente, ou o Google recusou o cliente | `not_configured` |
| O código do login expirou, foi negado ou o Google falhou | `auth_expired`, `auth_required` ou `api_error` |

Se o app OAuth estiver em modo de teste, o Google pode expirar o token de renovação a cada 7 dias *(a confirmar a regra vigente)*; publicar o app (em produção, sem verificação, uso pessoal) evita isso.

## Criar e alterar playlists {#playlists}

As três tools de playlist só aparecem com `music.enabled` **e** o cliente OAuth configurado (a chave de API sozinha não loga ninguém); sem o login feito, elas respondem `auth_required` com a instrução, em vez de falhar calado.

- **`music_playlist_create`** (`title`, `video_ids`): cria uma playlist **sempre privada** (mudar isso é decisão do Petrus) e adiciona as faixas **na ordem dada**, uma a uma. Até **25 faixas** por chamada. A pergunta de confirmação mostra o que será feito ("criar a playlist privada «Foco» com 2 músicas"), e como ela nomeia a playlist, um "sim" na conversa já basta ([Agente](agente.html#confirmacao)). Os itens de `video_ids` podem ser ids de 11 caracteres ou links do YouTube e do YouTube Music (o id é o que vem depois de `v=`); repetidos são descartados.
- **`music_playlist_add`** (`playlist_id`, `video_ids`, `playlist_title` opcional, só para a pergunta): adiciona a uma playlist existente; o id vem de `music_playlists`.
- **O que é recusado antes de perguntar:** título vazio, com mais de 150 caracteres ou com `<` ou `>`; ids ou links que não são do YouTube; mais de 25 faixas; playlist inválida. Você nunca é perguntado sobre algo que vai falhar.

**Custo de cota:** criar custa 50 unidades e **cada faixa adicionada também custa 50**, então uma playlist de 10 faixas gasta 550 do limite diário. O cérebro **reserva o custo inteiro antes de criar qualquer coisa**, para nunca deixar uma playlist pela metade por falta de cota.

**Falhas no meio:** uma faixa que não existe mais ou que já está na playlist é **pulada** (o resultado diz quantas não entraram); uma falha séria (cota, login) para e a resposta diz que a playlist foi criada, com quantas faixas entraram. Outros erros: `playlistNotFound` (não achei a playlist), conta sem canal do YouTube e playlist cheia.

## Gosto musical {#gosto}

A API oficial não dá o histórico de reprodução. O gosto vem do que o Petrus **pede e comenta**; resultados de busca nunca alimentam a memória subjetiva ([Memória V2](memoria-v2.html)). A v1 só registra o feedback e mostra com `juniper music taste`.

## Por que não a `ytmusicapi` {#ytmusicapi}

Uma POC com a biblioteca não oficial autenticou, mas recebeu HTTP 403 em tudo com um token OAuth próprio. Ela fica fora do caminho crítico e pode voltar atrás da mesma interface.

## Tasks {#tasks}

T-154 (provider, cota e busca), T-155 (login OAuth), T-156 (playlists), T-157 (gosto musical), T-158 (documentação e aceite). Épica #180.
