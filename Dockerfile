# Ratchet gateway (no GPU needed). The student runs on a Nebius Serverless Endpoint; see deploy/.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY ratchet ./ratchet
COPY sim ./sim
COPY evidence/scout ./evidence/scout
RUN pip install --no-cache-dir ".[sign]"

ENV RATCHET_MODE=mock \
    RATCHET_DB=/data/ratchet.db \
    RATCHET_SCOUT_RECORDINGS_DIR=/app/evidence/scout
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "ratchet.gateway:app", "--host", "0.0.0.0", "--port", "8000"]
