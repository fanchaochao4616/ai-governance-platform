"""FastAPI dependencies (auth)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services import auth_service


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    # also allow cookie for convenience
    return request.cookies.get("session_token")


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = _extract_token(request)
    user = auth_service.resolve_session(db, token)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    token = _extract_token(request)
    return auth_service.resolve_session(db, token)
