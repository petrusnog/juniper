---
title: Dashboard
group: Documentação Técnica
section: Clientes
order: 27
description: O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
status: pronta
tags: dashboard web painel memória afetos estudos notas pesquisa sonhos conversas responsivo login cookie csp adr-020
---

> **Nota:** esta página detalha o [ADR-020](adr-020.html). O dashboard é **somente leitura**. Código: `brain/dashboard/`.

## Visão geral {#visao-geral}

Um painel web para ver a memória da Juniper sem o terminal. Roda dentro do servidor (`juniper serve`), na porta **8766**, e abre em qualquer navegador de um dispositivo que alcance o servidor pela rede privada:

```text
http://<endereço-do-servidor-na-rede-privada>:8766/
```

O navegador nunca fala com o MongoDB: tudo passa pela [API HTTP](api-http.html#dashboard) do próprio cérebro. Nada neste painel escreve no banco (um teste confere).

## Login e defesas {#login}

- **Login:** a tela pede o token dos clientes (o mesmo do CLI e do app). O token **nunca vai na URL**: vai no cabeçalho `Authorization` de `GET /api/login`, e o servidor devolve um cookie `HttpOnly`, `SameSite=Strict`, que vale `dashboard.session_hours` (12 horas). Cinco tentativas erradas em cinco minutos bloqueiam aquele cliente por um tempo.
- **Host:** o cabeçalho `Host` precisa ser um IP, `localhost`, um nome `*.ts.net` ou um host de `dashboard.allowed_hosts` (defesa contra *DNS rebinding*); outro nome recebe 421.
- **Navegador:** CSP restritiva (nada inline nem externo), sem CORS, só `GET`. Tudo que vem do servidor entra na tela com `textContent`, nunca como HTML (um teste proíbe `innerHTML` e afins).
- **Saída:** tudo passa por um único ponto que **mascara segredos e remove vetores**.
- **Isolamento:** se a porta estiver ocupada ou uma rota falhar, o servidor e a conversa seguem funcionando; o problema vai para o log (evento `error` com `where=dashboard.start` ou `dashboard.request`).

## Páginas {#paginas}

A interface é responsiva (de 360 px a telas largas; menu lateral no computador, barra inferior no celular), com tema claro e escuro automático e navegação por teclado. Não usa biblioteca externa: os gráficos são SVG simples.

| Página | O que mostra |
|---|---|
| **Visão geral** | Como ela está agora (humor, sentimentos e ecos), a personalidade (radar de traços contra o jeito de partida), contadores de cada camada e o último sonho com as fases. |
| **Afetos** | A série do humor (7, 14 ou 30 dias) e os sentimentos (filtro conversa ou sonho); cada sentimento rastreia a fala que o causou, a resposta dela e a preocupação atingida. |
| **Memórias** | Busca e filtros (ativas, substituídas ou todas; brutas ou refinadas; tipo). O detalhe de uma refinada mostra a lembrança (contexto, o que ela sentiu, como ela era, relevância), a linhagem bruta ↔ refinada e as ligações. |
| **Memória central** | O núcleo da persona, o perfil, a personalidade (com a linha do tempo de cada traço), as crenças com as evidências, as preocupações e o **simulador do prompt**: escreva uma mensagem e veja o que a Memória V2 acrescentaria. |
| **Estudos** | Os temas, com passo, posição e intensidade; as notas por passo, as perguntas em aberto e cada nota com incerteza, fonte e data; "Todas as notas" com filtros. |
| **Sonhos** | Cada execução com horário, status, duração, conversas lidas, tokens gastos e as fases; abaixo, o diário. |
| **Conversas** | As sessões, quantos turnos esperam o próximo sonho e a conversa em balões. A resposta dela depois de ferramentas não aparece. Some com `dashboard.transcripts: false`. |
| **Pesquisa** | Uma caixa só sobre memórias, crenças, temas, notas, sentimentos e conversas, por palavras e por significado; o que casa por palavras vem antes. Sem o modelo de embeddings, cai para palavras e avisa. |

Uma camada desligada (por exemplo, a Memória V2) responde 503 com o motivo, e a página correspondente explica.

## Configuração {#configuracao}

As chaves `dashboard.*` estão em [Configuração](configuracao.html#trace). Para desligar: `dashboard.enabled: false`; a conversa não depende do dashboard.

## Acesso {#acesso}

- **Em desenvolvimento:** `http://127.0.0.1:8766/`, com o token de `.juniper-dev/state/token` ([Ambiente Docker](dev-docker.html)).
- **Em produção:** a porta é publicada só no endereço da rede privada e precisa estar liberada para os seus dispositivos; o passo a passo está em [Clientes em produção](clientes-producao.html#dashboard).

| Sintoma | Causa provável |
|---|---|
| Nada responde | A porta 8766 não está liberada na rede privada, ou não foi publicada no endereço certo. |
| "Host não aceito" (421) | O endereço usado não é IP, `localhost` nem `*.ts.net`: inclua-o em `dashboard.allowed_hosts`. |
| "muitas tentativas" (429) | Cinco tokens errados em cinco minutos; espere um pouco. |
| Volta sempre para o login | O token mudou no servidor (isso encerra as sessões) ou o cookie venceu. |
| A porta está ocupada | O servidor segue sem o dashboard e registra o aviso. |

**HTTPS (opcional):** atrás de um proxy que termine TLS e envie `X-Forwarded-Proto: https`, o cookie passa a ser marcado como `Secure`.

## Testes {#testes}

```bash
pytest tests/test_dashboard_server.py tests/test_dashboard_api.py tests/test_dashboard_frontend.py tests/test_acceptance_dashboard.py -v
```
