from celery import Celery

from app.config import get_settings
from app.logging_utils import install_redaction

install_redaction()  # worker logs never carry query literals or parameter values

settings = get_settings()

celery_app = Celery("querydoctor", broker=settings.redis_url, backend=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_soft_time_limit=240,  # a pathological plan must not hold a worker slot forever
    task_time_limit=300,
)
