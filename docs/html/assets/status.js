/* gerado por scripts/healthcheck.py — não edite */
window.JUNIPER_STATUS = {
  "generated": "2026-10-11T16:21:54",
  "live": true,
  "counts": {
    "ok": 15,
    "fail": 0,
    "skip": 0
  },
  "checks": [
    {
      "group": "Fundação",
      "name": "config.yaml carrega",
      "status": "ok",
      "detail": "/home/pnogueira/.juniper/config.yaml · prioridade: groq → ollama",
      "ms": 149
    },
    {
      "group": "Fundação",
      "name": "persona (prompts/juniper.md)",
      "status": "ok",
      "detail": "2630 caracteres",
      "ms": 0
    },
    {
      "group": "Fundação",
      "name": "testes de config",
      "status": "ok",
      "detail": "8 passed in 0.18s",
      "ms": 523
    },
    {
      "group": "T-011 · Contrato",
      "name": "contrato abstrato",
      "status": "ok",
      "detail": "contrato abstrato e hierarquia de erros ok",
      "ms": 4
    },
    {
      "group": "T-011 · Contrato",
      "name": "testes do contrato",
      "status": "ok",
      "detail": "11 passed in 0.03s",
      "ms": 396
    },
    {
      "group": "T-012 · Ollama",
      "name": "testes (servidor falso)",
      "status": "ok",
      "detail": "17 passed in 0.22s",
      "ms": 638
    },
    {
      "group": "T-013 · Groq",
      "name": "testes (SDK falso)",
      "status": "ok",
      "detail": "39 passed in 0.36s",
      "ms": 793
    },
    {
      "group": "T-013 · Groq",
      "name": "chave configurada",
      "status": "ok",
      "detail": "chave presente (valor não exibido)",
      "ms": 0
    },
    {
      "group": "Ao vivo · ollama",
      "name": "health_check",
      "status": "ok",
      "detail": "respondeu",
      "ms": 29
    },
    {
      "group": "Ao vivo · ollama",
      "name": "chat()",
      "status": "ok",
      "detail": "'Ok' · 21 tokens · llama3.1",
      "ms": 30103
    },
    {
      "group": "Ao vivo · ollama",
      "name": "stream()",
      "status": "ok",
      "detail": "2 chunks · 'Ok'",
      "ms": 509
    },
    {
      "group": "Ao vivo · ollama",
      "name": "processador",
      "status": "ok",
      "detail": "100% CPU",
      "ms": 0
    },
    {
      "group": "Ao vivo · groq",
      "name": "health_check",
      "status": "ok",
      "detail": "respondeu",
      "ms": 361
    },
    {
      "group": "Ao vivo · groq",
      "name": "chat()",
      "status": "ok",
      "detail": "'' · 96 tokens · openai/gpt-oss-120b",
      "ms": 421
    },
    {
      "group": "Ao vivo · groq",
      "name": "stream()",
      "status": "ok",
      "detail": "1 chunks · ''",
      "ms": 301
    }
  ],
  "roadmap": [
    {
      "task": "T-011",
      "name": "Contrato e tipos LLM",
      "file": "brain/llm/base.py",
      "implemented": true
    },
    {
      "task": "T-012",
      "name": "OllamaProvider",
      "file": "brain/llm/ollama_provider.py",
      "implemented": true
    },
    {
      "task": "T-013",
      "name": "GroqProvider",
      "file": "brain/llm/groq_provider.py",
      "implemented": true
    },
    {
      "task": "T-015",
      "name": "Router com failover",
      "file": "brain/llm/router.py",
      "implemented": true
    },
    {
      "task": "T-020",
      "name": "Base de tools",
      "file": "brain/tools/base.py",
      "implemented": true
    },
    {
      "task": "T-021",
      "name": "Registry de tools",
      "file": "brain/tools/registry.py",
      "implemented": true
    },
    {
      "task": "T-022",
      "name": "Tools de agenda (Google Calendar)",
      "file": "brain/tools/google_calendar.py",
      "implemented": true
    },
    {
      "task": "T-024",
      "name": "Tools de web",
      "file": "brain/tools/web.py",
      "implemented": false
    },
    {
      "task": "T-026",
      "name": "Bridge de comandos legados",
      "file": "brain/tools/juniper_cmds.py",
      "implemented": true
    },
    {
      "task": "T-030",
      "name": "Agent loop",
      "file": "brain/core/agent.py",
      "implemented": true
    },
    {
      "task": "T-031",
      "name": "Logging estruturado",
      "file": "brain/eventlog.py",
      "implemented": true
    },
    {
      "task": "T-040",
      "name": "CLI de chat",
      "file": "brain/clients/cli.py",
      "implemented": true
    }
  ]
};
