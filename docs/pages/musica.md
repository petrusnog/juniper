---
title: Música
group: Documentação Técnica
section: Integrações
order: 31
description: Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
status: pronta
tags: musica youtube music data api playlist cota quota oauth gosto musical open_url autoplay music_search videoId ytmusicapi
---

> **Nota:** esta página detalha o [ADR-024](../decisions/ADR-024.md). A música funciona de ponta a ponta: busca e cota, tocar no celular pelo [app Android](mobile.md) com autoplay, login, playlists e gosto musical, cobertos por teste de aceite. O código fica em `brain/music/` e `brain/tools/music.py`.

## Como funciona {#como-funciona}

1. O LLM chama **`play_music`** ([abaixo](#play-music)): a YouTube Data API v3 devolve até 5 faixas, com preferência pelas oficiais ("Artista - Topic"), e a primeira é a escolhida. Para só **mostrar opções**, ele usa `music_search` (`READ`).
2. O cérebro abre `https://music.youtube.com/watch?v=<videoId>` no celular pelo [`open_url`](executor.md#open-url). O domínio `music.youtube.com` está na lista de confiança, então abre **sem perguntar**.
   **Páginas de busca não valem:** se o LLM tentar abrir `music.youtube.com/search?...` (ou os resultados do YouTube) com `open_url`, a chamada é recusada antes de chegar ao celular, com a instrução de usar `music_search`. Um link de busca só abre uma lista e não toca nada; foi o que aconteceu no primeiro teste real. Só vale enquanto a música está ligada.
3. O celular redireciona para o app do YouTube Music e o [app mobile](mobile.md) dá o play automaticamente; se o autoplay falhar, a faixa aparece pronta e basta apertar play.

Tocar **não exige login**: basta a chave de API (`JUNIPER_YOUTUBE_API_KEY`, no `.env`) e `music.enabled: true`. A chave viaja no cabeçalho `x-goog-api-key`, **nunca na URL**, e não aparece em erro nem em log. Sem chave ou com a música desligada, a tool nem aparece para o LLM.

## Tocar: `play_music` e o canal do pedido {#play-music}

Para **tocar**, o modelo chama uma ferramenta só, `play_music`: ela busca, escolhe o melhor resultado (a mesma ordenação da busca) e abre. Antes eram dois passos soltos (`music_search` e depois `open_url`) e o modelo às vezes parava no meio e mandava o link no texto. Agora o passo de abrir não depende dele. `music_search` ficou para **mostrar opções** e para montar playlists.

`play_music` tem três modos (`mode`): `track` (uma faixa, o padrão), `album` (o álbum completo: busca playlists de álbum do YouTube Music, ids `OLAK5uy_...`, e abre a fila inteira) e `radio`. Uma playlist do Petrus (ou um álbum já achado) toca com `playlist_id`. O link que toca é `https://music.youtube.com/watch?list=<id>`, não `/playlist?list=` (esse só mostra a página). Álbum custa 100 unidades de cota por busca, como a de faixas; se nada é achado, o resultado diz e nenhum link é aberto. Se o aparelho for de voz e a música abrir, a sessão de voz termina depois do comentário ([voz](voz-audio.md#maos-livres)).

O que acontece depende de **por onde o pedido chegou**: o cérebro diz isso ao modelo em cada turno («Canal: aplicativo do celular (voz)», «Canal: telegram»...) e as próprias ferramentas se comportam assim:

| Canal | O que `play_music` e `open_url` fazem |
|---|---|
| **App do celular** (texto ou voz) | abrem no celular e dão o play, **sem segunda pergunta**: o pedido da pessoa é a confirmação |
| **Terminal, Telegram** e outros | **não abrem nada**, nem que haja um celular conectado: devolvem o link para o modelo mandar na resposta |

A pergunta de confirmação continua valendo para endereços fora da lista de confiança (`tools.open_url.trusted_domains`): a música é a exceção porque o link é montado pelo cérebro a partir do resultado da busca, nunca digitado pelo modelo.

## Tools {#tools}

| Tool | Tier | O que faz |
|---|---|---|
| `music_search` | READ | Busca faixas e devolve links (modo `radio` devolve o link de mix). |
| `music_playlists` | READ | Lista as playlists do usuário (nome, quantidade e id). |
| `music_playlist_create` | WRITE | Cria uma playlist **privada**, com confirmação em palavras. |
| `music_playlist_add` | WRITE | Adiciona faixas a uma playlist, com confirmação. |
| `music_feedback` | WRITE (sem confirmar) | Registra "gostei", "não gostei", "pulei" ou "pediu para tocar". |

Apagar playlist não existe na v1.

## Como a busca ranqueia {#ranqueamento}

O cérebro busca 10 resultados na categoria de música (o custo é o mesmo de buscar menos) e devolve até 5, sem repetir o mesmo vídeo: primeiro as faixas oficiais (canais "Artista - Topic"), depois as demais; versões "ao vivo", *cover*, *karaoke*, *reaction*, *remix*, *slowed*, *nightcore*, álbum completo e playlist descem na lista, **a menos que o pedido cite a palavra** (a comparação é por palavra inteira: "Alive" não é uma versão "live" nem "Discover" um "cover"). Títulos vêm com HTML escapado da API e são mostrados como texto. A escolha final é do LLM, que vê a lista. No modo `radio`, o link devolvido é o de mix (`list=RDAMVM<id>`); se o YouTube Music monta a fila e dá autoplay a partir dele ainda é **a confirmar no aparelho**.

## Cota e erros {#cota}

A cota padrão é de 10.000 unidades por dia (busca 100, escrita 50). O cérebro conta o que gasta (`music.quota_file`, por dia do **Pacífico**, sobrevive a reinício) e recusa antes do Google, com o erro tipado `quota_exhausted` e quanto falta para voltar; se o próprio Google disser que a cota acabou, o cérebro para de chamar até o dia virar. O limite padrão é 9.000 (margem sob os 10.000). Os demais códigos: `auth_required`, `auth_expired`, `not_configured`, `invalid_request` (entrada recusada), `not_supported`, `storage_error` (não deu para gravar o token ou o contador), `api_error` e `network_error`. A tool `music_feedback` ainda devolve `memory_unavailable` quando o MongoDB está fora. **Limite de taxa** do YouTube (`rateLimitExceeded`, uma rajada) **não** trava o dia: vira `api_error` ("tente de novo em instantes"); só `quotaExceeded` e `dailyLimitExceeded` marcam a cota do dia como esgotada.

## Playlists e login {#login}

Criar playlists exige o login do usuário no Google, pelo fluxo de **código de dispositivo**. Uma vez:

1. No Google Cloud, crie um **cliente OAuth** do tipo "TVs e dispositivos de entrada limitada" (no projeto da YouTube Data API) e ponha o ID e a chave no `.env` do cérebro: `JUNIPER_YOUTUBE_CLIENT_ID` e `JUNIPER_YOUTUBE_CLIENT_SECRET`. Se o app estiver em modo de teste, adicione o seu Gmail aos usuários de teste.
2. Rode **`juniper music login`**. Ele mostra um link e um código; abra o link em qualquer navegador, entre com a conta do YouTube Music e autorize. O comando espera, respeitando o intervalo do Google, e grava o token.
3. **`juniper music status`** mostra o que está configurado (sem revelar nenhuma chave ou token), a cota de hoje e se há login.

O token de renovação fica em `music.oauth_file` (`~/.juniper/music/oauth.json`, modo 600, fora do git). **Na VPS, o diretório `~/.juniper/music` é um volume do Docker (`juniper-music`), não um arquivo montado:** o token é gravado por troca de arquivo (escreve um temporário, força o disco e renomeia), o que um *bind mount* de arquivo único não permite; e sem o volume o token e o contador de cota sumiriam a cada deploy. Falha de gravação vira `storage_error` com instrução, nunca uma exceção. O acesso **se renova sozinho**, uma vez só mesmo com várias chamadas ao mesmo tempo. Nada é apagado por conta própria.

| Situação | Código |
|---|---|
| Nunca fez login | `auth_required`: "rode `juniper music login`" |
| Acesso expirado ou revogado | `auth_expired` ("rode `juniper music login` de novo") |
| Faltam o ID ou a chave do cliente, ou o Google recusou o cliente | `not_configured` |
| O código do login expirou, foi negado ou o Google falhou | `auth_expired`, `auth_required` ou `api_error` |

Se o app OAuth estiver em modo de teste, o Google pode expirar o token de renovação a cada 7 dias *(a confirmar a regra vigente)*; publicar o app (em produção, sem verificação, uso pessoal) evita isso.

## Criar e alterar playlists {#playlists}

As três tools de playlist só aparecem com `music.enabled` **e** o cliente OAuth configurado (a chave de API sozinha não loga ninguém); sem o login feito, elas respondem `auth_required` com a instrução, em vez de falhar calado.

- **`music_playlist_create`** (`title`, `video_ids`): cria uma playlist **sempre privada** (mudar isso exige uma decisão registrada em ADR) e adiciona as faixas **na ordem dada**, uma a uma. Até **25 faixas** por chamada. A pergunta de confirmação mostra o que será feito ("criar a playlist privada «Foco» com 2 músicas"), e como ela nomeia a playlist, um "sim" na conversa já basta ([Agente](agente.md#confirmacao)). Os itens de `video_ids` podem ser ids de 11 caracteres ou links do YouTube e do YouTube Music (o id é o que vem depois de `v=`); repetidos são descartados.
- **`music_playlist_add`** (`playlist_id`, `video_ids`): adiciona a uma playlist existente; o id vem de `music_playlists`. **Antes de perguntar**, a Juniper confere o id entre as playlists do usuário (1 unidade de cota) e a pergunta usa o **nome verdadeiro** dessa playlist, não um nome escolhido pelo LLM; um id que não é dele é recusado. Um título curto demais (menos de 3 caracteres) não vale como "sim" em contexto: ela pergunta de novo.
- **O que é recusado antes de perguntar:** título vazio, com mais de 150 caracteres ou com `<` ou `>`; ids ou links que não são do YouTube; mais de 25 faixas; playlist inválida. Você nunca é perguntado sobre algo que vai falhar.

**Custo de cota:** criar custa 50 unidades e **cada faixa adicionada também custa 50**, então uma playlist de 10 faixas gasta 550 do limite diário. O cérebro **reserva o custo inteiro antes de criar qualquer coisa**, para nunca deixar uma playlist pela metade por falta de cota.

**Falhas no meio:** uma faixa que não existe mais ou que já está na playlist é **pulada** (o resultado diz quantas não entraram). Uma falha séria (cota, login, rede) **para** e devolve o que existe: a mensagem e o `metadata` trazem o **id e o link da playlist e quantas faixas entraram**, e mandam usar `music_playlist_add` para completar, em vez de criar uma duplicada. Outros erros: `playlistNotFound` (não achei a playlist), conta sem canal do YouTube e playlist cheia. **Sem login, nada é gasto da cota**: o login é conferido antes de contar.

## Gosto musical {#gosto}

A API oficial não dá o histórico de reprodução, então o gosto vem **do que o usuário diz**: "toca Djavan" (`played`) e as reações dele ("gostei dessa" `like`, "não curto" `dislike`, "pula essa" `skip`). A tool **`music_feedback`** anota isso, em silêncio (como `remember`), com título e/ou artista, o id do vídeo quando houver e a conversa de onde veio.

**A regra do [ADR-017](../decisions/ADR-017.md) e o que ela garante de verdade:** o código garante que a **busca nunca escreve** e que a única porta de escrita é a `music_feedback`. Mas **quem decide chamá-la é o LLM**, e ela é silenciosa; então a regra "só o que o usuário escreveu" depende da instrução da tool, e o registro guarda **a citação das palavras dele** (`quote`, até 160 caracteres) para auditoria: um registro sem citação, ou cuja citação não é dele, aparece em `juniper music taste`. Texto com cara de segredo é recusado; título e artista passam por limpeza (caracteres de controle e de direção são removidos) e têm até 200 caracteres. Para um laço do LLM não encher o banco: a mesma reação repetida em até 1 minuto vale uma só, no máximo **30 registros por conversa** e **5.000 no total** (acima disso a tool diz para rodar `juniper music taste clear`).

Os registros ficam na coleção `music_taste` do MongoDB (precisa de `memory.session.store: mongo`; sem isso a tool nem aparece). Para ver e apagar:

| Comando | O que faz |
|---|---|
| `juniper music taste [-n 15]` | Resumo (por sinal e **artistas em alta**, com pontos: gostei +2, pedi +1, pulei −1, não gostei −2) e os últimos registros. |
| `juniper music taste clear [--yes]` | Apaga **tudo**, depois de perguntar. Não dá para desfazer. |

Nesta versão o gosto **só é registrado**: ainda não influencia a conversa nem as sugestões. Que o sonho noturno leia esses eventos e forme crenças sobre o gosto (e que a Juniper sugira algo fora da "bolha") é um passo seguinte que muda o ADR-017, e exige revisar o ADR-017. A coleção `music_taste` não entra na exclusão em cascata da Memória V2: quem apaga é o `juniper music taste clear`.

## Aceite {#aceite}

**O que os testes provam** (`tests/test_acceptance_music.py`, com o servidor, o dispatcher, as tools e o protocolo do executor reais, e só o LLM, o YouTube e o celular falsos):

- "Toca Oceano do Djavan": `music_search` devolve a lista com links, o LLM chama `open_url` e o celular recebe `invoke` com o link de `music.youtube.com`, **sem pergunta no meio**. "Algo parecido" usa o link de mix.
- Sem celular conectado, a Juniper avisa que o dispositivo está desconectado.
- Cota esgotada aparece como erro tipado, sem inventar resultado.
- **Defesas:** um título de resultado que manda abrir outro site é perguntado em palavras, com o endereço exato, e nada chega ao celular antes do "sim"; o truque `evil.example\.music.youtube.com` nunca chega ao celular.
- Playlist: a Juniper pergunta **uma vez** em palavras ("Posso criar a playlist «Foco» com 2 músicas?"), nada é criado antes do "sim", um "não" não cria nada, um pedido inválido é recusado antes de perguntar, e o link da playlist abre no celular.
- Gosto: a reação do usuário ("gostei dessa") é anotada em silêncio; uma busca sozinha não grava nada.

**O que só se verifica no aparelho** (roteiro manual depois do app e da chave):

1. `juniper music status`: chave `configurada`, música `ligada`, cota `0 de 9000`.
2. No app, "toca Oceano do Djavan": o YouTube Music abre na faixa **e dá play sozinho** (se o autoplay falhar, a faixa aparece pronta e basta apertar play).
3. "Toca algo parecido com Djavan": confira se o link de mix monta a fila no YouTube Music.
4. `juniper music login`, depois "cria uma playlist Foco com essas músicas": ela aparece na biblioteca do YouTube Music como **privada**.
5. "Gostei dessa", depois `juniper music taste`: o registro aparece; `juniper music taste clear` apaga.

## Por que não a `ytmusicapi` {#ytmusicapi}

Uma POC com a biblioteca não oficial autenticou, mas recebeu HTTP 403 em tudo com um token OAuth próprio. Ela fica fora do caminho crítico e pode voltar atrás da mesma interface.

## Para saber mais {#para-saber-mais}

- [Integrações](produto-integracoes.md) (Produto): O que a Juniper faz com a agenda do Google, a música do YouTube e os repositórios git, e o que cada integração precisa.
- [Tools](tools.md) (Documentação Técnica): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
