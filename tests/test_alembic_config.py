from io import StringIO

import pytest
from alembic.config import Config
from sqlalchemy.engine import make_url

from alembic import command
from app.core.config import Settings


def test_offline_migrations_accept_password_with_special_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        db_host="localhost",
        db_port=5432,
        db_name="test_database",
        db_user="test_user",
        db_password="fictional@password%value",
        auth_session_ttl_days=1,
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    output = StringIO()
    config = Config("alembic.ini", output_buffer=output)

    command.upgrade(config, "head", sql=True)

    assert (
        make_url(config.get_main_option("sqlalchemy.url")).password
        == settings.db_password
    )
    assert "CREATE TABLE users" in output.getvalue()
    assert "CREATE TABLE tasks" in output.getvalue()
