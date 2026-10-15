FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY alembic.ini ./
COPY migrations ./migrations
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && useradd --create-home deadbot
USER deadbot

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "deadbot"]
