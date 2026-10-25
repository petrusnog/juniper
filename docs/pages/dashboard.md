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

- **API de leitura (T-131):** todas as camadas por JSON, só `GET`, paginada (`?limit=` até 200 e `?offset=`). Tudo que sai passa por um único ponto que **mascara segredos e remove vetores**; nossos ids são preservados.

| Rota | O que entrega |
|---|---|
| `/api/overview` | Contadores, humor e sentimentos de agora, traços, último sonho |
| `/api/memories` (+ `/api/memories/<id>`) | Fatos, com filtros `type`, `status` (`active`, `superseded`, `all`), `reflexao` e `q`; o detalhe traz a lembrança e a linhagem bruta → refinada |
| `/api/beliefs` (+ `/<id>`) | Crenças com confiança, faixa de certeza, fontes e evidências |
| `/api/affects` (+ `/<id>`), `/api/mood` | Sentimentos (filtros `origin`, `kind`), o rastreio até a fala que os causou e a série do humor |
| `/api/persona`, `/api/concerns`, `/api/core` | Personalidade e histórico, preocupações, núcleo e perfil |
| `/api/interests` (+ `/<id>`), `/api/notes` | Temas com passo e posição; **cada nota** com incerteza, fonte e passo (filtros `interest`, `uncertainty`, `source`, `kind`, `q`) |
| `/api/prompt?message=` | O simulador: os blocos que a Memória V2 acrescentaria ao prompt para aquela mensagem, com tokens; usa um contexto próprio e não grava nada |
| `/api/search?q=` | Pesquisa em tudo: memórias, crenças, temas, notas, sentimentos e conversas; filtros `type` (repetível) e `limit` |
| `/api/dreams`, `/api/journal` | Execuções do sonho (fases, tokens, erros) e o diário |
| `/api/sessions` (+ `/<id>`) | Conversas (somem com `dashboard.transcripts: false`); a resposta dela depois de tools fica de fora |

Camada desligada responde 503 com o motivo, sem erro. Nenhuma rota escreve no banco (um teste confere).

- **Casca e primeiras páginas (T-132):** responsivo de 360 px a telas largas (menu lateral no computador, barra inferior no celular), tema claro e escuro automático, teclado e foco visíveis. Sem biblioteca externa: os gráficos são SVG simples.
  - **Visão geral:** como ela está agora (humor, sentimentos e ecos), a personalidade (radar de traços contra o jeito de partida), contadores de cada camada e o último sonho com as fases.
  - **Afetos:** a série do humor (7, 14 ou 30 dias) e a lista de sentimentos (filtro conversa ou sonho). Ao clicar num sentimento aparece **de onde veio**: a fala do Petrus, a resposta dela e a preocupação atingida.
- **Segurança do frontend:** tudo que vem do servidor entra na tela com `textContent`, nunca como HTML (um teste proíbe `innerHTML` e afins), e a CSP bloqueia script e estilo inline.

- **Memórias (T-133):** busca por texto e filtros (ativas, substituídas ou todas; brutas ou refinadas; tipo). O detalhe de uma refinada mostra o fato, a **lembrança** (contexto, o que ela sentiu, como ela era, relevância), a **linhagem** (bruta ↔ refinada, com links) e as ligações.
- **Memória central (T-133):** o núcleo (recolhido, para ler quando quiser), o perfil, a personalidade (radar, as frases que a descrevem e **a linha do tempo de cada traço** por versão), as crenças (confiança, faixa, fontes e **cada evidência**), o que importa para ela e o **simulador do prompt**: você escreve uma mensagem e vê, bloco a bloco, o que a V2 acrescentaria.

- **Estudos (T-134):** os temas que ela estuda, com passo, posição e intensidade. Dentro de um tema: as notas **por passo** (um gráfico simples), as **perguntas em aberto** que ficaram para o próximo passo e **cada nota**, com a incerteza (firme, incerteza média ou incerta), a fonte (modelo ou documento), o passo que a escreveu e a data. Dá para filtrar por incerteza e fonte e buscar no texto. "Todas as notas" lista as de todos os temas, paginadas, com os mesmos filtros e o link para o tema de cada uma.

- **Sonhos (T-135):** cada execução com o horário, o status (ok, parcial, falhou), a duração, as conversas lidas, os **tokens gastos** (barra proporcional) e as **fases** com o resultado, as mudanças e o resumo; fase que estourou o orçamento ou deu erro aparece destacada. Abaixo, o **diário** (7, 30 ou 90 dias; sonhos, estudos, personalidade).
- **Conversas (T-135):** as sessões com o número de turnos e se o sonho já as leu (ou quantos turnos esperam o próximo); dentro de uma, a conversa em balões, começando pelos turnos mais novos. A resposta dela depois de ferramentas **não aparece**. Com `dashboard.transcripts: false` a página diz que está desligada.

- **Pesquisa (T-136):** uma caixa só sobre memórias, crenças, temas, notas, sentimentos e conversas (as conversas só com `dashboard.transcripts`). Cada coisa é procurada **por palavras** (todas as palavras da busca aparecem) e **por significado** (embeddings, com um piso de relevância); o que casa por palavras vem sempre antes. Se o modelo de embeddings não responder, a busca cai para palavras e avisa. As conversas são lidas até os últimos 3000 turnos. Cada resultado é um link para a página certa.

## O que vem a seguir {#depois}

A T-137 fecha a épica: este livro de bolso por inteiro, o aceite ponta a ponta e o roteiro de deploy.

## Configuração {#configuracao}

Veja as chaves `dashboard.*` em [Configuração](configuracao.html).

## Na VPS {#vps}

O compose de produção publica `${TAILSCALE_IP}:8766`, nunca em `0.0.0.0`. **A ACL do Tailscale precisa liberar a porta 8766** para os seus dispositivos (hoje ela só libera a `8765` e o SSH). Para HTTPS (instalar como app no celular, por exemplo), use `tailscale serve` na VPS; o código não muda.
