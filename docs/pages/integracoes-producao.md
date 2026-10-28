---
title: Integrações Google Cloud e Groq
group: Produção
order: 3
description: Como ligar no servidor a agenda do Google, a música do YouTube e a voz (Google Text-to-Speech e Groq), com as credenciais, a configuração e a verificação de cada uma.
status: guia
tags: produção integrações google cloud gcp conta de serviço calendar agenda youtube data api oauth música text-to-speech voz groq whisper stt credenciais certs env cota orçamento
---

Todas as integrações rodam **no cérebro**, então valem para qualquer cliente. Cada uma é opcional e independente. As credenciais seguem a regra de sempre: chaves no `.env`, contas de serviço em `certs/` (montada só para leitura), nunca no Git nem na imagem ([ADR-003](adr-003.html), [ADR-013](adr-013.html)).

## Visão geral das credenciais {#credenciais}

| Credencial | Para quê | Onde nasce | Onde fica no servidor |
|---|---|---|---|
| Conta de serviço da agenda (JSON) | Ler e escrever eventos | Google Cloud → Contas de serviço | `certs/gcp-key.json` |
| Chave de API do YouTube | Buscar músicas | Google Cloud → Credenciais | `.env`: `JUNIPER_YOUTUBE_API_KEY` |
| Cliente OAuth (ID e segredo) | Login para criar playlists | Google Cloud → Credenciais | `.env`: `JUNIPER_YOUTUBE_CLIENT_ID` e `JUNIPER_YOUTUBE_CLIENT_SECRET` |
| Token do login do YouTube | Acessar as playlists | `juniper music login` | volume `juniper-music` |
| Conta de serviço da voz (JSON) | Sintetizar a fala | Google Cloud → Contas de serviço | `certs/gcp-voice.json` |
| Chave da Groq | Transcrever a fala (e, se quiser, como LLM) | `console.groq.com/keys` | `.env`: `JUNIPER_GROQ_API_KEY` |

