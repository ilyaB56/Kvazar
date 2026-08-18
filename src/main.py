"""Сборка приложения: FastAPI + модули из plugins.MANIFESTS."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_settings
from src.core.plugins import install_modules

logging.basicConfig(level=logging.INFO)

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Модульная ERP с интеграционной платформой",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    install_modules(app)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/v1/modules")
def modules():
    from src.core.plugins import MANIFESTS

    return [
        {"name": m.name, "version": m.version, "db_schema": m.db_schema,
         "depends_on": list(m.depends_on)}
        for m in MANIFESTS
    ]
