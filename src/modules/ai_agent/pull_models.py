"""Прогрев моделей Ollama: python -m src.modules.ai_agent.pull_models.

Запускать вручную после первого старта профиля ai
(docker compose --profile ai up -d). Ходит в Ollama через коннектор
(ADR-001: сеть — только в connectors/).
"""

from __future__ import annotations

import sys

from src.config import get_settings
from src.modules.integrations.connectors.llm import OllamaConnector


def main() -> int:
    settings = get_settings()
    # коннектор сам держит сетевой код; для pull используем его base_url
    connector = OllamaConnector(code="ollama", display_name="pull")
    base = connector.config.get("base_url", "http://localhost:11434").replace(
        "http://ollama:", "http://localhost:")
    for model in (settings.ai_chat_model, settings.ai_embed_model):
        print(f"pulling {model} …", flush=True)
        result = connector.pull_model(model, base_url=base)
        if not result.ok:
            print(f"ERROR pulling {model}: {result.error}", file=sys.stderr)
            return 1
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
