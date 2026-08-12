# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --gid 10001 api_nyaa \
    && useradd --uid 10001 --gid api_nyaa --no-create-home --shell /usr/sbin/nologin api_nyaa \
    && mkdir -p /data \
    && chown api_nyaa:api_nyaa /data

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements.txt

COPY --chown=api_nyaa:api_nyaa app ./app
COPY --chown=api_nyaa:api_nyaa run_server.py ./run_server.py

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

CMD ["python", "run_server.py"]
