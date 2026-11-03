---
title: Lembretes
group: Documentação Técnica
section: Integrações
order: 31
description: Lembretes pessoais no MongoDB, avisados no horário escolhido por Telegram e voz, e a diferença entre lembrete e evento da agenda.
status: pronta
tags: lembrete reminder telegram voz agenda mongodb create_reminder list_reminders update_reminder delete_reminder
---

## Visão geral

O [ADR-032](../decisions/ADR-032.md) define os limites de identidade, entrega e arquitetura. A Juniper guarda lembretes na coleção `reminders` do MongoDB, separados da memória subjetiva. É necessário `memory.session.store: mongo` para as ferramentas, o dashboard e o job do servidor. O fuso é `agent.timezone`.

### Lembrete isolado ou evento da agenda

São duas coisas diferentes, e a Juniper é instruída a não trocar uma pela outra:

| Você diz | O que ela faz | Aparece no Google Calendar? |
|---|---|---|
| “Me lembra de pagar a conta amanhã às 9h” / “me avisa às 15h para ligar pro João” | `create_reminder`: só um **aviso** no Telegram e na voz, no horário dito | Não |
| “Marca dentista quinta às 16h” / “agenda a reunião” | `create_event`: um **compromisso** no calendário, com checagem de conflito | Sim |
| “Marca dentista quinta às 16h e me avisa 1 hora antes” | `create_event(reminder=true, reminder_at=...)`: o compromisso **e** o aviso, numa só confirmação | Sim |

Na dúvida entre os dois ela pergunta “só avisar ou marcar na agenda?”. Todo aviso precisa de data **e hora**; se você não disse a hora, ela pergunta. Se você pediu só o evento, ela oferece o aviso depois (e a hora dele); aceitar chama `create_reminder` com os ids retornados, sem recriar o evento. Recusa explícita usa `reminder=false`.

`create_reminder`, `list_reminders`, `update_reminder` e `delete_reminder` permitem gerenciar tudo na conversa. Criar/editar um lembrete explicitamente pedido não pede confirmação adicional; excluir pede. Para editar/excluir, consulte a lista e use o id completo. Só aparecem os lembretes do usuário autenticado. O terminal local sem login não oferece as ferramentas de lembrete nem sincroniza a agenda com elas (não há dono a quem atribuir); use o cliente conectado ao servidor. Nenhum registro é criado sob `local`. Registros antigos sob `local` exigem migração administrativa explícita para o user_id correto, sem presumir quem é o dono.

Todo lembrete tem um **prazo** (`due_at`) e um **horário de aviso** (`notify_at`), em ISO 8601 no fuso do agente. Sem `notify_at`, o aviso sai no próprio prazo; com ele, antes (nunca depois). Uma data sem hora (`AAAA-MM-DD`) vale 9h nesse dia para o aviso, e como prazo vence ao fim do dia. Não há recorrência nem varredura periódica do Google Calendar.

## Entrega e status

Os status são independentes e cumulativos:

- **Pendente:** ainda sem confirmação de entrega.
- **Enviado pelo Telegram:** o Telegram aceitou a mensagem; não comprova leitura.
- **Relembrado no chat de voz:** a fala inteira foi transmitida ao cliente sem erro; não comprova que o aparelho a reproduziu ou que a pessoa a ouviu.
- **Vencido:** o prazo passou, mesmo que tenha sido avisado antes. Vencimento é calculado na consulta; não precisa esperar o job diário. Lembretes vencidos ficam no histórico, mas não são enviados.

Quando há lembretes pendentes, a abertura da voz cumprimenta conforme o horário e os anuncia. Sem lembretes, mantém o comportamento anterior: wake word usa a saudação original e abertura pelo botão fica silenciosa, sem gastar TTS. A marcação no MongoDB evita repetir cada lembrete nas próximas sessões e aparelhos. Falha ou interrupção antes de transmitir a fala completa libera o aviso para outra tentativa. Se a consulta dos lembretes falha, só a sessão com wake word avisa; a abertura pelo botão continua silenciosa. O envio Telegram não impede o aviso de voz e vice-versa.

O servidor verifica **a cada minuto** (no topo do minuto, sem cron no contêiner) quais lembretes já chegaram ao horário de aviso e ainda não foram entregues, e envia. O aviso sai com até um minuto de atraso. Se o brain ficou fora do ar, ao voltar ele ainda envia o que venceu até `reminders.grace_hours` (12 por padrão) depois do prazo; passado isso o aviso é descartado como obsoleto. Falha no Telegram não é marcada como sucesso: a tentativa se repete depois de 5 minutos, sem martelar a API. Uma queda do MongoDB gera uma linha de log por interrupção, não uma por minuto.

