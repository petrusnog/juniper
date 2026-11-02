---
title: Lembretes
group: Documentação Técnica
section: Integrações
order: 31
description: Lembretes pessoais no MongoDB, envio matinal por Telegram e aviso único por voz.
status: pronta
tags: lembrete reminder telegram voz agenda mongodb create_reminder list_reminders update_reminder delete_reminder
---

## Visão geral

A Juniper guarda lembretes na coleção `reminders` do MongoDB, separados da memória subjetiva. É necessário `memory.session.store: mongo` para as ferramentas, o dashboard e o job do servidor. O fuso é `agent.timezone`.

Peça “me lembre de pagar a conta amanhã” ou “marque dentista amanhã às 16h e adicione um lembrete”. Para um evento novo com lembrete, `create_event(reminder=true)` usa uma única confirmação para ambos. Sem pedido de lembrete, ela oferece depois de criar o evento; aceitar chama `create_reminder` com os ids retornados, sem recriar o evento nem pedir confirmação adicional. Recusa explícita usa `reminder=false`.

`create_reminder`, `list_reminders`, `update_reminder` e `delete_reminder` permitem gerenciar tudo na conversa. Criar/editar um lembrete explicitamente pedido não pede confirmação adicional; excluir pede. Para editar/excluir, consulte a lista e use o id completo. Só aparecem os lembretes do usuário autenticado. O terminal local sem autenticação usa o proprietário `local`.

Um prazo sem hora (`AAAA-MM-DD`) vence ao fim do dia no fuso do agente. `notify_on` permite avisar em um dia anterior; por padrão é o dia do prazo. Não há alarme no minuto exato, recorrência ou varredura periódica do Google Calendar.

## Entrega e status

Os status são independentes e cumulativos:

- **Pendente:** ainda sem confirmação de entrega.
- **Enviado pelo Telegram:** o Telegram aceitou a mensagem; não comprova leitura.
- **Relembrado no chat de voz:** a fala inteira foi transmitida ao cliente sem erro; não comprova que o aparelho a reproduziu ou que a pessoa a ouviu.
- **Vencido:** o prazo passou, mesmo que tenha sido avisado antes. Vencimento é calculado na consulta; não precisa esperar o job diário. Lembretes vencidos ficam no histórico, mas não são enviados.

Na abertura da voz (botão ou ativação por voz), ela cumprimenta conforme o horário e avisa os lembretes disponíveis. A marcação no MongoDB evita repetir cada lembrete nas próximas sessões e aparelhos. Falha ou interrupção antes de transmitir a fala completa libera o aviso para outra tentativa. O envio Telegram não impede o aviso de voz e vice-versa.

O servidor agenda o envio diariamente às 08:00, por padrão. Ao reiniciar depois desse horário, tenta recuperar os lembretes ainda válidos e não enviados; antes desse horário, aguarda. Não exige cron no contêiner. Um lembrete criado depois do job será avisado por voz na próxima abertura; pelo Telegram, somente na próxima execução (se ainda não venceu). Para antecipar esse caso ou repetir uma tentativa que falhou, rode o script manual abaixo. Falhas no Telegram não são marcadas como sucesso.

As entregas usam leases atômicos por lembrete/canal para evitar dois workers enviando simultaneamente. Um processo que cai libera a lease por expiração em até dez minutos. Telegram não oferece chave de idempotência para sendMessage: uma queda depois de ele aceitar a mensagem e antes de salvar o recibo pode resultar em repetição na próxima tentativa. Não há garantia de exatamente uma entrega nessa janela.

Editar título/notas preserva os recibos. Reprogramar prazo/dia de aviso reinicia os dois canais. Editar/excluir durante envio é recusado; tente novamente ao terminar. As ferramentas de edição/exclusão de eventos sincronizam os lembretes vinculados. Se o Google gravar mas o MongoDB falhar, a resposta informa o sucesso parcial; não repita a criação do evento. Alterações feitas diretamente no Google não são sincronizadas automaticamente. Editar/apagar somente o lembrete não modifica o compromisso.

## Configurar Telegram

O bot precisa ter sido iniciado pela pessoa em uma conversa privada (`/start`). O token existente `JUNIPER_TELEGRAM_BOT_TOKEN` deve estar disponível também no processo **brain**, responsável pelo job; em produção ele lê o `.env` montado. Não é necessário iniciar outro consumidor de updates. Nunca distribua lembretes para toda a lista de permissão do bot.

Associe explicitamente o `user_id` da Juniper ao `chat_id` privado do Telegram no config pessoal usado como camada base em produção:

```yaml
reminders:
  enabled: true
  morning_at: "08:00"
  telegram_chat_ids:
    usr_exemplo: 123456789
```

Use o id real de `juniper users list`, não o nome nem o `client_id`. Sem associação o lembrete permanece disponível para voz e o log registra `telegram_unconfigured`. Chat ids privados precisam ser positivos; grupos são recusados para evitar divulgação de compromissos pessoais. A allowlist de conversa do bot é uma configuração separada. Alternativa no `.env`:

```dotenv
JUNIPER_REMINDERS__MORNING_AT=08:00
JUNIPER_REMINDERS__TELEGRAM_CHAT_IDS={"usr_exemplo":123456789}
```

Reinicie o brain após mudar configuração. Para testar/recuperar o envio real, depois do horário configurado (este comando **envia** as mensagens elegíveis):

```bash
docker compose -f docker-compose.prod.yml exec -T brain python -m brain.reminders
```

Ou, no checkout com ambiente Python ativo: `python -m brain.reminders`. O script usa a mesma lógica do job. Permita HTTPS para `api.telegram.org` no ambiente de execução.

## Dashboard e diagnóstico

A aba **Lembretes** permite criar, pesquisar, filtrar por status, editar e excluir, com recibos de cada canal e histórico dos vencidos. Usa o login do dono e a proteção anti-CSRF existente; a API obtém o proprietário da sessão, nunca de argumentos do navegador. Cada página mostra 25 itens; a consulta traz até 500 lembretes por usuário.

```bash
docker compose -f docker-compose.prod.yml exec -T brain python -m brain logs -e reminder --json
```

Eventos `telegram_sent`, `telegram_failed` e `telegram_unconfigured` identificam o lembrete sem gravar título, texto, chat id ou token. `voice.reminders`/`voice.reminder_receipt` indicam falha de consulta/gravação por voz. Os testes usam MongoDB simulado e Telegram/voz falsos; envio real depende das credenciais e do destino configurados no deploy.

## Para saber mais

- [Agenda Google](google-calendar.md): compromissos vinculados.
- [Configuração](configuracao.md): opções de lembretes e Telegram.
- [Dashboard](dashboard.md): login e gestão pelo navegador.
