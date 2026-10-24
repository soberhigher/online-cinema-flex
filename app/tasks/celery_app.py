from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "online_cinema",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.auth_tasks"],
)
celery_app.conf.beat_schedule = {
    "purge-expired-activation-tokens-daily": {
        "task": "app.tasks.auth_tasks.purge_expired_activation_tokens",
        "schedule": 60 * 60 * 24,
    }
}
celery_app.conf.timezone = "UTC"
