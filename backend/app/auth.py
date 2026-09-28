from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from starlette.requests import Request
from starlette.responses import Response

from .config import settings

COOKIE = "yandu_session"
MAX_AGE = 14 * 24 * 3600
MIN_PASSWORD_LEN = 8
WEAK_PASSWORDS = {"", "change-me", "changeme", "password", "123456", "yandu"}
_HASH_PREFIX = "pbkdf2_sha256$"
_PBKDF2_ROUNDS = 210_000
_LOGIN_WINDOW = 15 * 60
_LOGIN_MAX_TRIES = 8
_failures: dict[str, list[float]] = {}


def _signer() -> TimestampSigner:
    secret_path = settings.data_dir / "session.secret"
    if not secret_path.exists():
        secret_path.write_text(secrets.token_hex(32), encoding="utf-8")
    return TimestampSigner(secret_path.read_text(encoding="utf-8").strip())


def is_weak_password(password: str | None) -> bool:
    raw = (password or "").strip()
    return len(raw) < MIN_PASSWORD_LEN or raw.lower() in WEAK_PASSWORDS


def is_hashed(value: str | None) -> bool:
    return (value or "").startswith(_HASH_PREFIX)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        _PBKDF2_ROUNDS,
    )
    return f"{_HASH_PREFIX}{_PBKDF2_ROUNDS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not is_hashed(stored):
        return hmac.compare_digest(password.encode("utf-8"), stored.encode("utf-8"))
    try:
        _, rounds_s, salt, digest = stored.split("$", 3)
        rounds = int(rounds_s)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            rounds,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), digest)


def _persist_hash(password: str) -> None:
    from . import db

    db.set_setting("access_password_hash", hash_password(password))
    if db.get_setting("access_password", ""):
        db.set_setting("access_password", "")


def bootstrap_password() -> None:
    from . import db

    if is_hashed(db.get_setting("access_password_hash", "")):
        return
    env = (settings.access_password or "").strip()
    if env and not is_weak_password(env):
        _persist_hash(env)
        return
    stored = db.get_setting("access_password", "")
    if stored and not is_weak_password(stored) and not is_hashed(stored):
        _persist_hash(stored)


def needs_setup() -> bool:
    from . import db

    if is_hashed(db.get_setting("access_password_hash", "")):
        return False
    env = (settings.access_password or "").strip()
    if env and not is_weak_password(env):
        return False
    stored = db.get_setting("access_password", "")
    return not (stored and not is_weak_password(stored) and not is_hashed(stored))


def password_ok(password: str) -> bool:
    from . import db

    offered = password or ""
    hashed = db.get_setting("access_password_hash", "")
    if is_hashed(hashed):
        return verify_password(offered, hashed)
    env = (settings.access_password or "").strip()
    if env and not is_weak_password(env):
        if hmac.compare_digest(offered.encode("utf-8"), env.encode("utf-8")):
            _persist_hash(offered)
            return True
        return False
    stored = db.get_setting("access_password", "")
    if stored and not is_weak_password(stored) and not is_hashed(stored):
        if hmac.compare_digest(offered.encode("utf-8"), stored.encode("utf-8")):
            _persist_hash(offered)
            return True
    return False


def save_password(password: str) -> str | None:
    if is_weak_password(password):
        return f"口令至少 {MIN_PASSWORD_LEN} 位，且不能用常见弱口令。"
    _persist_hash(password)
    return None


def complete_setup(
    password: str,
    *,
    llm_api_key: str = "",
    github_username: str = "",
) -> str | None:
    from . import db

    err = save_password(password)
    if err:
        return err
    if llm_api_key.strip():
        db.set_setting("llm_api_key", llm_api_key.strip())
    if github_username.strip():
        db.set_setting("github_username", github_username.strip())
    return None


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def login_blocked(ip: str) -> bool:
    now = time.monotonic()
    rec = [t for t in _failures.get(ip, []) if now - t < _LOGIN_WINDOW]
    _failures[ip] = rec
    return len(rec) >= _LOGIN_MAX_TRIES


def login_fail(ip: str) -> None:
    _failures.setdefault(ip, []).append(time.monotonic())


def login_ok(ip: str) -> None:
    _failures.pop(ip, None)


def make_session() -> str:
    return _signer().sign("ok").decode("utf-8")


def session_ok(token: str | None) -> bool:
    if not token:
        return False
    try:
        _signer().unsign(token, max_age=MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def cookie_secure(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    public = (settings.public_base_url or "").strip().lower()
    return public.startswith("https://")


def attach_session(resp: Response, request: Request) -> None:
    resp.set_cookie(
        COOKIE,
        make_session(),
        httponly=True,
        max_age=MAX_AGE,
        samesite="lax",
        secure=cookie_secure(request),
    )


def request_authed(request: Request) -> bool:
    return session_ok(request.cookies.get(COOKIE))


def open_path(path: str) -> bool:
    if path.startswith("/static") or path in {"/health", "/favicon.ico"}:
        return True
    if needs_setup():
        return path == "/setup"
    return path == "/login"
