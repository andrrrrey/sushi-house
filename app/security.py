import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request


password_hasher = PasswordHasher()
fernet = Fernet(os.environ["SETTINGS_ENCRYPTION_KEY"].encode())


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def encrypt_setting(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()


def decrypt_setting(value: str) -> str:
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("Stored integration setting cannot be decrypted") from exc


def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token", "")
    if not expected or not hmac.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Недействительный CSRF-токен")

