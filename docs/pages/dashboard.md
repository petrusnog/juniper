---
title: Dashboard
group: Documentação Técnica
section: Clientes
order: 27
description: O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas, ocorrências de vazamento (com exportação em planilha), pesquisa e a gestão de usuários, servido pelo próprio cérebro; só a gestão de usuários escreve.
status: pronta
tags: dashboard web painel memória afetos estudos notas pesquisa sonhos conversas responsivo login cookie csp adr-020
---

> **Nota:** esta página detalha o [ADR-020](../decisions/ADR-020.md). O dashboard é **somente leitura**, com uma exceção: a gestão de usuários e credenciais ([ADR-027](../decisions/ADR-027.md)). Código: `brain/dashboard/`.

## Visão geral {#visao-geral}

Um painel web para ver a memória da Juniper sem o terminal. Roda dentro do servidor (`juniper serve`), na porta **8766**, e abre em qualquer navegador de um dispositivo que alcance o servidor pela rede privada:

```text
http://<endereço-do-servidor-na-rede-privada>:8766/
```

O navegador nunca fala com o MongoDB: tudo passa pela [API HTTP](api-http.md#dashboard) do próprio cérebro. Nada neste painel escreve no banco, exceto a página **Usuários** ([Usuários e credenciais](usuarios.md#dashboard)); só `GET` responde nas demais rotas (um teste confere).

## Login e defesas {#login}

- **Login:** a tela pede o **client_id** e o **client_secret** do usuário ([Usuários e credenciais](usuarios.md)). Eles **nunca vão na URL**: vão no cabeçalho `Authorization` de `GET /api/login`, e o servidor devolve um cookie `HttpOnly`, `SameSite=Strict`, que vale `dashboard.session_hours` (12 horas) e é ligado àquela credencial: rotacioná-la ou revogá-la derruba a sessão. Cinco tentativas erradas em cinco minutos bloqueiam aquele cliente por um tempo. Enquanto `deployment.auth.legacy_token` for `true`, o token estático ainda entra (deixe o *client_id* em branco).
- **Host:** o cabeçalho `Host` precisa ser um IP, `localhost`, um nome `*.ts.net` ou um host de `dashboard.allowed_hosts` (defesa contra *DNS rebinding*); outro nome recebe 421.
- **Navegador:** CSP restritiva (nada inline nem externo), sem CORS, só `GET`. Tudo que vem do servidor entra na tela com `textContent`, nunca como HTML (um teste proíbe `innerHTML` e afins).
- **Saída:** tudo passa por um único ponto que **mascara segredos e remove vetores**.
- **Isolamento:** se a porta estiver ocupada ou uma rota falhar, o servidor e a conversa seguem funcionando; o problema vai para o log (evento `error` com `where=dashboard.start` ou `dashboard.request`).

## Páginas {#paginas}

A interface é responsiva e *mobile first* (de 360 px a telas largas; barra inferior no celular, menu lateral no computador) e tem navegação por teclado. Não usa biblioteca externa: os gráficos são SVG simples.

## Identidade visual {#visual kind=conceito file=brain/dashboard/static/assets/app.css}

O dashboard, o [livro de bolso](index.md#como-e-feita) e o [app do celular](mobile.md) têm o **mesmo visual de terminal**, num tema único e escuro, qualquer que seja o tema do sistema:

- **Cores:** fundo preto, o verde neon da logo (`#39e639`) para o que é ação, o item ativo e a fala do usuário, e a paleta Tango dos terminais Linux para o resto (ciano para a fala dela, âmbar para avisos, vermelho para erros, verde claro para o que é positivo). Os valores são exatamente os de `mobile/src/theme/tokens.ts`, e um teste confere que os dois arquivos não se afastam.
- **Fonte:** monoespaçada do sistema (`ui-monospace`, Cascadia, JetBrains Mono, Menlo, Consolas…), sem baixar nada. Cantos retos, brilho verde no foco, `>` antes dos títulos e do item ativo do menu, `$` nas falas do usuário e um cursor que pisca ao lado do nome.
- **Logo:** na tela de login, grande, com **JUNIPER** em caixa alta embaixo; no dashboard, **sempre visível** na barra superior (fixa, em qualquer tela e ao rolar), com o nome **Juniper** ao lado direito. Clicar nela volta à visão geral. A imagem (`assets/logo.png`, com fundo transparente) vem de `mobile/branding/juniper-neon.jpg` e é servida pelo próprio dashboard, como o ícone da aba (`favicon.png`): a política de segurança só aceita imagens do próprio endereço.
- **A marca animada:** é o par «logo + **Juniper** à direita + cursor `_` piscando», o mesmo nas três interfaces: na barra do dashboard, no menu lateral e na página inicial do livro de bolso, e no topo do app. No login, o cursor pisca ao lado de **JUNIPER**.
- **Movimento:** o cursor piscando respeita `prefers-reduced-motion` (parado, quando o sistema pede menos movimento).

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
| **Usuários** | Só o dono. Usuários e credenciais (dispositivo, criação, último uso), cadastro, nova credencial, rotação, revogação e desativação. O `client_secret` aparece uma vez, num aviso que some ao fechar. Detalhes em [Usuários e credenciais](usuarios.md#dashboard). |
| **Pesquisa** | Uma caixa só sobre memórias, crenças, temas, notas, sentimentos e conversas, por palavras e por significado; o que casa por palavras vem antes. Sem o modelo de embeddings, cai para palavras e avisa. |

Uma camada desligada (por exemplo, a Memória V2) responde 503 com o motivo, e a página correspondente explica.

## Exportar ocorrências em planilha {#exportar kind=função file=brain/dashboard/occurrences.py}

`GET /api/occurrences/export.xlsx` (com o login do dashboard) devolve um `.xlsx` com duas abas:

| Aba | Uma linha por | Colunas |
|---|---|---|
| **Mensagens** | mensagem | Ocorrência, Tipo (`Relato (/vazou)` ou `Detectado`), ID da sessão, Canal, Voz, Data da ocorrência, Ordem, **Autor** (`Usuário`, `Juniper` ou `Juniper (texto bruto do modelo)`), **Mensagem**, Observação |
| **Ocorrências** | ocorrência | Ocorrência, Tipo, ID da sessão, Canal, Voz, Data, Nota, Regras, se o texto vazou até você, Modelo, Commit, número de mensagens |

Filtros na URL: `kind=relato\|automatico\|todos` (padrão `todos`), `days` (janela das detecções, padrão 30) e `id` (uma ocorrência só, o id curto da lista). A lista mostra até 500 de cada origem; a planilha leva as **200** mais novas de cada (ela traz todas as mensagens de cada ocorrência). As datas saem no fuso do agente.

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

- [Auditoria de vazamentos](vazamentos.md) (Documentação Técnica): Como a Juniper registra as respostas em que escapou maquinaria (tags, instruções, trechos do prompt), como marcar um vazamento na hora com /vazou (texto e voz), o que cada registro guarda, como consultar com `juniper vazamentos` e por que esses turnos ficam fora da memória.
- [Observabilidade](observabilidade.md) (Documentação Técnica): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo por etapa.
- [Usuários e credenciais](usuarios.md) (Documentação Técnica): O cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash) e a migração do token estático.
- [Login com MFA](login.md) (Documentação Técnica): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [Memória V2](memoria-v2.md) (Documentação Técnica): A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em camadas, com o algoritmo de sonho.
- [Servidor WebSocket](servidor.md) (Documentação Técnica): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Clientes em produção](clientes-producao.md) (Produção): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o dashboard.
