FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app

# postgresql-client-16 (pg_dump/pg_restore/psql) для системы бэкапов:
# версия клиента должна строго совпадать с сервером (db: pg16) — pg_dump 17
# пишет SET transaction_timeout, который сервер 16 не понимает.
# Кодовое имя дистрибутива подставляем автоматически (base = debian trixie).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && . /etc/os-release \
    && curl -fsSL -o /usr/share/keyrings/pgdg.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/keyrings/pgdg.asc] http://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 \
    && rm -rf /var/lib/apt/lists/*

COPY . .
# dev-группа: pytest/ruff — полный набор гоняется в живом контейнере
RUN pip install --no-cache-dir -e .[dev]

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
