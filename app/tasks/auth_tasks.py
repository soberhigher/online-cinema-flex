import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import cast

from celery import shared_task
from sqlalchemy import delete
from sqlalchemy.engine import CursorResult

from app.core.config import get_settings
from app.models.accounts import ActivationToken


@shared_task
def send_email(to_address: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.send_message(message)


@shared_task
def purge_expired_activation_tokens() -> int:
    """Remove expired activation tokens; users can request a fresh token."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    sync_url = settings.database_url_sync
    engine = create_engine(sync_url, pool_pre_ping=True)
    session_factory = sessionmaker(engine)
    with session_factory() as session:
        result = cast(
            CursorResult[tuple[object, ...]],
            session.execute(
                delete(ActivationToken).where(ActivationToken.expires_at < datetime.now(UTC))
            ),
        )
        session.commit()
        deleted = result.rowcount or 0
    engine.dispose()
    return deleted
