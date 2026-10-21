from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, Field, field_validator

from app.core.vapid import decode_key


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=64)

    @field_validator("p256dh")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(
                ec.SECP256R1(), decode_key(value, 65)
            )
        except ValueError:
            raise ValueError("Invalid subscription public key") from None
        return value

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, value: str) -> str:
        decode_key(value, 16)
        return value


class PushSubscriptionEndpoint(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("Endpoint must be an HTTPS URL")
        return endpoint


class PushSubscriptionInput(PushSubscriptionEndpoint):
    keys: PushSubscriptionKeys
