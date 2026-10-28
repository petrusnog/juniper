---
title: Privacidade e segurança
group: Produto
order: 8
description: Para onde vão os seus dados, o que fica só no seu servidor, como a Juniper se protege e o que você controla.
status: guia
tags: produto privacidade segurança dados terceiros servidor rede privada backup criptografia segredos token controle
---

## Onde os seus dados ficam {#onde}

A Juniper é feita para rodar num servidor **seu**. Conversas, memórias, perfil, opiniões e sentimentos ficam no banco de dados desse servidor, e não num serviço da Juniper: não existe uma "nuvem da Juniper".

## O que sai do servidor {#o-que-sai}

Algumas tarefas dependem de serviços externos. Cada um recebe só o necessário:

| Serviço | O que recebe | Quando |
|---|---|---|
| Provedor do modelo de linguagem (DeepInfra, Groq) | A sua mensagem e o contexto da resposta (trechos de memória, perfil, dados que as ferramentas leram). | A cada mensagem. |
| Google Calendar | Os dados dos eventos que ela cria ou altera. | Quando você usa a agenda. |
| YouTube | Os termos de busca e as playlists. | Quando você usa a música. |
| Groq (transcrição) | O áudio da sua fala. | Na conversa por voz. |
| Google (voz) | O texto das respostas que ela fala. | Na conversa por voz. |
| Telegram | As mensagens trocadas com o bot. | Se você usa o Telegram. |

O que **não sai**: a busca por significado na memória roda no próprio servidor, e os registros técnicos ficam só nele. Cada serviço tem a própria política de retenção de dados, que vale conferir antes do uso diário.

## Como o servidor é protegido {#servidor}

- **Nenhuma porta aberta para a internet.** O servidor só é alcançável por uma rede privada entre os seus dispositivos. Para quem está de fora, ele não responde ([ADR-013](adr-013.html)).
- **Token em cada conexão.** Além da rede privada, todo cliente precisa de um token: são duas travas independentes.
- **Segredos fora do código.** Chaves de acesso ficam em arquivos protegidos no servidor, nunca no código, nas imagens ou nos registros. Nos registros, chaves e senhas aparecem mascaradas.
- **Backup criptografado.** A memória pode ser copiada todo dia, criptografada com uma chave que fica só com você: quem obtiver a cópia vê apenas bytes ilegíveis ([Backup da memória](backup.html)).
- **Login com segundo fator** está planejado para todos os canais ([ADR-021](adr-021.html)); ainda não existe.

## Como a Juniper se protege dela mesma {#acoes}

- Ações que alteram algo pedem o seu "sim", e o "sim" é interpretado pelo sistema, não pelo modelo ([Ferramentas e permissões](produto-ferramentas.html#permissao)).
- Conteúdo vindo de fora é tratado como informação, nunca como ordem.
- O celular confere de novo, por conta própria, cada pedido do cérebro antes de executar (por exemplo, nunca abre endereços de redes internas).
- A memória filtra segredos antes de guardar e recusa documentos no perfil.

## O que você controla {#controle}

- **Ver, exportar e apagar** a memória a qualquer momento ([Memória](produto-memoria.html#controle)).
- **Ligar ou desligar** cada recurso: memória de longo prazo, Memória V2, sentimentos, voz, música, Telegram.
- **Auditar** o que a Juniper concluiu e por quê ([Personalidade](produto-personalidade.html#transparencia)).
- **Escolher os provedores** de modelo de linguagem, inclusive um que roda localmente.

## Limites honestos {#limites}

- Os filtros de segredo são heurísticos: um segredo num formato desconhecido pode passar.
- Nenhuma defesa contra *prompt injection* é completa; por isso as ações continuam pedindo confirmação.
- O Telegram não oferece criptografia de ponta a ponta para bots.
- Um servidor único não tem alta disponibilidade: se cair, a Juniper fica fora do ar até ser restaurado (os dados voltam pelo backup).

Os detalhes técnicos estão em [Segurança](seguranca.html) e na trilha [Produção](producao.html).
