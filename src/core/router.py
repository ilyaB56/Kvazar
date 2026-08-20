"""API ядра: auth, пользователи, компании, контакты, настройки, события."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.core import events
from src.core.auth import (
    CurrentUser,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    require_role,
    verify_password,
)
from src.core.models import AuditEvent, Company, Contact, EventOutbox, Setting, User
from src.db import get_db

router = APIRouter(tags=["core"])


# ---------- Schemas ----------

class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    full_name: str = ""
    role: str = "user"


class CompanyIn(BaseModel):
    name: str
    inn: str = ""


class ContactIn(BaseModel):
    company_id: uuid.UUID | None = None
    full_name: str
    email: str = ""
    phone: str = ""


class SettingIn(BaseModel):
    key: str
    value: str | int | bool | dict
    value_type: str = "string"


# ---------- Auth ----------

@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Bad credentials")
    db.add(
        AuditEvent(user_id=user.id, action="login", entity_type="user", entity_id=str(user.id))
    )
    db.commit()
    return TokenOut(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(body: RefreshIn, db: Session = Depends(get_db)):
    from jwt import PyJWTError

    try:
        payload = decode_token(body.refresh_token)
    except PyJWTError as exc:
        raise HTTPException(401, "Invalid refresh token") from exc
    if payload.get("type") != "refresh":
        raise HTTPException(401, "Wrong token type")
    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise HTTPException(401, "User not found")
    return TokenOut(
        access_token=create_access_token(user.id, user.role),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


# ---------- Users (admin) ----------

@router.get("/users", response_model=list[UserOut], dependencies=[Depends(require_role("admin"))])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(User)).all()


@router.post("/users", response_model=UserOut, status_code=201,
             dependencies=[Depends(require_role("admin"))])
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "Email already exists")
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
    )
    db.add(user)
    db.add(AuditEvent(action="user.created", entity_type="user", payload={"email": body.email}))
    db.commit()
    db.refresh(user)
    return user


# ---------- Companies / Contacts ----------

@router.get("/companies", response_model=list[CompanyIn])
def list_companies(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Company)).all()


@router.post("/companies", status_code=201)
def create_company(body: CompanyIn, user: CurrentUser, db: Session = Depends(get_db)):
    company = Company(name=body.name, inn=body.inn)
    db.add(company)
    events.publish(db, "company.created", {"name": body.name, "inn": body.inn})
    db.commit()
    db.refresh(company)
    return company


@router.get("/contacts")
def list_contacts(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Contact)).all()


@router.post("/contacts", status_code=201)
def create_contact(body: ContactIn, user: CurrentUser, db: Session = Depends(get_db)):
    contact = Contact(**body.model_dump())
    db.add(contact)
    events.publish(db, "contact.created", body.model_dump(mode="json"))
    db.commit()
    db.refresh(contact)
    return contact


# ---------- Settings ----------

@router.get("/settings")
def list_settings(user: CurrentUser, db: Session = Depends(get_db)):
    return db.scalars(select(Setting)).all()


@router.put("/settings", dependencies=[Depends(require_role("admin"))])
def upsert_setting(body: SettingIn, db: Session = Depends(get_db)):
    setting = db.scalar(select(Setting).where(Setting.key == body.key))
    if setting:
        setting.value = body.value
        setting.value_type = body.value_type
    else:
        db.add(Setting(key=body.key, value=body.value, value_type=body.value_type))
    db.commit()
    return {"ok": True}


# ---------- Outbox (админ) ----------

@router.get("/events/outbox", dependencies=[Depends(require_role("admin"))])
def list_outbox(event_name: str = "", limit: int = 50, db: Session = Depends(get_db)):
    """Последние события шины из outbox (для отладки и smoke-проверок)."""
    query = select(EventOutbox).order_by(EventOutbox.id.desc()).limit(min(max(limit, 1), 500))
    if event_name:
        query = query.where(EventOutbox.event_name == event_name)
    return [
        {
            "id": row.id,
            "event_name": row.event_name,
            "payload": row.payload,
            "processed": row.processed,
            "created_at": row.created_at.isoformat(),
        }
        for row in db.scalars(query).all()
    ]
