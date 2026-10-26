# C27 hosted demo: one container with the CounterSign app and dashboard, the
# bundled PayPal mock on loopback and a disposable PostgreSQL 16. Demo mode only.
# It needs no secrets: the database password is random per boot and the mock
# uses fake credentials. Never pass PayPal sandbox or model keys to it.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# PostgreSQL 16 from the PostgreSQL apt repository (migrations require major 16).
# No default cluster is created; the start script builds a fresh one per boot.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && install -d /usr/share/postgresql-common/pgdg /etc/postgresql-common/createcluster.d \
 && echo "create_main_cluster = false" > /etc/postgresql-common/createcluster.d/no-main.conf \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16 \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# PostgreSQL refuses to run as root. UID 1000 also suits hosts that require it.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

COPY . /app
# Editable install keeps countersign importable from /app/src, so the dashboard
# files in /app/web are found next to it. Windows checkouts may carry CRLF.
# Bytecode is compiled here because a 0.1 CPU host would otherwise compile on every boot.
RUN pip install -e ".[dev]" \
 && sed -i 's/\r$//' scripts/hosted_start.sh \
 && python -m compileall -q src scripts mock_paypal migrations

ENV PYTHONPATH=/app \
    COUNTERSIGN_MODE=demo \
    PORT=8080

USER app
# A migrated, empty template cluster; each boot copies it and sets a new password.
RUN bash scripts/hosted_start.sh --build-template
EXPOSE 8080
ENTRYPOINT ["tini", "--", "bash", "/app/scripts/hosted_start.sh"]
