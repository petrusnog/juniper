# Imagem do brain da Juniper (ADR-013, T-080): roda `juniper serve` como usuário sem privilégio.
#
#   docker build -t juniper-brain:local --build-arg GIT_SHA=$(git rev-parse HEAD) .
#
# Nada de segredo entra aqui: o `.env`, o `config.yaml` e a chave do Google são montados em
# tempo de execução (veja docker-compose.prod.yml). O modelo de embeddings (~120 MB) também não
# vai na imagem: ele baixa uma vez para o volume `juniper-models` e é reaproveitado entre deploys.
FROM python:3.12-slim

ARG GIT_SHA=unknown
LABEL org.opencontainers.image.title="juniper-brain" \
      org.opencontainers.image.source="https://github.com/petrusnog/juniper" \
      org.opencontainers.image.revision="${GIT_SHA}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# Usuário sem privilégio, com a pasta de estado (~/.juniper) já no dono certo: os volumes nomeados
# montados nela herdam esse dono na primeira vez.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin juniper \
    && mkdir -p /home/juniper/.juniper/state /home/juniper/.juniper/logs \
        /home/juniper/.juniper/models /home/juniper/.juniper/certs \
    && chown -R juniper:juniper /home/juniper

WORKDIR /app

# Dependências primeiro: a camada só refaz quando o requirements.txt muda.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# O brain roda direto do código (o prompt da persona é lido de ../prompts, relativo ao pacote).
COPY brain ./brain
COPY prompts ./prompts

USER juniper
WORKDIR /home/juniper

EXPOSE 8765

# Mesmo teste do healthcheck do compose, para `docker run` isolado também ficar saudável.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "brain"]
CMD ["serve"]
