# Image for the API, the Celery worker and the one-shot seed job (build context: repository root).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY packages/querydoctor /srv/packages/querydoctor
COPY apps/api /srv/apps/api
RUN pip install /srv/packages/querydoctor /srv/apps/api

WORKDIR /srv/apps/api
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:factory --factory --host 0.0.0.0 --port 8000"]
