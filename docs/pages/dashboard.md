---
title: Dashboard
group: Documentação Técnica
section: Clientes
order: 27
description: O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas, ocorrências de vazamento (com exportação em planilha) e pesquisa, somente leitura, servido pelo próprio cérebro.
status: pronta
tags: dashboard web painel memória afetos estudos notas pesquisa sonhos conversas responsivo login cookie csp adr-020
---

> **Nota:** esta página detalha o [ADR-020](../decisions/ADR-020.md). O dashboard é **somente leitura**. Código: `brain/dashboard/`.

## Visão geral {#visao-geral}

Um painel web para ver a memória da Juniper sem o terminal. Roda dentro do servidor (`juniper serve`), na porta **8766**, e abre em qualquer navegador de um dispositivo que alcance o servidor pela rede privada:

```text
http://<endereço-do-servidor-na-rede-privada>:8766/
```

O navegador nunca fala com o MongoDB: tudo passa pela [API HTTP](api-http.md#dashboard) do próprio cérebro. Nada neste painel escreve no banco (um teste confere).

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
| **Ocorrências** | Os vazamentos na fala: os que você marcou com `/vazou` ou «isso vazou» (com as últimas 10 interações) e os que a [auditoria](vazamentos.md) detectou (com o texto bruto do modelo e o entregue). A lista filtra por origem; o detalhe mostra a sessão, o canal, a nota ou as regras e as mensagens em balões, e cada tela tem o botão **Baixar planilha (.xlsx)**. Precisa do MongoDB: sem ele a tela explica. |
| **Pesquisa** | Uma caixa só sobre memórias, crenças, temas, notas, sentimentos e conversas, por palavras e por significado; o que casa por palavras vem antes. Sem o modelo de embeddings, cai para palavras e avisa. |

Uma camada desligada (por exemplo, a Memória V2) responde 503 com o motivo, e a página correspondente explica.

## Exportar ocorrências em planilha {#exportar kind=função file=brain/dashboard/occurrences.py}

`GET /api/occurrences/export.xlsx` (com o login do dashboard) devolve um `.xlsx` com duas abas:

| Aba | Uma linha por | Colunas |
|---|---|---|
| **Mensagens** | mensagem | Ocorrência, Tipo (`Relato (/vazou)` ou `Detectado`), ID da sessão, Canal, Voz, Data da ocorrência, Ordem, **Autor** (`Usuário`, `Juniper` ou `Juniper (texto bruto do modelo)`), **Mensagem**, Observação |
| **Ocorrências** | ocorrência | Ocorrência, Tipo, ID da sessão, Canal, Voz, Data, Nota, Regras, se o texto vazou até você, Modelo, Commit, número de mensagens |

Filtros na URL: `kind=relato\|automatico\|todos` (padrão `todos`), `days` (janela das detecções, padrão 30) e `id` (uma ocorrência só, o id curto da lista). No máximo 500 de cada origem. As datas saem no fuso do agente.

O arquivo é gerado sem biblioteca extra (`brain/dashboard/xlsx.py`): o texto vai como *texto*, nunca como fórmula (uma mensagem que começa com `=` aparece como foi dita), caracteres que o XML não aceita são trocados, uma célula é cortada antes do limite do Excel e os segredos passam pelo mesmo mascaramento do resto do dashboard.

## Configuração {#configuracao}

As chaves `dashboard.*` estão em [Configuração](configuracao.md#trace). Para desligar: `dashboard.enabled: false`; a conversa não depende do dashboard.

## Acesso {#acesso}

- **Em desenvolvimento:** `http://127.0.0.1:8766/`, com o token de `.juniper-dev/state/token` ([Ambiente Docker](dev-docker.md)).
- **Em produção:** a porta é publicada só no endereço da rede privada e precisa estar liberada para os seus dispositivos; o passo a passo está em [Clientes em produção](clientes-producao.md#dashboard).

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
pytest tests/test_dashboard_server.py tests/test_dashboard_api.py tests/test_dashboard_frontend.py tests/test_acceptance_dashboard.py tests/test_dashboard_occurrences.py tests/test_dashboard_xlsx.py -v
```

## Para saber mais {#para-saber-mais}

- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Login com MFA](login.md) (Documentação Técnica): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [Memória V2](memoria-v2.md) (Documentação Técnica): A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com o algoritmo de sonho.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Clientes em produção](clientes-producao.md) (Produção): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o dashboard.
- [Operação e manutenção](operacao.md) (Produção): O dia a dia do servidor: comandos dentro do contêiner, logs, saúde, comandos administrativos da memória, atualização manual, troca de segredos e como recriar tudo do zero.
