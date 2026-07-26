"""User auth against SQLite: password hash, session tokens, seed admin."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import User, UserSession
from config import (
    AUTH_SESSION_DAYS,
    DEFAULT_ADMIN_DISPLAY_NAME,
    DEFAULT_ADMIN_PASSWORD,
    DEFAULT_ADMIN_USERNAME,
)

_PBKDF2_ROUNDS = 120_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    """Return storage string: pbkdf2_sha256$rounds$salt$hexdigest."""
    if not password:
        raise ValueError("password required")
    salt_hex = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt_hex.encode("utf-8"),
        _PBKDF2_ROUNDS,
    )
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt_hex}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds_s, salt_hex, digest_hex = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt_hex.encode("utf-8"),
            rounds,
        )
        return hmac.compare_digest(dk.hex(), digest_hex)
    except Exception:  # noqa: BLE001
        return False


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username.strip()).first()


def ensure_default_admin(db: Session) -> None:
    """Create default admin if users table is empty."""
    n = db.query(User).count()
    if n > 0:
        return
    u = User(
        username=DEFAULT_ADMIN_USERNAME,
        password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
        display_name=DEFAULT_ADMIN_DISPLAY_NAME,
        role="admin",
        is_active=True,
    )
    db.add(u)
    db.commit()


def login(
    db: Session,
    username: str,
    password: str,
) -> tuple[User, str, datetime]:
    user = get_user_by_username(db, username)
    if not user or not user.is_active:
        raise ValueError("用户名或密码错误")
    if not verify_password(password, user.password_hash):
        raise ValueError("用户名或密码错误")
    token = secrets.token_urlsafe(32)
    expires = _utcnow() + timedelta(days=AUTH_SESSION_DAYS)
    sess = UserSession(
        user_id=user.id,
        token=token,
        expires_at=expires.replace(tzinfo=None),  # sqlite naive utc
        last_seen_at=_utcnow().replace(tzinfo=None),
    )
    db.add(sess)
    db.commit()
    return user, token, expires


def logout(db: Session, token: str | None) -> None:
    if not token:
        return
    db.query(UserSession).filter(UserSession.token == token).delete()
    db.commit()


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.query(UserSession).filter(UserSession.token == token).first()
    if not sess:
        return None
    exp = sess.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < _utcnow():
        db.delete(sess)
        db.commit()
        return None
    user = db.get(User, sess.user_id)
    if not user or not user.is_active:
        return None
    sess.last_seen_at = _utcnow().replace(tzinfo=None)
    db.commit()
    return user


def change_password(
    db: Session,
    user: User,
    old_password: str,
    new_password: str,
) -> None:
    if not verify_password(old_password, user.password_hash):
        raise ValueError("原密码不正确")
    if len(new_password) < 6:
        raise ValueError("新密码至少 6 位")
    user.password_hash = hash_password(new_password)
    user.updated_at = _utcnow().replace(tzinfo=None)
    db.commit()


def user_public(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name or user.username,
        "role": user.role,
    }
