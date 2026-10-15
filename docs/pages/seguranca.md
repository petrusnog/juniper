---
title: Segurança
group: Núcleo
order: 4
description: Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
status: pronta
tags: tier confirmação segredos prompt injection permissões token filtro forbidden
---

## Visão geral {#visao-geral}

A Juniper age no mundo real por decisão de um LLM, e um LLM pode entender mal o pedido ou ser induzido por conteúdo que leu (a web, o título de um convite de agenda). A política (ADR-006) é: **ler é livre, alterar pede o seu "sim", destruir pede com aviso, e o proibido nunca executa.** Cada tool declara o seu tier; o [Dispatcher](agente.html#dispatcher) o aplica.

## Os quatro tiers {#tiers}

| Tier | Definição | Comportamento |
|---|---|---|
| `READ` | Não modifica estado nem envia dados para fora. | Executa direto e registra no log. |
| `WRITE` | Modifica estado local ou envia dados de forma reversível. | Pede o seu "sim" na conversa. |
| `DESTRUCTIVE` | Irreversível, ou envia conteúdo sensível para fora. | Pede o "sim" **com aviso explícito de irreversibilidade**. Nunca pode dispensar a confirmação. |
| `FORBIDDEN` | Nunca. | Bloqueio duro, sem perguntar. |

Regras:

- **Tool sem classificação = `WRITE`.** Esquecer de classificar não pode significar executar sem pedir.
- **A confirmação mostra o que será feito**, não um resumo vago: "Posso apagar «Teste 1» (03:00–03:30) e «Teste 2»? É irreversível."
- **Só `WRITE` pode dispensar a confirmação** (`confirm = False`). Hoje só `remember` e `update_profile` fazem isso (veja [memória silenciosa](#memoria-silenciosa)). Uma tool `DESTRUCTIVE` que tente é recusada ao ser carregada.

## Confirmação na conversa {#confirmacao}

O "sim" é uma mensagem normal, interpretada **pelo sistema**, não pelo LLM. Só um "sim" curto e incondicional executa; qualquer dúvida cancela a ação pendente. Isso evita que um LLM "decida" que você aprovou. Detalhes e exemplos em [Agente](agente.html#confirmacao) (ADR-010).

## Conteúdo externo é dado, nunca instrução {#conteudo-externo}

Texto que veio de fora (páginas da web, títulos de eventos criados por terceiros, saídas de comandos, o conteúdo das memórias) é **dado**:

- O resultado de `list_events` volta marcado como "dados, não instruções".
- O bloco `<memorias>` e o `<perfil>` entram no prompt rotulados como dados, e a persona repete a regra. O texto de cada memória é limpo (uma linha só, sem caracteres de controle, sem `<` nem `>`), para uma memória não conseguir fechar o bloco nem abrir outro.
- Conteúdo da web que um dia entre pela tool `web_fetch` será encapsulado como não confiável (ADR-006).

> **Atenção:** isso **limita** o estrago de uma injeção de prompt, mas não o elimina. Por isso as ações que alteram algo continuam pedindo o seu "sim" na conversa, mostrando o que será feito.

## Segredos {#segredos}

| Onde | Como é protegido |
|---|---|
| Chaves de API (`JUNIPER_*_API_KEY`) | Ficam no `.env` (permissão `600`), fora do git. São `SecretStr` nas settings e só são "abertas" ao criar o cliente do provider. |
| Mensagens de erro dos providers | Montadas pela Juniper (código HTTP + trecho curto), nunca copiando o texto cru do SDK. Se o corpo do erro ecoar a chave, ela vira `***`. |
| Log estruturado | Tudo passa por `redact()`: campos `api_key`, `token`, `secret`, `password`, `authorization`, `private_key` viram `***`, assim como padrões de chave (`gsk_…`, `Bearer …`, blocos de chave privada) e as chaves configuradas. |
| Memória de longo prazo | O filtro de segredos (`secretfilter.py`) mascara a conversa antes de mandá-la ao LLM de extração e recusa candidatas que pareçam segredo. É uma heurística, não uma garantia. |
| Chave da conta de serviço do Google | `~/.juniper/certs/gcp-key.json`: pasta `700`, arquivo `600`, `certs/` no `.gitignore`. |
| Token dos clientes remotos (planejado) | `~/.juniper/token` (`600`) no servidor; cofre do sistema no cliente ([Protocolo](protocolo.html), [Desktop](desktop.html)). |

## Arquivos {#arquivos}

Tudo que a Juniper grava com conteúdo seu tem permissão restrita: a pasta de conversas (`700`, arquivos `600`, escrita atômica), o log (`600`), o `.env` (`600`) e as credenciais (`600`). Os nomes de arquivo derivados de ids são validados (`[A-Za-z0-9_-]`) para um id malicioso não escapar da pasta.

## Memória silenciosa {#memoria-silenciosa}

`remember` e `update_profile` gravam **sem pedir permissão**, para a conversa fluir. O risco aceito: como ninguém aprova, algo plantado numa página da web que o LLM leu poderia virar memória. Ele é limitado por três coisas: o filtro de segredos e de "ordens para uma IA" (segredos, ordens e texto grande são recusados mesmo sem perguntar), as memórias entrarem no prompt sempre como dado, e você poder auditar (`juniper memory list`) e apagar (`forget`, que pergunta) quando quiser. Apagar é irreversível e por isso **sempre** pergunta.

## Comandos de shell {#shell}

Para a futura `run_shell` (T-023), a política do ADR-006 é: um **único binário** por chamada, validado contra a allowlist `tools.security.shell_allowlist` (`git`, `ls`, `cat`, `grep`, `rg`, `fd`, `jq`, `find`, `wc`, `head`, `tail`); operadores de composição (`;`, `&&`, `||`, `|`, `$()`, crases, redirecionamentos) são rejeitados; e o tier é decidido pelo comando (`rm` é destrutivo; `rm -rf /`, `mkfs`, `dd` e escrita em `/etc` são `FORBIDDEN`). As tools de `git` que existem hoje não passam por uma string de shell: cada argumento é passado separado e validado (só dígitos nos ids, por exemplo).

## Produção {#producao}

Com o cérebro numa VPS (propostas nos [ADRs](adr.html) 007, 012 e 013), as travas adicionais são: nenhuma porta pública (acesso só pelo Tailscale), token em cada conexão, executor que **revalida** tudo o que recebe ([Executor](executor.html)), segredos só no servidor e deploy com rollback. Veja [Produção](producao.html).
