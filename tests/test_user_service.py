from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_session_token, verify_password
from app.models.session import AuthSession
from app.models.user import User
from app.services.auth import (
    AuthRegister,
    AuthService,
    LoginAlreadyExistsError,
    SessionNotExist,
)


def make_session(existing_user: User | None = None) -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_user
    session.execute.return_value = result
    return session


async def test_registration_hashes_password() -> None:
    session = make_session()

    async def flush_registration() -> None:
        user = session.add.call_args.args[0]
        user.id = uuid4()

    session.flush.side_effect = flush_registration
    data = AuthRegister(login="alice", password="a long test password")

    token = await AuthService(session).register(data)
    user = session.add.call_args_list[0].args[0]
    auth_session = session.add.call_args_list[1].args[0]

    assert user.login == data.login
    assert user.password_hash != data.password
    assert verify_password(data.password, user.password_hash)
    assert isinstance(token, str)
    assert isinstance(auth_session, AuthSession)
    assert auth_session.user_id == user.id
    assert auth_session.token_hash == hash_session_token(token)
    session.commit.assert_awaited_once()


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


async def test_session_token_resolves_user() -> None:
    session = make_session()
    token = "valid-session-token"
    user = User(id=uuid4(), login="alice", password_hash="hashed")
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(token),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = auth_session
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = user
    session.execute.side_effect = [session_result, user_result]

    current_user = await AuthService(session).get_current_user(token)

    assert current_user is user
    assert session.execute.await_count == 2


async def test_expired_session_token_is_rejected() -> None:
    session = make_session()
    auth_session = AuthSession(
        user_id=uuid4(),
        token_hash=hash_session_token("expired-session-token"),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = auth_session
    session.execute.return_value = result

    with pytest.raises(SessionNotExist):
        await AuthService(session).get_current_user("expired-session-token")

    session.execute.assert_awaited_once()


async def test_unknown_login_still_checks_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.auth import AuthLogin, InvalidCredentialsError

    verifier = AsyncMock(return_value=True)
    monkeypatch.setattr("app.services.auth.run_in_threadpool", verifier)
    session = make_session()
    with pytest.raises(InvalidCredentialsError):
        await AuthService(session).login(
            AuthLogin(login="unknown", password="test-password")
        )
    verifier.assert_awaited_once()
    session.add.assert_not_called()
    session.commit.assert_not_awaited()
