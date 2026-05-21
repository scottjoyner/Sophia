FROM python:3.11-slim

ARG INSTALL_VOICE_INSIGHT_DEPS=0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
COPY scripts ./scripts

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e . \
    && if [ "$INSTALL_VOICE_INSIGHT_DEPS" = "1" ]; then python -m pip install --no-cache-dir -e ".[insight]"; fi

VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/healthz || exit 1

CMD ["voice-agent", "serve", "--host", "0.0.0.0", "--port", "8765", "--config", "configs/container.yaml"]
