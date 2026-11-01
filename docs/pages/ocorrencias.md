---
title: Ocorrências e ações do dashboard
group: Documentação Técnica
section: Núcleo
order: 11
description: Como registrar qualquer bug ou comportamento errado (pelo dashboard ou por fala, com a tool report_issue), como o dono acompanha cada ocorrência, e as ações de memória e as tarefas (sonho e reindexação) que o dashboard executa.
status: pronta
tags: ocorrências bug issue report_issue registro severidade estado ações memória esquecer peso preocupação personalidade sonho reindexação tarefas confirmação csrf adr-028
---

> **Nota:** esta página detalha o [ADR-028](../decisions/ADR-028.md). Código: `brain/memory/issues.py`, `brain/tools/issues.py`, `brain/dashboard/actions.py`, `brain/dashboard/jobs.py`.

## Ocorrências de qualquer tipo {#ocorrencias kind=conceito file=brain/memory/issues.py}

Além dos vazamentos ([Auditoria de vazamentos](vazamentos.md)), a Juniper guarda **registros**: um título, o que aconteceu, o tipo (`bug`, `vazamento`, `comportamento`, `ideia`, `outro`), a gravidade (`baixa`, `media`, `alta`) e um estado que o dono move (`aberta`, `investigando`, `resolvida`, `descartada`). Cada registro leva a origem (`fala` ou `dashboard`), a conversa, o canal, o `owner_id` de quem o criou e um histórico de mudanças e notas.

Os três tipos de ocorrência aparecem juntos na página **Ocorrências** do [dashboard](dashboard.md#paginas) (filtros *Marcadas por mim*, *Detectadas* e *Registradas*) e na planilha `.xlsx`, que ganhou as colunas *Tipo de ocorrência*, *Gravidade* e *Estado*. Os registros ficam na coleção `issues` do MongoDB; sem o MongoDB (`memory.session.store: mongo`), não há onde guardá-los e a tool não é oferecida.

Texto livre passa pelo filtro de segredos antes de gravar: um segredo nunca é guardado.

## Registrar por fala {#tool kind=ferramenta file=brain/tools/issues.py}

A tool `report_issue` (WRITE, [com confirmação na conversa](seguranca.md#confirmacao)) vale para frases como «isso é um bug», «anota que você errou a data». A Juniper escreve o título e a descrição com as palavras da pessoa, e o sistema mostra o que vai gravar (*registrar a ocorrência «…» (tipo bug, gravidade alta)*) e espera o «sim».

- **Quem e onde** vêm do sistema (a conversa, o canal e o usuário da conexão), nunca dos argumentos que o modelo manda.
- A tool **só cria**. Mudar o estado, a gravidade, anotar ou apagar é do dono, no dashboard: o modelo não limpa o próprio histórico de erros.
- Para vazamento de instruções ou tags continua valendo `/vazou` ou «isso vazou».

## Acompanhar no dashboard {#acompanhar}

Na página Ocorrências o dono registra uma nova, e em cada registro muda o estado e a gravidade, adiciona notas e apaga (digitando o id). Rotas em [API HTTP](api-http.md#acoes).

## Ações sobre a memória {#memoria}

As mesmas operações dos comandos `juniper memory …`, pelo navegador, só para o dono:

| Onde | Ação | Confirmação |
|---|---|---|
| Memórias (detalhe) | Esquecer a memória, junto com as versões antigas que ela substituiu | digitar o id |
| Memória central | Esquecer uma opinião (crença) | digitar o id |
| Memória central | Mudar o peso de uma preocupação (0 a 1, fica no histórico dela) | não pede |
| Memória central | Voltar a personalidade à versão anterior (cria uma versão nova; o histórico fica) | não pede |
| Estudos (tema) | Esquecer o tema | digitar o id |
| Afetos | Apagar todos os sentimentos | digitar `sentimentos` |

A confirmação por id é **conferida no servidor**, não só na tela. Cada ação vai ao log (`dashboard`, `op`, `actor`, `target`) sem nenhum texto da memória. `study add` continua só na CLI: o texto de estudo não cabe na *query* dos `POST`.

## Tarefas: sonho e reindexação {#tarefas kind=classe file=brain/dashboard/jobs.py}

A página **Tarefas** roda, em segundo plano e uma por vez, o sonho (`juniper memory dream`, com a opção de ensaio) e a reindexação (`juniper memory reindex`, com a opção de refazer tudo). São as mesmas rotinas dos comandos. O servidor segue conversando; uma segunda tarefa recebe `409`, e o sonho também recusa começar se a passada da noite estiver rodando. As últimas 10 execuções, com o relatório ou o erro, ficam em memória (reiniciar o servidor esquece a lista, não o efeito); desligar o servidor interrompe a tarefa em curso.

## Fora do dashboard {#fora}

Deploy, reinício e backup da VPS ficam de fora de propósito: o dashboard roda dentro do contêiner do cérebro e não deve controlar o próprio host.

## Testes {#testes}

```bash
pytest tests/test_issues.py tests/test_dashboard_memory_actions.py tests/test_dashboard_jobs.py tests/test_dashboard_ops.py -v
```

## Para saber mais {#para-saber-mais}

- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper, com as páginas de leitura, logs, saúde e as ações do dono.
- [Auditoria de vazamentos](vazamentos.md) (Documentação Técnica): Como a Juniper registra as respostas em que escapou maquinaria e como marcar um vazamento na hora com /vazou.
- [Usuários e credenciais](usuarios.md) (Documentação Técnica): Quem pode entrar no dashboard e executar essas ações, e como o dono é identificado.
- [Memória V2](memoria-v2.md) (Documentação Técnica): As camadas que as ações de memória alteram: crenças, preocupações, personalidade e estudos.
- [Tools](tools.md) (Documentação Técnica): O registro de tools, os tiers e a confirmação; `report_issue` é uma delas.
- [API HTTP](api-http.md) (Referência): As rotas de leitura e de escrita do dashboard.