**Quem recebe:** todos os usuários de `telegram.allowed_user_ids` (`JUNIPER_TELEGRAM__ALLOWED_USER_IDS`), a mesma lista de quem pode falar com o bot, em conversa privada (o id do usuário é o id do chat). Se houver mais de um, cada um recebe uma vez; numa nova tentativa, quem já recebeu não recebe de novo. Como o bot atende todos eles com a mesma credencial, os lembretes de qualquer dono vão para essa lista: não há isolamento por usuário no Telegram (o mesmo limite do restante do sistema, `JUNIPER.md` §2). Lista vazia: não envia e registra `telegram_unconfigured`.

Um lembrete criado depois do horário de aviso sai no próximo minuto (se o aviso já passou e o prazo não, sai na hora). Para forçar uma passagem manual: `python -m brain.reminders`.

As entregas usam leases atômicos por lembrete/canal para evitar dois workers enviando simultaneamente. Um processo que cai libera a lease por expiração em até dez minutos. Telegram não oferece chave de idempotência para sendMessage: uma queda depois de ele aceitar a mensagem e antes de salvar o recibo pode resultar em repetição na próxima tentativa. Não há garantia de exatamente uma entrega nessa janela.

Editar título/notas preserva os recibos. Reprogramar prazo ou horário de aviso reinicia os dois canais. Ao mudar o prazo sem informar `notify_at`, preserva a antecedência (quanto antes do prazo o aviso sai), tanto pela agenda como pela ferramenta de lembretes. Vínculos informados pelo modelo são conferidos na agenda antes de gravar. Editar/excluir durante envio é recusado; tente novamente ao terminar. As ferramentas de edição/exclusão de eventos sincronizam os lembretes vinculados. Se o Google gravar mas o MongoDB falhar, a resposta informa o sucesso parcial; não repita a criação do evento. Alterações feitas diretamente no Google não são sincronizadas automaticamente. Editar/apagar somente o lembrete não modifica o compromisso.

## Configurar Telegram

Não há variável nova. Use o que já existe para o bot:

- `JUNIPER_TELEGRAM_BOT_TOKEN`: o token do bot, que precisa estar disponível também no processo **brain** (o job roda lá; em produção ele lê o `.env` montado). Nunca é registrado em log.
- `JUNIPER_TELEGRAM__ALLOWED_USER_IDS` (ou `telegram.allowed_user_ids`): quem recebe. Cada pessoa precisa ter iniciado o bot em conversa privada (`/start`).

Opcionais (já têm padrão), em `config.yaml`: `reminders.retention_days` (90) e `reminders.grace_hours` (12). Reinicie o brain depois de mudar a configuração. Permita HTTPS para `api.telegram.org` no ambiente de execução. Para forçar uma passagem agora (este comando **envia** o que já está no horário):

```bash
docker compose -f docker-compose.prod.yml exec -T brain python -m brain.reminders
```

## Dashboard e diagnóstico

A aba **Lembretes** permite criar, pesquisar, filtrar por status, editar e excluir, com recibos de cada canal e histórico dos vencidos. Usa o login do dono e a proteção anti-CSRF existente; a API obtém o proprietário da sessão, nunca de argumentos do navegador. Cada página mostra 25 itens; não há corte silencioso em 500. O job remove vencidos após a janela reminders.retention_days (90 dias por padrão). As escritas do dashboard (o campo “Quando avisar” aceita data/hora ISO) seguem o padrão das demais (parâmetros na *query* do `POST`, com sessão, origem e token anti-CSRF do dono), por isso as observações pelo dashboard vão até 500 caracteres; textos maiores (até 2000) só pela conversa. Aceitar corpo no servidor HTTP é uma mudança de transporte que fica para uma task própria.

```bash
docker compose -f docker-compose.prod.yml exec -T brain python -m brain logs -e reminder --json
```

Eventos `telegram_sent`, `telegram_failed` e `telegram_unconfigured` identificam o lembrete sem gravar título, texto, chat id ou token. `voice.reminders`/`voice.reminder_receipt` indicam falha de consulta/gravação por voz. Os testes padrão usam MongoDB simulado e Telegram/voz falsos. tests/test_reminders_integration.py adiciona verificações de índice parcial, upsert e leases concorrentes contra MongoDB real, em banco temporário; são marcadas integration e puladas se o serviço não estiver disponível. Esses cenários ainda não foram exercitados aqui contra MongoDB real. API Telegram, TTS e fluxo no aparelho também não foram exercitados com serviços reais; dependem do deploy.

## Para saber mais {#para-saber-mais}

- [Agenda Google](google-calendar.md): compromissos vinculados.
- [Configuração](configuracao.md): opções de lembretes e Telegram.
- [Dashboard](dashboard.md): login e gestão pelo navegador.
