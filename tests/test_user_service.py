from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.models.user import User
from app.services.auth import AuthRegister, AuthService, LoginAlreadyExistsError


def make_session(existing_user: User | None = None) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_user
    session.execute.return_value = result
    return session


async def test_registration_hashes_password() -> None:
    session = make_session()
    data = AuthRegister(login="alice", password="a long test password")

    user = await AuthService(session).register(data)

    assert user.login == data.login
    assert user.password_hash != data.password
    assert verify_password(data.password, user.password_hash)
    session.add.assert_called_once_with(user)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(user)


async def test_existing_login_is_rejected() -> None:
    session = make_session(User(login="alice", password_hash="existing"))

    with pytest.raises(LoginAlreadyExistsError):
        await AuthService(session).register(
            AuthRegister(login="alice", password="a long test password")
        )

    session.add.assert_not_called()
    session.commit.assert_not_awaited()


async def test_conflicting_registration_rolls_back() -> None:
    session = make_session()
    session.commit.side_effect = IntegrityError("insert", {}, Exception("conflict"))

    with pytest.raises(LoginAlreadyExistsError):
        await AuthService(session).register(
            AuthRegister(login="alice", password="a long test password")
        )

    session.rollback.assert_awaited_once()
    session.refresh.assert_not_awaited()
