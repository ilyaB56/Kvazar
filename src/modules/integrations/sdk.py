"""Connector SDK — единый контракт для всех интеграций.

Чтобы подключить любой внешний сервис (банк, CRM, госсервис, нейросеть),
пишется класс-наследник BaseConnector и регистрируется в connector_types.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Capabilities:
    fetch: bool = False
    push: bool = False
    webhooks: bool = False


@dataclass
class ConnectorResult:
    ok: bool
    data: Any = None
    error: str = ""


@dataclass
class BaseConnector(abc.ABC):
    """Базовый адаптер интеграции.

    config — публичные настройки (base_url, компания и т.п.),
    credentials — расшифрованные секреты (api_key, токены). Секреты нельзя
    логировать и возвращать наружу.
    """

    code: str  # уникальный код типа коннектора, напр. "http_rest"
    display_name: str
    capabilities: Capabilities = field(default_factory=Capabilities)
    config_schema: dict[str, Any] = field(default_factory=dict)

    config: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, Any] = field(default_factory=dict)

    @abc.abstractmethod
    def test_connection(self) -> ConnectorResult:
        """Проверка доступности сервиса и валидности учётных данных."""

    def fetch(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        """Получить данные из внешней системы."""
        raise NotImplementedError(f"{self.code} does not support fetch")

    def push(self, endpoint: str = "", payload: dict | None = None) -> ConnectorResult:
        """Отправить данные во внешнюю систему."""
        raise NotImplementedError(f"{self.code} does not support push")

    def verify_webhook(self, headers: dict[str, str], body: bytes) -> bool:
        """Проверка подписи/токена входящего webhook."""
        raise NotImplementedError(f"{self.code} does not support webhooks")


class ConnectorRegistry:
    """Реестр типов коннекторов (аналог магазина интеграций, backend-часть)."""

    def __init__(self) -> None:
        self._types: dict[str, type[BaseConnector]] = {}
        self._protos: dict[str, BaseConnector] = {}

    def register(self, connector_cls: type[BaseConnector]) -> None:
        # Читаем code/display_name из инстанса-прототипа без state
        proto = connector_cls(
            code=connector_cls.code,  # type: ignore[call-arg]
            display_name=connector_cls.display_name,  # type: ignore[call-arg]
        )
        self._types[proto.code] = connector_cls
        self._protos[proto.code] = proto

    def build(self, code: str, config: dict, credentials: dict) -> BaseConnector:
        cls = self._types.get(code)
        if cls is None:
            msg = f"Unknown connector type: {code}"
            raise KeyError(msg)
        return cls(code=code, display_name=cls.display_name, config=config, credentials=credentials)  # type: ignore[call-arg]

    def available(self) -> list[dict[str, Any]]:
        # Поля с default_factory (capabilities, config_schema) не существуют как
        # атрибуты класса, пока коннектор не задал их явно в теле класса.
        # Берём значение класса, если задано, иначе — дефолт из прототипа.
        result: list[dict[str, Any]] = []
        for cls in self._types.values():
            proto = self._protos[cls.code]
            capabilities = getattr(cls, "capabilities", None) or proto.capabilities
            config_schema = getattr(cls, "config_schema", None) or proto.config_schema
            result.append({
                "code": cls.code,
                "display_name": cls.display_name,
                "capabilities": capabilities.__dict__,
                "config_schema": config_schema,
            })
        return result


registry = ConnectorRegistry()
