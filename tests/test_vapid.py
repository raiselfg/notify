import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from app.core.config import Settings
from app.core.vapid import encode_key, public_key_bytes


@pytest.fixture
def vapid_values() -> dict[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    return {
        "vapid_private_key": encode_key(
            key.private_numbers().private_value.to_bytes(32, "big")
        ),
        "vapid_public_key": encode_key(public_key_bytes(key)),
        "vapid_subject": "mailto:admin@example.com",
    }


def make_settings(values: dict[str, object]) -> Settings:
    return Settings.model_validate(
        {
            "db_host": "localhost",
            "db_port": 5432,
            "db_name": "test",
            "db_user": "test",
            "db_password": "fake-password",
            "auth_session_ttl_days": 30,
            "vapid_private_key": None,
            "vapid_public_key": None,
            "vapid_subject": None,
            **values,
        }
    )


def test_valid_configuration_hides_private_key(
    vapid_values: dict[str, str],
) -> None:
    settings = make_settings(dict(vapid_values))
    settings.require_vapid()
    assert vapid_values["vapid_private_key"] not in repr(settings)


def test_sender_requires_vapid_configuration() -> None:
    settings = make_settings({})
    with pytest.raises(ValueError, match="not configured"):
        settings.require_vapid()


def test_partial_configuration_is_rejected() -> None:
    with pytest.raises(ValidationError, match="All VAPID settings"):
        make_settings({"vapid_public_key": "fake-key"})


def test_mismatched_keys_are_rejected(vapid_values: dict[str, str]) -> None:
    other_key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(ValidationError, match="Invalid VAPID key pair") as error:
        make_settings(
            {
                **vapid_values,
                "vapid_public_key": encode_key(public_key_bytes(other_key)),
            }
        )
    assert vapid_values["vapid_private_key"] not in str(error.value)


@pytest.mark.parametrize("subject", ["http://example.com", "mailto:", "bad-contact"])
def test_invalid_contact_is_rejected(
    vapid_values: dict[str, str], subject: str
) -> None:
    with pytest.raises(ValidationError, match="contact URL"):
        make_settings({**vapid_values, "vapid_subject": subject})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("push_ttl_seconds", 0),
        ("push_ttl_seconds", 3601),
        ("push_timeout_seconds", 0),
        ("push_timeout_seconds", float("inf")),
    ],
)
def test_invalid_delivery_limits_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        make_settings({field: value})


@pytest.mark.parametrize("key", ["not-a-key", "!" * 43, "A" * 43, "A" * 44])
def test_malformed_private_key_is_rejected(
    vapid_values: dict[str, str], key: str
) -> None:
    with pytest.raises(ValidationError, match="Invalid VAPID key pair"):
        make_settings({**vapid_values, "vapid_private_key": key})


def test_https_contact_is_accepted(vapid_values: dict[str, str]) -> None:
    make_settings({**vapid_values, "vapid_subject": "https://example.com/contact"})
