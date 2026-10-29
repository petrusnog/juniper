<!-- GERADO por scripts/build_docs.py — não edite; rode `python scripts/build_docs.py` -->
# Mapa da documentação

Índice de entrada: escolha a linha que responde à sua dúvida e abra só essa nota. Busca por palavra-chave: `python scripts/docs_search.py "sua pergunta"`.
Estado atual do projeto (marcos entregues, o que não existe, dívidas): [JUNIPER.md](JUNIPER.md).

## Introdução

- [Introdução](pages/introducao.md): O que é a Juniper, o que ela sabe fazer hoje, como é construída e quais trilhas seguir para aprender mais.

## Ambiente Local

- [Como contribuir](pages/contribuindo.md): O fluxo de trabalho (issue, branch, PR), as regras de arquitetura, os testes exigidos, as convenções de código e como manter a…
- [Primeiros passos](pages/primeiros-passos.md): Tutorial completo do ambiente de desenvolvimento, do clone até a Juniper respondendo no terminal, no Telegram e no Android, com o…
- [Ambiente Docker](pages/dev-docker.md): Referência do ambiente de desenvolvimento em contêineres: serviços, portas, variáveis, o que recarrega sozinho, como acompanhar…
- [Ambiente mobile](pages/mobile-dev.md): Como preparar o Android SDK, rodar o app num emulador ou celular com recarga ao vivo, testar, regenerar os tipos do protocolo e…
- [Instalação sem Docker (modo local)](pages/install.md): O instalador da Juniper: o cérebro dentro do próprio terminal (modo local), sem contêineres, a partir de uma máquina limpa.

## Produto

- [Como a Juniper funciona](pages/produto.md): A Juniper explicada sem código: o cérebro e os corpos, o caminho de uma mensagem e o que acontece quando ela precisa agir.
- [Modelos de linguagem (LLM)](pages/produto-llm.md): O que é um modelo de linguagem, quais a Juniper usa, por que ela tem mais de um e o que isso significa para custo, velocidade e…
- [Ferramentas e permissões](pages/produto-ferramentas.md): Como a Juniper age no mundo real por meio de ferramentas, os quatro níveis de risco e como a permissão é dada na própria conversa.
- [Memória](pages/produto-memoria.md): Como a Juniper lembra: a conversa atual, os fatos duráveis sobre você, o seu perfil, a busca por significado e como ver e apagar…
- [Personalidade, opiniões e sentimentos](pages/produto-personalidade.md): A Memória V2 explicada sem código: opiniões com grau de certeza, sentimentos que passam, interesses próprios, uma personalidade…
- [Onde conversar](pages/produto-canais.md): Os canais da Juniper (terminal, Telegram, app Android com texto e voz, painel web) e o que cada um faz de melhor.
- [Integrações](pages/produto-integracoes.md): O que a Juniper faz com a agenda do Google, a música do YouTube e os repositórios git, e o que cada integração precisa.
- [Privacidade e segurança](pages/produto-privacidade.md): Para onde vão os seus dados, o que fica só no seu servidor, como a Juniper se protege e o que você controla.
- [Glossário](pages/glossario.md): Os termos usados nesta documentação, explicados em uma ou duas frases.

## Documentação Técnica

### Arquitetura

- [Visão geral da arquitetura](pages/architecture.md): As camadas da Juniper, onde cada uma mora no código, as regras que as mantêm simples, os modos de execução e o caminho de uma…
### Clientes

- [Servidor WebSocket](pages/servidor.md): Como o cérebro atende os clientes no modo servidor: autenticação, handshake, conversas ligadas a conexões, turnos, executores…
- [CLI](pages/cli.md): O comando juniper: chat, mensagem única, retomar conversa, comandos de memória, git e utilitários.
- [Voz no terminal](pages/voz.md): Como ela fala: os prefixos, fala fixa versus gerada por IA, e a regra para comandos novos.
- [Bot do Telegram](pages/telegram.md): O bot do Telegram como cliente da Juniper: como funciona, como criar e configurar, o que ele aceita e o que não faz.
- [App mobile](pages/mobile.md): O app Android da Juniper (React Native): conexão, chat de texto, chat de voz contínuo, executor open_url com autoplay de música e…
- [Voz por áudio](pages/voz-audio.md): Falar com a Juniper e ouvi-la: Google Chirp 3 HD pelo cérebro, STT da Groq, trava de orçamento e as mensagens de áudio do…
- [Executor](pages/executor.md): O dispositivo como corpo da Juniper: comandos estruturados, revalidação local e a tool open_url.
- [Dashboard](pages/dashboard.md): O painel web da Juniper: memórias, afetos, estudos, sonhos, conversas, ocorrências de vazamento (com exportação em planilha) e…
### Integrações

