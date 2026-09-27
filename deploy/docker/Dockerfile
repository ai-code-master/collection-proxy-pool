FROM python:3.12-slim

ARG APP_VERSION=0.2.0
LABEL org.opencontainers.image.title="ProxyPool Core" \
      org.opencontainers.image.version="$APP_VERSION" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY . /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROXY_POOL_DATA=/data \
    PROXY_POOL_CONFIG=/app/examples/config.json \
    PROXY_POOL_SOURCES=/app/examples/sources.json

RUN groupadd --gid 10001 proxy-pool \
  && useradd --uid 10001 --gid proxy-pool --no-create-home proxy-pool \
  && mkdir -p /data \
  && chown proxy-pool:proxy-pool /data
VOLUME ["/data"]
EXPOSE 21992
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:21992/healthz', timeout=3)"

USER 10001:10001
CMD ["python", "scripts/pool.py", "serve"]
