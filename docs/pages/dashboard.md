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
| `/api/dreams`, `/api/journal` | Execuções do sonho (fases, tokens, erros) e o diário |
| `/api/sessions` (+ `/<id>`) | Conversas (somem com `dashboard.transcripts: false`); a resposta dela depois de tools fica de fora |

Camada desligada responde 503 com o motivo, sem erro. Nenhuma rota escreve no banco (um teste confere).

- **Casca e primeiras páginas (T-132):** responsivo de 360 px a telas largas (menu lateral no computador, barra inferior no celular), tema claro e escuro automático, teclado e foco visíveis. Sem biblioteca externa: os gráficos são SVG simples.
  - **Visão geral:** como ela está agora (humor, sentimentos e ecos), a personalidade (radar de traços contra o jeito de partida), contadores de cada camada e o último sonho com as fases.
  - **Afetos:** a série do humor (7, 14 ou 30 dias) e a lista de sentimentos (filtro conversa ou sonho). Ao clicar num sentimento aparece **de onde veio**: a fala do Petrus, a resposta dela e a preocupação atingida.
- **Segurança do frontend:** tudo que vem do servidor entra na tela com `textContent`, nunca como HTML (um teste proíbe `innerHTML` e afins), e a CSP bloqueia script e estilo inline.

## O que vem a seguir {#depois}

As páginas restantes: Memórias, Memória central (com o simulador do prompt), Estudos, Sonhos, Conversas e Pesquisa.

## Configuração {#configuracao}

Veja as chaves `dashboard.*` em [Configuração](configuracao.html).

## Na VPS {#vps}

O compose de produção publica `${TAILSCALE_IP}:8766`, nunca em `0.0.0.0`. **A ACL do Tailscale precisa liberar a porta 8766** para os seus dispositivos (hoje ela só libera a `8765` e o SSH). Para HTTPS (instalar como app no celular, por exemplo), use `tailscale serve` na VPS; o código não muda.
