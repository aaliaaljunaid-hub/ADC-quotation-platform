FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary isn't in requirements.txt (the base app is SQLite-first for
# zero-setup local dev) — a container deployment is presumed to run against
# Postgres, so it's installed here instead.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt psycopg2-binary

COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini .

ENV PYTHONUNBUFFERED=1 \
    AUTO_CREATE_TABLES=false
EXPOSE 8000

# Migrations run on every start rather than as a separate release step —
# simplest thing that works for a single-service deployment; a multi-instance
# rollout should instead run `alembic upgrade head` once before scaling up.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000
