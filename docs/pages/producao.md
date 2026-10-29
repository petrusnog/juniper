---
title: Visão geral da produção
group: Produção
order: 1
description: Como a Juniper roda em produção num servidor qualquer: topologia, requisitos, modelo de segurança, configuração, dados e o roteiro das páginas seguintes.
status: guia
tags: produção deploy vps servidor topologia requisitos rede privada tailscale wireguard docker compose segurança volumes configuração custos riscos
---

Esta trilha ensina a implantar o ecossistema da Juniper no **seu** servidor: o cérebro numa VPS, as integrações com o Google Cloud e a Groq, e os clientes (terminal, Telegram, app Android e dashboard). Nada aqui depende de um provedor específico: qualquer VPS com Linux e Docker serve.

## Topologia {#topologia}

```text
  Seus dispositivos (na rede privada)                 Servidor (VPS)
 ┌──────────────────────────────┐                   ┌──────────────────────────────────────┐
 │ Terminal (juniper, modo      │                   │ docker compose (projeto juniper-prod)│
 │   server)                    │── WebSocket ────► │   brain    :8765 (WS + /health)      │
 │ App Android (texto, voz,     │   ws://:8765      │            :8766 (dashboard)         │
 │   executor)                  │                   │   mongo    (sem porta publicada)     │
 │ Navegador (dashboard)        │── HTTP :8766 ───► │   telegram (opcional, sem porta)     │
 └──────────────────────────────┘                   └──────────────────────────────────────┘
                                                        │ saída HTTPS
           Telegram (nuvem) ◄── long polling ───────────┤
                                                        ├──► DeepInfra / Groq (LLM, STT)
                                                        ├──► Google Calendar, YouTube, Text-to-Speech
           CI (GitHub Actions) ── SSH pela rede privada ┘     (opcional: deploy contínuo)
```

- O **cérebro** (`brain`) e o **MongoDB** rodam em contêineres, pelo `docker-compose.prod.yml`. O bot do **Telegram** é um terceiro contêiner, opcional.
- Os clientes chegam ao servidor por uma **rede privada** cifrada. Para a internet, o servidor não responde em nenhuma porta da Juniper ([ADR-013](../decisions/ADR-013.md)).
- O servidor só faz conexões **de saída**: para os provedores de LLM, para o Google, para o Telegram.

## Requisitos {#requisitos}

| Item | Mínimo | Recomendado |
|---|---|---|
| Servidor | VPS x86_64, 2 vCPU, 4 GB de RAM, 20 GB de disco | 4 vCPU, 8 GB de RAM, SSD |
| Sistema | Linux com Docker e o plugin Compose v2 | Ubuntu LTS |
| Região | Qualquer uma | Perto do provedor de LLM (para DeepInfra e Groq, os EUA): o cérebro chama o modelo várias vezes por turno, e o cliente só uma |
| Rede privada | Uma VPN entre o servidor e os seus dispositivos | Tailscale (usado nos exemplos) ou WireGuard |
| Contas | Uma chave de LLM (DeepInfra ou Groq) | Google Cloud (agenda, música, voz), Groq (voz), Telegram (bot), GitHub (deploy contínuo e backup) |