Os comandos administrativos abaixo rodam **dentro do contêiner** do cérebro. Com o [atalho](operacao.html#atalho), `juniper <comando>` equivale a `docker compose -f docker-compose.prod.yml exec brain python -m brain <comando>`.

Depois de mexer no `.env`, em `certs/` ou no `config.yaml`, recrie o cérebro:

```bash
docker compose -f docker-compose.prod.yml up -d
```

## Projeto no Google Cloud {#gcp}

Num projeto do Google Cloud (crie um em `console.cloud.google.com`), ative em *APIs e serviços → Biblioteca* as APIs que for usar: **Google Calendar API**, **YouTube Data API v3** e **Cloud Text-to-Speech API**. A voz exige **faturamento** ativo no projeto (é cobrada por caractere acima da faixa gratuita); a agenda e a busca de músicas não.

## Agenda do Google {#calendar}

A Juniper usa uma **conta de serviço**: sem tela de consentimento nem token que expira; o acesso é dado compartilhando as agendas com o e-mail da conta ([ADR-008](adr-008.html), [ADR-010](adr-010.html)).

1. **Conta de serviço:** *IAM e administrador → Contas de serviço → Criar*. Não precisa de papel no projeto. Em *Chaves → Adicionar chave → JSON*, baixe o arquivo (é a única vez que ele aparece).
2. **Agenda da Juniper:** no Google Calendar, crie uma agenda (por exemplo, "Juniper") e, em *Compartilhar com pessoas específicas*, adicione o e-mail da conta de serviço com **"Fazer alterações em eventos"**. O ID da agenda está em *Integrar agenda*.
3. **As suas agendas** (opcional): compartilhe com a conta de serviço com "Fazer alterações em eventos" (para ela gerenciar) ou "Ver todos os detalhes" (só para checar conflitos).
4. **Configuração** no seu `config.yaml` do servidor (a camada base; veja [Configuração em camadas](producao.html#configuracao)):

   ```yaml
   google:
     calendar_id: <id-da-agenda-juniper>@group.calendar.google.com
     manage_calendar_ids: [voce@gmail.com]     # agendas que ela também altera
     read_calendar_ids: []                     # agendas só lidas
   agent:
     timezone: America/Sao_Paulo               # o seu fuso
   ```

5. **A chave** no servidor, do seu computador:

   ```bash
   scp meu-projeto-a1b2c3.json deploy@juniper-brain:~/juniper/certs/gcp-key.json
   ssh deploy@juniper-brain "chmod 600 ~/juniper/certs/gcp-key.json"
   ```

6. **Conferir:** recrie o cérebro e peça a um cliente "o que tenho amanhã na agenda?". Para validar a chave e o acesso às agendas antes, rode `python scripts/check_google.py` no seu computador, com a mesma chave em `~/.juniper/certs/gcp-key.json`.

| Sintoma | Causa |
|---|---|
| `Permission denied` ao ler a chave | O dono não é o uid 10001: `sudo chown 10001:10001 certs/gcp-key.json`. |
| "não tem agenda configurada" | O `config.yaml` não chegou: confira `JUNIPER_BASE_CONFIG_FILE` no `.env` e se é um **arquivo** (não uma pasta criada pelo Docker). |
| Erro de acesso à agenda | A agenda não foi compartilhada com o e-mail da conta de serviço. |
| Horário errado nos eventos | `agent.timezone` errado. |

Detalhes do comportamento: [Agenda Google](google-calendar.html).

## Música (YouTube) {#musica}

A busca usa a YouTube Data API oficial; tocar no celular depende do [app Android](clientes-producao.html#app) conectado ([ADR-024](adr-024.html)).

1. **Chave de API:** *Credenciais → Criar credenciais → Chave de API*; restrinja-a à *YouTube Data API v3*.
2. **Cliente OAuth** (só para playlists): *Credenciais → ID do cliente OAuth*, tipo **"TVs e dispositivos de entrada limitada"**. Na tela de consentimento, **publique o app** (status "Em produção"; é gratuito e não o torna público): em modo de teste, o token de renovação expira em 7 dias. No login aparece "app não verificado": *Avançado → Acessar*.
3. **`.env`:**

   ```bash
   JUNIPER_YOUTUBE_API_KEY=...
   JUNIPER_YOUTUBE_CLIENT_ID=...
   JUNIPER_YOUTUBE_CLIENT_SECRET=...
   JUNIPER_MUSIC__ENABLED=true
   ```

4. Recrie o cérebro e confira: `juniper music status` mostra a chave, o cliente OAuth, a cota de hoje e o login, sem revelar segredos.
5. **Login** (só para playlists): `juniper music login` mostra um código e um endereço do Google; confirme no navegador. Enquanto você não confirma, o log mostra respostas `428` do `oauth2.googleapis.com/token`: é o esperado. O token se renova sozinho e fica no volume `juniper-music`.
6. **Testar sem celular:** `juniper "procura Oceano do Djavan no YouTube Music"` (peça "procura", não "toca": sem o app conectado, tocar devolve só o link).

**Cota:** 10.000 unidades por dia (busca 100, escrita 50), zerada à meia-noite do Pacífico. A Juniper para em 9.000 (`music.daily_quota_limit`) e avisa com o erro `quota_exhausted`. Detalhes: [Música](musica.html).

## Voz {#voz}

A conversa por voz do app passa pelo cérebro: o Google sintetiza a fala (voz Chirp 3 HD) e a Groq transcreve o que o usuário diz (Whisper) ([ADR-023](adr-023.html)).

1. **Conta de serviço da voz:** como na agenda, crie uma conta de serviço (pode ser outra, no projeto com faturamento e a Text-to-Speech ativa) e baixe a chave JSON.
2. **No servidor:**

   ```bash
   scp gcp-voice.json deploy@juniper-brain:~/juniper/certs/gcp-voice.json
   ssh deploy@juniper-brain "chmod 600 ~/juniper/certs/gcp-voice.json"
   ```

3. **`.env`:**

   ```bash
   JUNIPER_GROQ_API_KEY=...
   JUNIPER_VOICE__ENABLED=true
   ```

4. Recrie o cérebro e confira: `juniper voice status` deve mostrar «Credencial do Google: encontrada», «Chave da Groq: configurada» e o gasto do mês.

**Orçamento:** o cérebro conta os caracteres enviados ao Google por mês (padrão: até 1.250.000, `voice.tts.monthly_chars_limit`) e guarda o gasto no volume `juniper-music`, para sobreviver a deploys. Ao atingir o teto, ou se o Google ou a Groq falharem, a voz do próprio celular assume e a Juniper avisa uma vez. O áudio vai à Groq e o texto das respostas ao Google: confira a política de retenção de cada um. Detalhes: [Voz por áudio](voz-audio.html).

## Modelo de linguagem {#llm}

O `config.prod.yaml` usa `llm.priority: [deepinfra, groq]` (o Ollama roda na máquina de quem o usa, não no servidor). Basta uma das chaves (`JUNIPER_DEEPINFRA_API_KEY`, `JUNIPER_GROQ_API_KEY`); com as duas, a segunda é o failover. Para a reflexão noturna da Memória V2, `llm.reflection.provider` e `model` escolhem um modelo mais capaz ([LLM](llm.html), [ADR-009](adr-009.html)).
