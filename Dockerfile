# Imagem do brain da Juniper (ADR-013, T-080). Dois alvos sobre a mesma base:
#
#   prod (padrão)  o código vai DENTRO da imagem; roda `juniper serve` sem privilégio.
#                  docker build -t juniper-brain:local --build-arg GIT_SHA=$(git rev-parse HEAD) .
#   dev            o código NÃO vai na imagem: vem por volume (docker-compose.dev.yml) e o
#                  servidor reinicia sozinho quando um arquivo muda.
#                  docker build --target dev -t juniper-brain:dev --build-arg UID=$(id -u) .
#
# Nada de segredo entra aqui: o `.env`, o `config.yaml` e a chave do Google são montados em
# tempo de execução (veja os compose). O modelo de embeddings (~120 MB) também não vai na
# imagem: ele baixa uma vez para o volume `juniper-models` e é reaproveitado entre deploys.

# --- base: Python e dependências (a camada só refaz quando o requirements.txt muda) ------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt

# --- dev: o código chega por volume e o servidor reinicia quando ele muda -------------------------
FROM base AS dev

# O mesmo uid do seu usuário, para a pasta de estado montada do host (.juniper-dev/) ficar sua.
ARG UID=1000
RUN pip install "watchfiles>=0.21" \
    && useradd --create-home --uid ${UID} --shell /usr/sbin/nologin juniper \
    && mkdir -p /home/juniper/.juniper/state /home/juniper/.juniper/logs \
        /home/juniper/.juniper/models /home/juniper/.juniper/certs /home/juniper/.juniper/music \
    && chown -R juniper:juniper /home/juniper

USER juniper
WORKDIR /home/juniper
EXPOSE 8765

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).status == 200 else 1)"]

# O watchfiles pede a parada com SIGINT: o servidor fecha as conexões e os clientes reconectam
# sozinhos (ADR-012, seção 10).
ENTRYPOINT ["watchfiles", "--grace-period", "1", "python -m brain serve", "/app/brain", "/app/prompts"]

# --- prod: o código vai na imagem; sem privilégio, sem segredos ------------------------------------
FROM base AS prod

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="juniper-brain" \
      org.opencontainers.image.source="https://github.com/petrusnog/juniper" \
      org.opencontainers.image.revision="${GIT_SHA}"

# Usuário sem privilégio, com a pasta de estado (~/.juniper) já no dono certo: os volumes nomeados
# montados nela herdam esse dono na primeira vez.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin juniper \
    && mkdir -p /home/juniper/.juniper/state /home/juniper/.juniper/logs \
        /home/juniper/.juniper/models /home/juniper/.juniper/certs /home/juniper/.juniper/music \
    && chown -R juniper:juniper /home/juniper

# O brain roda direto do código (o prompt da persona é lido de ../prompts, relativo ao pacote).
COPY brain ./brain
COPY prompts ./prompts
# As fontes da documentação, para a Juniper responder sobre si mesma (ADR-025). Sem segredos.
COPY docs/pages ./docs/pages
COPY docs/decisions ./docs/decisions

USER juniper
WORKDIR /home/juniper

EXPOSE 8765

# Mesmo teste do healthcheck do compose, para `docker run` isolado também ficar saudável.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "brain"]
CMD ["serve"]
