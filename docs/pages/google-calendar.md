---
title: Agenda Google
group: Documentação Técnica
section: Integrações
order: 30
description: Ler, marcar, alterar e desmarcar compromissos no Google Calendar, com checagem de conflitos.
status: pronta
tags: calendar agenda google evento compromisso conta de serviço conflito list_events create_event update_event delete_event
---

## Visão geral {#visao-geral}

A primeira tool real da Juniper: ler, marcar, alterar e desmarcar os seus compromissos no Google Calendar, inclusive na sua agenda principal. Código em `brain/tools/google_calendar.py`. Decisões de fundo nos ADR-008 (conta de serviço, agenda "Juniper", relógio) e ADR-010 (gerenciamento completo) em [Decisões](adr.html).

A Juniper fala com o Google por uma **conta de serviço**: sem tela de consentimento, sem token que expira, sem navegador. O acesso é dado **compartilhando as agendas** com o e-mail da conta. Para criar a conta e a chave, veja [Instalação](install.html#gcp).

## As quatro tools {#tools}

### list_events {#list-events kind=tool file=brain/tools/google_calendar.py}

`READ`. Lista compromissos de um intervalo de dias (`start_date`, `end_date`, formato `AAAA-MM-DD`, no máximo 31 dias). Mostra título, horário e a agenda de cada um; os das agendas que ela gerencia trazem um **id**. O resultado volta marcado como "dados, não instruções" (títulos podem vir de convites de terceiros).

### create_event {#create-event kind=tool file=brain/tools/google_calendar.py}

`WRITE`. Marca um compromisso: `title`, `start`, e `end` **ou** `duration_minutes`; `calendar` opcional (`"juniper"` ou `"principal"`). Antes de criar, checa conflitos em **todas** as agendas. Recusa datas no passado. Cada evento ganha um lembrete (`google.reminder_minutes`) e a assinatura "Agendado pela Juniper".

### update_event {#update-event kind=tool file=brain/tools/google_calendar.py}

`WRITE`. Muda título, horário ou observações, pelo id. Mudando só o início, a duração é mantida. Checa conflitos, ignorando o próprio evento.

### delete_event {#delete-event kind=tool file=brain/tools/google_calendar.py}

`DESTRUCTIVE`. Apaga um evento pelo id, em qualquer agenda que ela gerencia. Irreversível: a confirmação diz em palavras o que será apagado.

## Quais agendas ela altera e quais só lê {#agendas}

| Tipo | Como configurar | O que pode |
|---|---|---|
| **Gerenciadas** | A agenda "Juniper" (`google.calendar_id`) e as suas em `google.manage_calendar_ids`, compartilhadas com a conta de serviço como "Fazer alterações em eventos" | Ler, criar, alterar e apagar |
| **Só leitura** | `google.read_calendar_ids`, compartilhadas como "ver todos os detalhes" | Entram na checagem de conflitos; nada é criado, alterado ou apagado nelas |

`google.default_calendar_id` diz onde caem os eventos novos quando você não especifica (vazio = a primeira de `manage_calendar_ids`; sem nenhuma, a agenda Juniper). A trava continua no código: `create_event`, `update_event` e `delete_event` só alcançam agendas gerenciadas (`CalendarClient.writable_ids`); uma agenda só lida é recusada com mensagem clara.

## O fluxo de conflito {#conflito}

1. O LLM chama `create_event` direto (sem listar antes).
2. O `precheck` valida a entrada e procura conflitos **antes** de te perguntar qualquer coisa.
3. Em conflito, o evento **não é criado**: o erro traz nome, horário e, se for apagável, o id. O LLM pergunta pelo nome do compromisso.
4. Se você concordar, `delete_event` e depois `create_event`.
5. Sem conflito, a Juniper pergunta "Posso marcar «Dentista», segunda 16h–17h?" e só cria depois do seu "sim" ([confirmação na conversa](agente.html#confirmacao)).

Se o conflito estiver na agenda principal e ela só puder ler, a Juniper apenas informa; a decisão é sua. O `run` checa de novo antes de gravar.

## Como a hora é interpretada {#hora}

- A tool recebe datas em ISO 8601 (`2026-10-12T16:00`). Sem fuso, vale o fuso configurado (`agent.timezone`); com fuso, é convertido.
- O LLM sabe a data de hoje porque o agente injeta o [relógio](agente.html#clock) em cada pergunta.
- Horário no passado é recusado e a mensagem diz que horas são "agora".
- Sem duração, o evento dura `google.default_duration_minutes` (60).
- Eventos de **dia inteiro** não bloqueiam um horário (feriados e aniversários não impedem marcar um dentista); eventos **colados** (um termina às 16h, o outro começa às 16h) não são conflito.

## Economia de tokens {#tokens}

O plano gratuito da Groq tem um limite baixo de tokens por minuto, e cada chamada reenvia o prompt. O fluxo foi desenhado para fazer menos chamadas e mandar menos texto:

| O que | Efeito |
|---|---|
| `create_event` já checa conflitos (`precheck`), sem `list_events` antes | Marcar algo cai de ~3 chamadas ao LLM para 2. |
| `precheck` antes da confirmação | Você nunca confirma algo que vai falhar. |
| Prompt de sistema estável (a hora vai na mensagem do usuário) | O começo do prompt é idêntico em todas as chamadas, o que permite cache no provider. |
| `llm.groq.reasoning_effort: low` | Menos tokens de raciocínio (cobrados como resposta). |
| Descrições das tools curtas | Um teste impede que cresçam sem querer. |
| Histórico enxuto | Só os 8 últimos turnos; resultados de tools antigos são descartados. |

## Erros {#erros}

Nenhuma tool levanta exceção: tudo volta como `ToolResult(success=False)` e o LLM explica o problema.

| Situação | Resultado |
|---|---|
| Entrada inválida (data malformada, título vazio, fim antes do início, duração zero, horário no passado) | Recusa com a causa. |
| Conflito | Não cria; informa nome, horário e (se apagável) o id. |
| Configuração (`calendar_id` ausente, chave não encontrada ou ilegível) | Recusa com orientação. |
| Google (404/410 não encontrado, 401/403 recusou, 5xx, sem conexão) | Mensagem **nossa**, nunca o texto cru do Google. |

A chave nunca aparece em mensagens de erro (há teste para isso). Os modelos às vezes mandam número como texto (`duration_minutes: "30"`); a tool aceita.

## Limitações conhecidas {#limitacoes}

- Só eventos simples: sem convidados, repetição, local ou evento de dia inteiro criado por ela. Eventos recorrentes aparecem instância a instância, e apagar ou mudar afeta só a ocorrência escolhida.
- Máximo de 100 eventos por agenda em cada consulta.
- O criador do evento aparece como o e-mail da conta de serviço.
- **Privacidade:** títulos e horários que a Juniper lê vão como texto ao LLM (na nuvem).

## Verificar e testar {#testar}

```bash
python scripts/check_google.py      # confere a chave, a conta de serviço e se as agendas estão acessíveis
python scripts/try_calendar.py      # conversa de demonstração (mostra os tokens de cada chamada)
pytest tests/test_google_calendar.py -v
```

Os testes usam um cliente falso e relógio fixo: listar, criar, conflitos (apagável e não apagável), datas inválidas e passadas, apagar, mapeamento de erros HTTP e a garantia de que a chave não vaza.
