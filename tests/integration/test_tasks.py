from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.models.accounts import ActivationToken
from app.tasks import auth_tasks
from tests.helpers import TEST_DATABASE_URL, create_user


class FakeSMTP:
    sent: list[EmailMessage] = []

    def __init__(self, host: str, port: int) -> None:
        self.address = (host, port)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_send_email_builds_message(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.sent.clear()
    monkeypatch.setattr(auth_tasks.smtplib, "SMTP", FakeSMTP)
    auth_tasks.send_email.run("me@example.com", "Hello", "Body text")
    [message] = FakeSMTP.sent
    assert message["To"] == "me@example.com"
    assert message["From"] == get_settings().smtp_from
    assert message["Subject"] == "Hello"
    assert message.get_content().strip() == "Body text"


async def test_purge_removes_only_expired_activation_tokens(
    db_factory: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    expired_user = await create_user(db_factory, "expired@example.com", active=False)
    fresh_user = await create_user(db_factory, "fresh@example.com", active=False)
    now = datetime.now(UTC)
    async with db_factory() as session:
        session.add_all(
            [
                ActivationToken(
                    user_id=expired_user, token_hash="a" * 64, expires_at=now - timedelta(hours=1)
                ),
                ActivationToken(
                    user_id=fresh_user, token_hash="b" * 64, expires_at=now + timedelta(hours=1)
                ),
            ]
        )
        await session.commit()

    monkeypatch.setattr(
        auth_tasks, "get_settings", lambda: SimpleNamespace(database_url=TEST_DATABASE_URL)
    )
    assert await auth_tasks._purge_expired_activation_tokens() == 1
    async with db_factory() as session:
        remaining = (await session.scalars(select(ActivationToken.user_id))).all()
    assert remaining == [fresh_user]