- [Agenda Google](pages/google-calendar.md): Ler, marcar, alterar e desmarcar compromissos no Google Calendar, com checagem de conflitos.
- [Música](pages/musica.md): Achar e tocar músicas pelo YouTube Music do celular, criar playlists e aprender o gosto musical, pela YouTube Data API oficial.
- [Comandos git](pages/git.md): search e deploy no terminal e pelo agente (git_search e git_deploy).
### Memória

- [Memória](pages/memoria.md): Visão geral das duas camadas de memória, como elas se encaixam e como validar tudo na sua máquina.
- [Memória de sessão](pages/memoria-sessao.md): A conversa atual, o orçamento de tokens, retomar com --resume e onde as conversas ficam (arquivos ou MongoDB).
- [Memória de longo prazo](pages/memoria-longo-prazo.md): Embeddings locais, VectorStore, extração em segundo plano, recuperação (RAG), perfil do usuário e as tools de memória.
- [Memória V2](pages/memoria-v2.md): A Juniper além dos fatos: crenças com confiança calculada por evidência, sentimentos, interesses e personalidade viva, em…
### Núcleo

- [Agente](pages/agente.md): O loop que decide, chama tools e reavalia: Agent, Dispatcher, Session e a confirmação na conversa.
- [LLM](pages/llm.md): O contrato LLMProvider, os tipos, os erros, os três providers, o streaming e o LLMRouter com failover.
- [Tools](pages/tools.md): Como uma tool é definida, registrada e executada: Tool, ToolResult, Tier, Locality e o registry.
- [Segurança](pages/seguranca.md): Os quatro tiers, a confirmação na conversa, o tratamento de segredos e a regra "conteúdo externo é dado".
- [Observabilidade](pages/observabilidade.md): O log técnico (JSONL, um arquivo por dia), `juniper logs`, a captura das requisições HTTP, a rotação diária e a medição de tempo…
- [Login com MFA](pages/login.md): O login global com segundo fator (TOTP) que vale para todo cliente da Juniper: CLI, Telegram, dashboard e app.
- [Metadocumentação](pages/metadocs.md): Como a Juniper responde sobre a própria arquitetura: detector de perguntas por palavras-chave, busca BM25 local na documentação…
- [Auditoria de vazamentos](pages/vazamentos.md): Como a Juniper registra as respostas em que escapou maquinaria (tags, instruções, trechos do prompt), como marcar um vazamento na…

## Referência

- [API WebSocket](pages/protocolo.md): Referência completa do protocolo entre os clientes e o cérebro: conexão, envelope, cada mensagem com os seus campos, erros…
- [API HTTP](pages/api-http.md): As rotas HTTP do cérebro: a saúde do servidor, o download do app Android e a API de leitura do dashboard, com autenticação…
- [Configuração](pages/configuracao.md): Onde ficam config.yaml e .env, a precedência e a referência de todas as opções.

## Produção

- [Visão geral da produção](pages/producao.md): Como a Juniper roda em produção num servidor qualquer: topologia, requisitos, modelo de segurança, configuração, dados e o…
- [Deploy do cérebro numa VPS](pages/deploy-vps.md): Tutorial completo para levar o cérebro a uma VPS de qualquer provedor: acesso e SSH, sistema, Docker, rede privada, firewall…
- [Integrações Google Cloud e Groq](pages/integracoes-producao.md): Como ligar no servidor a agenda do Google, a música do YouTube e a voz (Google Text-to-Speech e Groq), com as credenciais, a…
- [Clientes em produção](pages/clientes-producao.md): Como conectar ao cérebro em produção o terminal, o bot do Telegram, o app Android (gerar, instalar e configurar o APK) e o…
- [Backup da memória](pages/backup.md): Como a memória da Juniper (o banco MongoDB) é copiada, criptografada e versionada num repositório privado do GitHub, e como…
- [Deploy contínuo](pages/deploy-continuo.md): Cada merge em master vira deploy: imagem no registro de contêineres, entrada temporária do CI na rede privada, SSH, backup, troca…
- [Operação e manutenção](pages/operacao.md): O dia a dia do servidor: comandos dentro do contêiner, logs, saúde, comandos administrativos da memória, atualização manual…
- [Integrar com o cérebro](pages/integrar-cliente.md): Guia prático para escrever um cliente ou integrar outro sistema à Juniper pela API WebSocket, com um exemplo completo em Python…

## Decisões (ADR)

Cada ADR, o status e as páginas que o citam.

