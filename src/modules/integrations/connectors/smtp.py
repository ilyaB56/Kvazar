"""SMTP-коннектор (sales-automation §5.3, этап C).

Push-only: {to, subject, text} через smtplib (self-hosted, дух ADR-001).
host/port/TLS в config, пароль в credentials (Fernet). Сетевой импорт
smtplib разрешён исключительно здесь (banned-api, pyproject).
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from src.modules.integrations.connectors.egress import check_egress, log_egress
from src.modules.integrations.sdk import (
    BaseConnector,
    Capabilities,
    ConnectorResult,
    registry,
)


class SmtpConnector(BaseConnector):
    code = "smtp"
    display_name = "Email (SMTP)"
    capabilities = Capabilities(push=True)
    config_schema = {
        "host": {"type": "string", "required": True},
        "port": {"type": "int", "default": 587},
        "use_tls": {"type": "bool", "default": True},
        "from_email": {"type": "string", "required": True},
        "timeout_seconds": {"type": "int", "default": 15},
    }

    def test_connection(self) -> ConnectorResult:
        host = str(self.config.get("host", ""))
        port = int(self.config.get("port", 587))
        try:
            check_egress(f"smtp://{host}")
            with smtplib.SMTP(host, port, timeout=self.config.get("timeout_seconds", 15)) as client:
                client.noop()
            log_egress(connector=self.code, url=f"smtp://{host}:{port}", status="ok")
            return ConnectorResult(ok=True)
        except Exception as exc:  # noqa: BLE001 — любая ошибка сети/логина
            log_egress(connector=self.code, url=f"smtp://{host}:{port}",
                       status="error", error=str(exc))
            return ConnectorResult(ok=False, error=str(exc))

    def push(self, endpoint: str = "", params: dict | None = None) -> ConnectorResult:
        """Отправка письма: params = {to, subject, text}."""
        params = params or {}
        to = str(params.get("to", "")).strip()
        subject = str(params.get("subject", ""))
        text = str(params.get("text", ""))
        if not to or "@" not in to:
            return ConnectorResult(ok=False, error=f"invalid recipient: {to!r}")
        host = str(self.config.get("host", ""))
        port = int(self.config.get("port", 587))
        use_tls = bool(self.config.get("use_tls", True))
        username = str(self.credentials.get("username", ""))
        password = str(self.credentials.get("password", ""))
        from_email = str(self.config.get("from_email", username or "erp@localhost"))

        message = EmailMessage()
        message["From"] = from_email
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        try:
            check_egress(f"smtp://{host}")
            with smtplib.SMTP(host, port, timeout=self.config.get("timeout_seconds", 15)) as client:
                if use_tls:
                    client.starttls()
                if username and password:
                    client.login(username, password)
                client.send_message(message)
            log_egress(connector=self.code, url=f"smtp://{host}:{port}", status="sent")
            return ConnectorResult(ok=True)
        except Exception as exc:  # noqa: BLE001
            log_egress(connector=self.code, url=f"smtp://{host}:{port}",
                       status="error", error=str(exc))
            return ConnectorResult(ok=False, error=str(exc))


registry.register(SmtpConnector)
