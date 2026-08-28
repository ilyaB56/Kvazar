"""Фоновые задачи ai_agent (классификация выписок — этап F)."""

from __future__ import annotations

import logging

from src.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def classify_task(payload: dict) -> dict:
    """Классификация items события в предложения — только в воркере."""
    from src.modules.ai_agent.classify import run_classify

    return run_classify(payload)