A pilha usa cerca de 1,5 GB de memória com tudo de pé (o cérebro, o MongoDB e o modelo de embeddings). Para medir uma VPS candidata antes de decidir, use `scripts/bench_server.py` ([Dimensionar](deploy-vps.md#dimensionar)).

## Rede privada {#rede}

O protocolo usa `ws://`, sem TLS, porque a cifra vem da rede: os clientes só alcançam o servidor por uma VPN. O `docker-compose.prod.yml` publica as portas 8765 e 8766 **só no endereço do servidor dentro da VPN** (a variável `TAILSCALE_IP` do `.env`, que aceita o IP de qualquer VPN), nunca em `0.0.0.0`; sem a variável, publica em `127.0.0.1`.

- **Tailscale** (o caminho dos exemplos): cada dispositivo instala o Tailscale e entra na mesma conta; o servidor recebe um IP `100.x.y.z` e um nome. O dashboard aceita nomes `*.ts.net` por padrão.
- **WireGuard ou outra VPN:** funciona igual. Ponha o IP do servidor na VPN em `TAILSCALE_IP`, inclua o nome que os clientes usam em `dashboard.allowed_hosts` (se não for um IP) e libere as portas 8765 e 8766 na interface da VPN.

Um cliente que não consiga entrar na VPN (uma TV, um carro) não alcança o servidor; o bot do Telegram contorna isso porque roda no próprio servidor.

## Segurança {#seguranca}

| Trava | Como |
|---|---|
| Nenhuma porta pública | Portas publicadas só no IP da VPN; MongoDB sem porta; firewall com `default deny incoming`; SSH só pela VPN. |
| Token em cada conexão | O cérebro gera o token dos clientes no primeiro boot; WebSocket e dashboard o exigem. Rede e token são duas travas independentes. |
| Segredos fora da imagem | `.env`, `config.yaml` e `certs/` ficam no servidor e são montados **somente leitura**; a imagem não tem segredo nenhum. |
| Menor privilégio | O contêiner roda como um usuário sem privilégio (uid 10001); o bot do Telegram não recebe o `.env`, não publica porta e descarta capacidades do kernel. |
| Deploy seguro | O deploy nunca apaga volumes, faz backup antes e volta sozinho para a versão anterior se o novo cérebro não ficar saudável ([Deploy contínuo](deploy-continuo.md)). |
| Dados recuperáveis | Backup diário criptografado, fora do servidor ([Backup da memória](backup.md)). |

## Configuração em camadas {#configuracao}

| Arquivo no servidor | Montado como | O que define |
|---|---|---|
| `docker/config.prod.yaml` (versionado) | `~/.juniper/config.yaml` | A infraestrutura: modo `server`, MongoDB, `llm.priority`, fuso, onde fica o gasto da voz. **Sempre vence.** |
| `config.yaml` (seu, fora do Git) | camada base | O que é pessoal: agendas do Google, música, voz, Memória V2. Só completa o que o `config.prod.yaml` não define. Aponte com `JUNIPER_BASE_CONFIG_FILE=./config.yaml` no `.env`. |
| `.env` (fora do Git, `600`) | `~/.juniper/.env` | Segredos e ajustes por variável (`JUNIPER_*`) ([Configuração](configuracao.md#variaveis)). |
| `certs/` (fora do Git, `700`) | `~/.juniper/certs` | As contas de serviço do Google. |

Ajuste o fuso do agente (`agent.timezone`) no `config.prod.yaml` do seu fork, ou por `JUNIPER_AGENT__TIMEZONE` no `.env`.

## Dados {#dados}

Volumes nomeados do projeto `juniper-prod`; reiniciar ou reconstruir o cérebro não perde nada, e o deploy nunca roda `down -v`.

| Volume | Conteúdo |
|---|---|
| `juniper-mongo-data` | Conversas, memórias, perfil e Memória V2. **É o que o backup copia.** |
| `juniper-state` | O token dos clientes. |
| `juniper-logs` | Os logs JSONL (rotação diária, 3 dias). |
| `juniper-models` | O modelo de embeddings (baixa uma vez). |
| `juniper-music` | O login do YouTube, a cota diária da música e o gasto mensal da voz. |

Além dos volumes, o compose monta **somente leitura** uma pasta do host (`JUNIPER_APP_DIR`, padrão `./app-release`) em `/home/juniper/.juniper/app`: o APK do app Android e o `latest.json`, que o pipeline de publicação grava ([Clientes em produção](clientes-producao.md#app-atualizar)). Não é dado do cérebro e não entra no backup; o deploy não a apaga.

## O roteiro {#roteiro}

| # | Página | Resultado |
|---|---|---|
| 1 | [Deploy do cérebro numa VPS](deploy-vps.md) | Servidor endurecido, na rede privada, com o cérebro saudável e nenhuma porta pública. |
| 2 | [Integrações Google Cloud e Groq](integracoes-producao.md) | Agenda, música e voz ligadas. |
| 3 | [Clientes em produção](clientes-producao.md) | Terminal, Telegram, app Android e dashboard conectados. |
| 4 | [Backup da memória](backup.md) | Cópia diária criptografada fora do servidor. |
| 5 | [Deploy contínuo](deploy-continuo.md) | Cada merge em `master` vira deploy, com rollback. Opcional. |
| 6 | [Operação e manutenção](operacao.md) | O dia a dia: logs, comandos administrativos, atualização, recuperação. |

Para escrever um cliente próprio ou integrar outro sistema ao cérebro, veja [Integrar com o cérebro](integrar-cliente.md).

## Riscos aceitos {#riscos}

- **Servidor único**, sem alta disponibilidade: se cair, a Juniper fica fora até ser recriada pelo [roteiro](deploy-vps.md) e restaurada pelo [backup](backup.md#restaurar).
- **Dependências externas** (a VPN, os provedores de LLM, o GitHub para o CI), mitigadas pelo failover de LLM e pelo deploy manual.
- **Dados pessoais num servidor:** mitigados por nenhuma porta pública, token, usuário sem privilégio, permissões restritas e backup criptografado. O disco do servidor em si não é criptografado pela Juniper.
- **Deploy automático sem revisão humana no último passo:** mitigado por testes obrigatórios, *healthcheck*, rollback e backup pré-deploy.

## Para saber mais {#para-saber-mais}

- [Introdução](introducao.md) (Introdução): O que é a Juniper, o que ela sabe fazer hoje, como é construída e quais trilhas seguir para aprender mais.
- [Ambiente Docker](dev-docker.md) (Ambiente Local): Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar logs e banco, e os problemas comuns.
- [Privacidade e segurança](produto-privacidade.md) (Produto): Para onde vão os seus dados, o que fica só no seu servidor, como a Juniper se protege e o que você controla.
- [Visão geral da arquitetura](architecture.md) (Documentação Técnica): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma mensagem.
- [Segurança](seguranca.md) (Documentação Técnica): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Configuração](configuracao.md) (Referência): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.
- [Deploy do cérebro numa VPS](deploy-vps.md) (Produção): Tutorial completo para levar o cérebro a uma VPS de qualquer provedor: acesso e SSH, sistema, Docker, rede privada, firewall, segredos, subida, verificação e varredura de portas.
- [Integrações Google Cloud e Groq](integracoes-producao.md) (Produção): Como ligar no servidor a agenda do Google, a música do YouTube e a voz (Google Text-to-Speech e Groq), com as credenciais, a configuração e a verificação de cada uma.
- [Clientes em produção](clientes-producao.md) (Produção): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o dashboard.
- [Backup da memória](backup.md) (Produção): Como a memória da Juniper (o banco MongoDB) é copiada, criptografada e versionada num repositório privado do GitHub, e como restaurar.
- [Deploy contínuo](deploy-continuo.md) (Produção): Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca de versão, healthcheck e rollback automático.
- [Operação e manutenção](operacao.md) (Produção): O dia a dia do servidor: comandos dentro do contêiner, logs, saúde, comandos administrativos da memória, atualização manual, troca de segredos e como recriar tudo do zero.
- [Integrar com o cérebro](integrar-cliente.md) (Produção): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python, confirmação, reconexão, executor e a API HTTP.
