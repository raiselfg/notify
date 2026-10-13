from hashlib import sha256
from secrets import token_urlsafe

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash(token_urlsafe(32))


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(
    password: str,
    password_hash: str,
) -> bool:
    return password_hasher.verify(
        password,
        password_hash,
    )


def generate_session_token() -> str:
    return token_urlsafe(32)


def hash_session_token(token: str) -> str:
    bytes_token = token.encode("utf-8")
    hashed = sha256(bytes_token).hexdigest()

    return hashed
