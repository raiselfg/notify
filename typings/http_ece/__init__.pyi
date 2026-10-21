from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

def decrypt(
    content: bytes,
    *,
    private_key: EllipticCurvePrivateKey,
    auth_secret: bytes,
    version: str,
) -> bytes: ...
