---
title: Servidor WebSocket
group: Documentação Técnica
section: Clientes
order: 20
description: Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores, voz, saúde, tarefas diárias e desligamento.
status: pronta
tags: servidor websocket juniper serve brainserver chathandler conversa turno conexão health shutdown 1012 executor voz scheduler canal
---

No modo `server`, o comando `juniper serve` sobe o cérebro como um processo próprio que atende os clientes por um único WebSocket ([ADR-012](../decisions/ADR-012.md)). O contrato das mensagens está na [API WebSocket](protocolo.md); esta página explica como o servidor se organiza por dentro.

## Peças {#pecas}

| Peça | Arquivo | Responsabilidade |
|---|---|---|
| `BrainServer` | `brain/clients/server.py` | Escuta, autentica antes do upgrade, faz o handshake, mantém o heartbeat e o ciclo de vida de cada conexão. |
| `ChatHandler` | `brain/clients/chat.py` | O que acontece dentro de uma conexão: chat, sessões, tools, memória e voz. Roda o `Agent`. |
| `ExecutorHub` | `brain/clients/executors.py` | O catálogo das tools que os dispositivos registram e o roteamento de cada `invoke` ([Executor](executor.md)). |
| Serviço de memória | `brain/clients/memory_service.py` | Atende `memory.request` com as mesmas operações do `juniper memory`. |
| Sessão de voz | `brain/clients/voice_session.py` | Uma conversa por voz por conexão ([Voz por áudio](voz-audio.md#sessao)). |
| Protocolo | `brain/clients/protocol.py` | Os modelos pydantic de cada mensagem; geram `docs/protocol/schema.json`. |
| Dashboard | `brain/dashboard/` | Um segundo servidor HTTP, só leitura, no mesmo processo ([Dashboard](dashboard.md)). |

## Conexão {#conexao kind=classe file=brain/clients/server.py}

1. **HTTP antes do upgrade.** `GET /health` responde sem autenticação. `GET /app/update` e `GET /app/update/apk` (o download do app Android, com o mesmo Bearer, [API HTTP](api-http.md#app-update)) também passam por aqui. Qualquer outro caminho que não `/v1/ws` recebe 404. Em `/v1/ws`, o cabeçalho `Authorization: Bearer <client_id>.<client_secret>` é conferido pelo serviço de identidade ([Usuários e credenciais](usuarios.md)), em tempo constante e com limite de tentativas (ou, com `legacy_token`, comparado com o token do servidor); sem ele, 401 e nada de WebSocket.
2. **Handshake.** O cliente manda `hello` com `protocol_version` e a identificação (`client.kind`, `device_id`); o servidor responde `welcome` com a conversa aberta ou retomada. Versão incompatível fecha com `4426`; uma segunda conexão com o mesmo `device_id` substitui a primeira (`4409`).
3. **Heartbeat.** O servidor manda `ping` a cada 30 s; 90 s sem nenhum sinal fecham a conexão (`4408`).
4. **Mensagens.** Cada quadro é validado contra o protocolo; um quadro malformado recebe `error` com `bad_request`, sem fechar a conexão. O handler precisa **voltar logo**: trabalho longo (um turno do agente) roda numa tarefa acompanhada pela conexão, para o servidor continuar lendo `pong` e `chat.cancel`.
5. **Fim.** Quando a conexão acaba, as tarefas dela são canceladas, o catálogo do executor é removido e a conversa é desligada da conexão.

Um bug numa conexão nunca derruba o servidor: a exceção vai ao log e a conexão fecha com `1011`.

## Conversas e turnos {#conversas kind=classe file=brain/clients/chat.py}

- **Uma conversa, uma conexão.** Uma `Conversation` (histórico, confirmação pendente) fica ligada a uma conexão por vez. Se outra conexão a retoma, a anterior recebe `session.detached`. A confirmação pendente **não sobrevive** a essa troca, a uma queda nem a um `session.resume` ([ADR-010](../decisions/ADR-010.md)).
- **Um turno por vez.** Um `chat.send` com um turno em andamento recebe `turn_in_progress`.
- **O turno pertence à conversa, não à conexão.** Se o cliente cai no meio, o turno termina no servidor, a conversa é salva com a resposta e a extração de memória conclui. Ao retomar, `session.state.history` traz a resposta completa.
- **Tempo máximo.** Um turno tem até 120 s, além do limite de voltas do agente (`agent.max_iterations`).
- **Canal do pedido.** A cada turno o servidor diz ao modelo por onde o pedido chegou ("Canal: aplicativo do celular (voz)", "Canal: telegram") e deixa isso disponível às tools (`brain/tools/channel.py`). É assim que `play_music` abre a música no celular quando o pedido veio do app e devolve o link quando veio do terminal ou do Telegram ([Música](musica.md#play-music)).

## Memória ao subir {#aquecimento}

Com a memória de longo prazo ligada, o servidor aquece o modelo de embeddings em segundo plano ao subir (na primeira vez, ele é baixado). Enquanto isso, as conversas funcionam sem consultar a memória e o `/health` responde `{"status": "degraded", "memory": "warming_up"}`, ainda com HTTP 200, para o *healthcheck* do Docker não reiniciar o contêiner. Pronto, vira `{"status": "ok", "memory": "ready"}` (ou `"memory": "off"` sem memória de longo prazo). A resposta também traz a versão do cérebro.

## Tarefas diárias {#agendador kind=módulo file=brain/scheduler.py}

Não há cron dentro do contêiner: o próprio servidor agenda as tarefas no fuso do agente (`brain/scheduler.py`).

| Tarefa | Quando | O que faz |
|---|---|---|
| Rotação de logs | `logging.rotate_at` (00:10) e uma vez ao subir | Compacta os logs de dias anteriores e apaga os vencidos ([Observabilidade](observabilidade.md#rotacao)). |
| Sonho da Memória V2 | `memory.v2.night.at` (03:00), só com a V2 ligada | Relê o dia e atualiza as camadas da V2 ([Memória V2](memoria-v2.md#passada-noturna)). |

## Desligamento {#desligamento}

Ao receber `SIGTERM` (um deploy, um `docker compose up` com imagem nova, o `watchfiles` em desenvolvimento), o servidor fecha as conexões com o código `1012` ("reiniciando"), dá um tempo para os turnos em andamento acabarem e conclui a extração de memória. Os clientes reconhecem o `1012` e reconectam quase na hora. `SIGINT` fecha com `1001`. O compose dá 30 s de margem (`stop_grace_period`).

## Iniciar {#iniciar}

```bash
juniper serve        # com deployment.mode: server
```

| Opção | Efeito |
|---|---|
| `deployment.server.host` / `port` | Onde escutar (no contêiner, `0.0.0.0:8765`; quem restringe é a publicação da porta no compose). |
| `deployment.auth.token_file` | Onde fica o token estático dos clientes. Se o arquivo não existe, o servidor gera um token aleatório no primeiro boot e o grava com permissão `600`. Vale enquanto `deployment.auth.legacy_token` for `true`. |
| `deployment.auth.*` (demais) | Usuários e credenciais (`client_id`/`client_secret`), o *pepper* e o espelho do dono: veja [Usuários e credenciais](usuarios.md). O servidor confere a credencial antes do upgrade e, a cada heartbeat, derruba a conexão cuja credencial foi revogada. |
| `dashboard.*` | O painel web, na porta 8766 ([Dashboard](dashboard.md)). |

A referência das opções está em [Configuração](configuracao.md#deployment).

## Testes {#testes}

```bash
pytest tests/test_server.py tests/test_chat_server.py -v
```

Cobrem autenticação, handshake, heartbeat, códigos de fechamento, turnos, cancelamento, retomada, troca de conexão, queda no meio do turno e desligamento.

## Para saber mais {#para-saber-mais}

- [Usuários e credenciais](usuarios.md) (Documentação Técnica): O cadastro de usuários pela CLI e pelo dashboard, o client_id e o client_secret (visto uma vez, guardado só como hash) e a migração do token estático.
- [Ambiente mobile](mobile-dev.md) (Ambiente Local): Como preparar o Android SDK, rodar o app num emulador ou celular com recarga ao vivo, testar, regenerar os tipos do protocolo e gerar um APK.
- [Onde conversar](produto-canais.md) (Produto): Os canais da Juniper (terminal, Telegram, app Android com texto e voz, painel web) e o que cada um faz de melhor.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Agente](agente.md) (Documentação Técnica): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [Login com MFA](login.md) (Documentação Técnica): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [CLI](cli.md) (Documentação Técnica): O comando juniper: chat, mensagem única, retomar conversa, comandos de memória, git e utilitários.
- [Bot do Telegram](telegram.md) (Documentação Técnica): O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
- [App mobile](mobile.md) (Documentação Técnica): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e o roteiro de aceite no aparelho.
- [Executor](executor.md) (Documentação Técnica): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [Dashboard](dashboard.md) (Documentação Técnica): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas e pesquisa, somente leitura, servido pelo próprio cérebro.
- [API WebSocket](protocolo.md) (Referência): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros, códigos de fechamento e reconexão.
- [API HTTP](api-http.md) (Referência): As rotas HTTP do cérebro: a saúde do servidor e a API de leitura do dashboard, com autenticação, paginação, filtros e códigos de resposta.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
