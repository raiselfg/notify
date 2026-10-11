from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=64)


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
