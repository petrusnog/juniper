---
title: Arquitetura
group: Começando
order: 3
description: As camadas da Juniper, as regras que as mantêm simples e o caminho de uma mensagem.
status: guia
tags: camadas cérebro clientes transporte regras jarvis
---

## Visão geral {#visao-geral}

A Juniper segue a lógica de um assistente sempre disponível: **um cérebro** (o agente, que pensa) e **vários corpos** (interfaces e executores: o terminal, o desktop, o celular). Os clientes são finos: capturam a sua mensagem e mostram a resposta. Quem decide é o cérebro.

Analogia: a interface é o balcão de um restaurante, o cérebro é o chef, as tools são os equipamentos da cozinha e a memória é o caderno de receitas e pedidos antigos. O balcão nunca cozinha e o chef nunca atende na rua.

Hoje o cérebro roda **dentro do processo do CLI** (`deployment.mode: local`). A Sprint 3 o leva para uma VPS e conecta clientes remotos (veja [Produção](producao.html)).

## Mapa em camadas {#camadas}

Leia de cima para baixo: o pedido desce pelas camadas até o mundo real, e a resposta sobe de volta.

```html-raw
<div class="legend"><span class="tag g">pronto</span><span class="tag a">proposto (ADRs 012 a 015)</span><span class="tag v">futuro</span></div>
<div class="layers">
  <div class="layer"><div class="lname"><b>5 · Interfaces</b><small>Onde você fala com ela. Finas: só capturam e exibem.</small></div><div class="lblocks">
    <div class="blk done"><b>CLI</b><small>juniper: chat, streaming, confirmação na conversa</small></div>
    <div class="blk next"><b>Desktop (Electron)</b><small>chat por texto + executor (ADR-015)</small></div>
    <div class="blk future"><b>Voz</b><small>STT, TTS e wake word (Sprint 4)</small></div></div></div>
  <div class="lgap">↓ mensagem · resposta em stream ↑</div>
  <div class="layer"><div class="lname"><b>4 · Transporte</b><small>Só existe quando o cérebro roda na nuvem.</small></div><div class="lblocks">
    <div class="blk next"><b>WebSocket único</b><small>chat, sessões, memória e executor (ADR-012)</small></div>
    <div class="blk next"><b>Token + Tailscale</b><small>sem portas públicas (ADR-013)</small></div></div></div>
  <div class="lgap">↓ ↑</div>
  <div class="layer"><div class="lname"><b>3 · Cérebro</b><small>Decide o que fazer: brain/core/.</small></div><div class="lblocks">
    <div class="blk done"><b>Agent</b><small>decide → chama tool → reavalia</small></div>
    <div class="blk done"><b>Dispatcher</b><small>acha a tool, checa o tier, pede confirmação</small></div>
    <div class="blk done"><b>Session</b><small>persona + histórico</small></div>
    <div class="blk done"><b>Consent</b><small>entende o "sim" da conversa</small></div></div></div>
  <div class="lgap">↓ pede · devolve ↑</div>
  <div class="layer"><div class="lname"><b>2 · Capacidades</b><small>O que o cérebro usa para pensar, agir e lembrar.</small></div><div class="lblocks">
    <div class="blk done"><b>LLM Router</b><small>DeepInfra → Groq → Ollama</small></div>
    <div class="blk done"><b>Tools</b><small>agenda, git, memória; registry com auto-descoberta</small></div>
    <div class="blk done"><b>Memória de sessão</b><small>conversa salva e retomável</small></div>
    <div class="blk done"><b>Memória de longo prazo</b><small>fatos duráveis, perfil e busca por significado</small></div>
    <div class="blk next"><b>Tools de executor</b><small>open_url no desktop (ADR-014)</small></div></div></div>
  <div class="lgap">↓ ↑</div>
  <div class="layer"><div class="lname"><b>1 · Mundo externo</b><small>Fora do código da Juniper.</small></div><div class="lblocks">
    <div class="blk done"><b>DeepInfra / Groq</b><small>LLM na nuvem</small></div>
    <div class="blk done"><b>Ollama</b><small>LLM local (reserva)</small></div>
    <div class="blk done"><b>Google Calendar</b><small>sua agenda, por conta de serviço</small></div>
    <div class="blk done"><b>MongoDB</b><small>conversas, memórias e perfil</small></div>
    <div class="blk next"><b>VPS (Hetzner)</b><small>onde o cérebro vai rodar (ADR-013)</small></div></div></div>
  <div class="lgap"></div>
  <div class="layer side-layer"><div class="lname"><b>Transversais</b><small>Atravessam todas as camadas.</small></div><div class="lblocks">
    <div class="blk done"><b>Configuração</b><small>config.yaml + .env, um só ponto de acesso</small></div>
    <div class="blk done"><b>Segurança em 4 tiers</b><small>READ · WRITE · DESTRUCTIVE · FORBIDDEN</small></div>
    <div class="blk done"><b>Logs estruturados</b><small>JSONL com segredos mascarados</small></div></div></div>
</div>
```

