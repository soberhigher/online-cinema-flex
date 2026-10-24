import asyncio
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import cast

from celery import shared_task
from sqlalchemy import delete
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.accounts import ActivationToken


@shared_task(
    autoretry_for=(OSError, smtplib.SMTPException),
    retry_backoff=30,
    retry_kwargs={"max_retries": 5},
)
def send_email(to_address: str, subject: str, body: str) -> None:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
        smtp.send_message(message)


async def _purge_expired_activation_tokens() -> int:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = cast(
                CursorResult[tuple[object, ...]],
                await session.execute(
                    delete(ActivationToken).where(ActivationToken.expires_at < datetime.now(UTC))
                ),
            )
            await session.commit()
            return result.rowcount or 0
    finally:
        await engine.dispose()


@shared_task
def purge_expired_activation_tokens() -> int:
    """Remove expired activation tokens; users can request a fresh token."""
    return asyncio.run(_purge_expired_activation_tokens())
