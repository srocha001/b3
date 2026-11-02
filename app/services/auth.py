import os
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Request
from jose import JWTError, jwt

from app.database import SessionLocal
from app.models import User

ALGORITHM = "HS256"
COOKIE_NAME = "b3_session"
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "")
    if len(key) < 32 or key == "replace-with-a-long-random-secret":
        raise RuntimeError("Configure uma SECRET_KEY segura no arquivo .env.")
    return key


def validate_secret_key():
    _secret_key()


def hash_password(password: str) -> str:
    if len(password.encode()) > 72:
        raise ValueError("A senha deve ter no máximo 72 bytes UTF-8.")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_session_token(user: User) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=12)
    return jwt.encode(
        {"sub": str(user.id), "exp": expires}, _secret_key(), algorithm=ALGORITHM
    )


def current_user(request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        user_id = int(jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])["sub"])
    except (JWTError, ValueError, KeyError, RuntimeError):
        return None
    with SessionLocal() as db:
        return db.get(User, user_id)


def login_allowed(ip: str) -> bool:
    now = time.monotonic()
    attempts = _attempts[ip]
    window = int(os.getenv("LOGIN_WINDOW_SECONDS", "900"))
    while attempts and attempts[0] < now - window:
        attempts.popleft()
    return len(attempts) < int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))


def record_failed_login(ip: str):
    _attempts[ip].append(time.monotonic())


def clear_login_attempts(ip: str):
    _attempts.pop(ip, None)
