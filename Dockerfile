FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORTFOLIO_DATA_DIR=/data \
    PORTFOLIO_DESKTOP_NOTIFICATIONS=0

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# SQLite lives on a mounted volume so holdings survive redeploys.
VOLUME ["/data"]
EXPOSE 8080

# 0.0.0.0 is required inside a container; serve refuses to start without an API token.
CMD ["sh", "-c", "stockcapy serve --host 0.0.0.0 --port ${PORT:-8080}"]