## Uma frase sua, passo a passo {#fluxo}

Exemplo: você digita *"amanhã quero uma ida ao dentista, às 16h"*.

| # | O que acontece | Camada |
|---|---|---|
| 1 | A CLI recebe a frase e a entrega ao [Agent](agente.html#agent). | Interface |
| 2 | O agente monta o pedido ao LLM: persona, perfil, memórias relevantes, a hora de agora (é isso que permite entender "amanhã") e a lista de tools. | Cérebro |
| 3 | O [LLMRouter](llm.html#llmrouter) envia à DeepInfra; se ela falhar, tenta a Groq e, por último, o Ollama, com a mesma conversa. | Capacidades |
| 4 | O LLM pede `create_event` com título e horário já resolvidos. | Capacidades |
| 5 | O [Dispatcher](agente.html#dispatcher) acha a tool no registry; o `precheck` consulta o Google Calendar **antes** de te perguntar qualquer coisa. | Cérebro |
| 6 | Se já houver algo às 16h, a Juniper pergunta: "Já existe «Fisioterapia» às 16h. Desmarco para marcar o dentista?" | Cérebro |
| 7 | Sem conflito, ela pergunta em palavras "Posso marcar «Dentista», segunda 16h–17h?" e espera o seu "sim" ([confirmação na conversa](agente.html#confirmacao)). | Cérebro |
| 8 | Você confirma; o evento é criado e aparece no seu celular. A resposta chega palavra por palavra. | Capacidades → Interface |
| 9 | Tudo é registrado no [log](observabilidade.html), a conversa é salva e, em segundo plano, o que vale lembrar vai para a [memória](memoria.html). | Transversal |

## Regras que mantêm tudo simples {#regras}

1. **O cérebro não conhece as interfaces.** `brain/core/` nunca importa de `brain/clients/`. Por isso a mesma Juniper serve terminal, desktop e voz.
2. **Providers são trocáveis.** O cérebro fala com "um LLM" (`LLMProvider`), nunca com DeepInfra, Groq ou Ollama especificamente. O router escolhe.
3. **Tool nova = arquivo novo.** Cada capacidade é um plugin que se registra sozinho ([Tools](tools.html)). Nada no cérebro muda.
4. **Perigo exige permissão.** Ler é livre; alterar pede o seu "sim" na conversa; destruir também, com aviso de irreversibilidade; o que é proibido nunca executa ([Segurança](seguranca.html)).
5. **Um só ponto de configuração.** Nenhum módulo lê variável de ambiente ou YAML diretamente: tudo vem de `brain.config.settings` ([Configuração](configuracao.html)).
6. **Shell é interface, Python é cérebro.** O `shell/` só orquestra e chama; a lógica vive em `brain/`.

## Onde cada camada mora no código {#codigo}

| Camada | Pasta / arquivo | Página |
|---|---|---|
| Interfaces | `brain/clients/cli.py`, `brain/clients/voice.py` | [CLI](cli.html), [Voz](voz.html) |
| Transporte | `brain/clients/server.py` (a implementar) | [Protocolo](protocolo.html) |
| Cérebro | `brain/core/{agent,dispatcher,session,consent,clock}.py` | [Agente](agente.html) |
| LLM | `brain/llm/` | [LLM](llm.html) |
| Tools | `brain/tools/` | [Tools](tools.html) |
| Memória | `brain/memory/` | [Memória](memoria.html) |
| Configuração | `brain/config.py` | [Configuração](configuracao.html) |
| Logs e tempos | `brain/eventlog.py`, `brain/timing.py` | [Observabilidade](observabilidade.html) |
| Comandos legados | `shell/` | [Comandos git](git.html) |
| Persona | `prompts/persona.core.md` | [Agente](agente.html#persona) |

## Roadmap {#roadmap}

| Sprint | Foco | Estado |
|---|---|---|
| 1 | Hello Agent: chat local, LLMs com failover, tools, agenda | pronta |
| 2 | Memória: sessão, longo prazo, perfil, busca por significado | pronta |
| 3 | Produção: cérebro na VPS, protocolo, CLI remoto, desktop com chat, executor `open_url` | em planejamento (ADRs 012 a 015) |
| 4 | Voz: STT/TTS locais no cliente, wake word | futura |
| 5 | Mais executores: PC, celular, casa | futura |
| 6 | Iniciativa: tarefas agendadas, proatividade supervisionada | futura |

> **Nota:** esta página descreve o plano vigente. Quando uma decisão mudar, ela muda junto; o histórico de cada escolha está nas [Decisões (ADRs)](adr.html).
