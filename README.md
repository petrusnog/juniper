<p align="center">
  <img src="docs/html/assets/juniper.png" alt="Juniper" width="200">
</p>

<h1 align="center">🌿 Juniper</h1>

<p align="center">
  <b>Versão 4.0.0</b><br>
  Criada por Petrus Rennan ☕
</p>

---

Juniper é uma assistente autônoma de automação e conversação para o terminal. Você fala com ela em linguagem natural, e ela decide quais ferramentas usar (Git, agenda do Google e outras) para resolver o que você pediu, com failover automático entre provedores de LLM.

## Funcionalidades ativas

- **Chat no terminal:** `juniper` abre a conversa; `juniper <mensagem>` manda uma pergunta isolada, com resposta em streaming.
- **LLM com failover:** DeepInfra (principal), Groq e Ollama (reserva local), na ordem de prioridade configurada.
- **Busca de commits:** `juniper search 4911 6531` encontra commits por ID de task, palavra-chave ou autor.
- **Deploy de feature:** `juniper deploy 4911` aplica os commits da task (cherry-pick) em `feature/<id>-develop` e `feature/<id>-stage`.
- **Agenda do Google:** lista, cria e apaga eventos via conta de serviço do GCP.
- **Tools plugáveis:** `juniper tools` lista tudo o que ela sabe fazer.
- **Instalação automática:** `juniper install` prepara o ambiente, dispensa Python prévio e pede a chave da conta de serviço do GCP.
- **Memória com MongoDB:** as conversas são salvas e `juniper -r` retoma a última. Por padrão ficam em arquivos (`~/.juniper/sessions`, sem instalar nada). O MongoDB é opcional e **exige o Docker instalado e rodando**: o instalador pergunta qual você prefere e, se for o MongoDB, confere o Docker, sobe o banco e cria usuário e senha locais no `.env` sozinho (depois: `juniper install --memory=mongo`). Sem Docker, fique com arquivos. Se o banco estiver fora do ar, ela avisa o que fazer. Detalhes na documentação (T-053).
- **Livro de bolso local:** `juniper docs` serve a documentação em `http://localhost:47831`.

## Documentação completa

📖 **https://petrusnog.github.io/juniper/**
