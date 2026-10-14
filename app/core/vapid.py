import base64
import binascii

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def encode_key(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_key(value: str, length: int) -> bytes:
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except ValueError, binascii.Error:
        raise ValueError("Invalid VAPID key encoding") from None
    if len(decoded) != length or encode_key(decoded) != value:
        raise ValueError("Invalid VAPID key format")
    return decoded


def public_key_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def validate_key_pair(private_key: str, public_key: str) -> None:
    try:
        private = decode_key(private_key, 32)
        public = decode_key(public_key, 65)
        key = ec.derive_private_key(int.from_bytes(private, "big"), ec.SECP256R1())
        if public_key_bytes(key) != public:
            raise ValueError("VAPID keys do not match")
    except ValueError:
        raise ValueError("Invalid VAPID key pair") from None
