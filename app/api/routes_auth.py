"""Auth routes: login / logout / me / change-password."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.services import auth_service
from config import AUTH_SESSION_DAYS

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str = Field(min_length=6)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    user: UserOut


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.cookies.get("session_token")


@router.post("/login", response_model=LoginOut)
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)) -> LoginOut:
    try:
        user, token, expires = auth_service.login(db, body.username, body.password)
    except ValueError as e:
        raise HTTPException(401, str(e)) from e
    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=AUTH_SESSION_DAYS * 24 * 3600,
    )
    pub = auth_service.user_public(user)
    return LoginOut(
        token=token,
        expires_at=expires,
        user=UserOut(**pub),
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _ = user
    auth_service.logout(db, _token_from_request(request))
    response.delete_cookie("session_token")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut(**auth_service.user_public(user))


@router.post("/change-password")
def change_password(
    body: ChangePasswordBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        auth_service.change_password(db, user, body.old_password, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "message": "密码已修改"}
