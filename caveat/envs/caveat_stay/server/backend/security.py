"""Security helpers shared by CAVEAT-Stay authentication and database seeding."""

from passlib.context import CryptContext


_PASSWORD_CONTEXT = CryptContext(
    schemes=["pbkdf2_sha256"],
    deprecated="auto",
    pbkdf2_sha256__rounds=600_000,
)


def hash_password(password: str) -> str:
    """Hash a password with a unique salt and a deliberately expensive KDF."""
    return _PASSWORD_CONTEXT.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    """Return whether *password* matches a supported encoded password hash."""
    if not encoded:
        return False
    try:
        return _PASSWORD_CONTEXT.verify(password, encoded)
    except (TypeError, ValueError):
        return False