- [ADR-001](decisions/ADR-001.md): Idioma, Providers de LLM e Camada HMI (Aprovado) · `architecture`, `contribuindo`, `produto-llm`
- [ADR-002](decisions/ADR-002.md): Schema de Tools (Aprovado) · `architecture`, `contribuindo`, `llm`, `produto-ferramentas`, `tools`
- [ADR-003](decisions/ADR-003.md): Configuração (Aprovado) · `architecture`, `configuracao`, `contribuindo`, `integracoes-producao`, `memoria-sessao`
- [ADR-006](decisions/ADR-006.md): Política de Segurança para Execução de Comandos (Aprovado) · `architecture`, `contribuindo`, `memoria-sessao`, `produto-ferramentas`, `produto-integracoes`, `seguranca`, `tools`
- [ADR-007](decisions/ADR-007.md): Arquitetura de Deployment (Aprovado) · `agente`, `architecture`, `contribuindo`, `produto`, `seguranca`, `tools`
- [ADR-008](decisions/ADR-008.md): Integração com Google Calendar (primeira tool real) (Proposto) · `google-calendar`, `integracoes-producao`, `produto-integracoes`
- [ADR-009](decisions/ADR-009.md): DeepInfra como provider principal e nova ordem do failover (Aceito) · `architecture`, `contribuindo`, `integracoes-producao`, `llm`, `produto-llm`
- [ADR-010](decisions/ADR-010.md): Gerenciamento completo da agenda do usuário (Aceito) · `agente`, `architecture`, `contribuindo`, `google-calendar`, `integracoes-producao`, `integrar-cliente`, `mobile`, `produto-ferramentas`, `produto-integracoes`, `produto`, `protocolo`, `seguranca`, `servidor`, `voz-audio`
- [ADR-011](decisions/ADR-011.md): Arquitetura de Memória (Proposto) · `memoria-longo-prazo`, `memoria-sessao`, `memoria`, `produto-memoria`
- [ADR-012](decisions/ADR-012.md): Protocolo cliente ↔ brain (Proposto) · `architecture`, `contribuindo`, `integrar-cliente`, `observabilidade`, `produto-canais`, `protocolo`, `seguranca`, `servidor`, `vazamentos`
- [ADR-013](decisions/ADR-013.md): Deploy e infraestrutura de produção (Proposto) · `contribuindo`, `deploy-continuo`, `deploy-vps`, `integracoes-producao`, `mobile`, `producao`, `produto-privacidade`, `produto`, `protocolo`, `seguranca`
- [ADR-014](decisions/ADR-014.md): Executor do desktop e a tool `open_url` (Proposto) · `contribuindo`, `executor`, `integrar-cliente`, `produto-ferramentas`, `produto-integracoes`, `protocolo`, `seguranca`, `tools`
- [ADR-015](decisions/ADR-015.md): Cliente desktop (Electron) (Substituído pelo [ADR-022]) · `architecture`, `mobile`
- [ADR-016](decisions/ADR-016.md): Cliente de mensageria: o bot do Telegram (Proposto) · `clientes-producao`, `configuracao`, `produto-canais`, `telegram`
- [ADR-017](decisions/ADR-017.md): Memória V2: do fato à personalidade viva (Aceito) · `agente`, `configuracao`, `memoria-v2`, `memoria`, `musica`, `produto-personalidade`
- [ADR-018](decisions/ADR-018.md): Memória refinada: fato e lembrança (Aceito) · `configuracao`, `memoria-longo-prazo`, `memoria-v2`, `memoria`, `produto-memoria`, `produto-personalidade`
- [ADR-019](decisions/ADR-019.md): Conhecimento próprio: estudo progressivo e notas (Aceito) · `memoria-v2`, `produto-personalidade`
- [ADR-020](decisions/ADR-020.md): Dashboard web da Juniper (somente leitura, na tailnet) (Aceito) · `dashboard`, `produto-canais`
- [ADR-021](decisions/ADR-021.md): Login global com MFA (Proposto) · `architecture`, `login`, `produto-privacidade`, `seguranca`
- [ADR-022](decisions/ADR-022.md): App mobile da Juniper (React Native, Android) (Proposto) · `architecture`, `mobile`, `produto-canais`
- [ADR-023](decisions/ADR-023.md): Voz da Juniper (Google Chirp 3 HD, STT, orçamento e áudio no protocolo) (Proposto) · `integracoes-producao`, `mobile`, `produto-canais`, `protocolo`, `voz-audio`
- [ADR-024](decisions/ADR-024.md): Música (YouTube Data API, playlists e gosto musical) (Proposto) · `executor`, `integracoes-producao`, `musica`, `produto-integracoes`
- [ADR-025](decisions/ADR-025.md): Metadocumentação: a Juniper responde sobre si mesma (Proposto) · `architecture`, `configuracao`, `metadocs`, `produto`
- [ADR-026](decisions/ADR-026.md): Auto-atualização do app Android: o cérebro serve o APK (Proposto) · `api-http`, `clientes-producao`, `configuracao`, `mobile-dev`, `mobile`
