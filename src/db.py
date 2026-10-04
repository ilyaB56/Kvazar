from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.config import get_settings


class Base(DeclarativeBase):
    """Единая база для всех моделей. Схемы задаются в моделях через __table_args__."""


_settings = get_settings()
engine = create_engine(_settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# RO-подключение браузера таблиц (devtools-spec §9.2): роль erp_ro без
# прав на запись + default_transaction_read_only на соединении (двойная
# защита) + statement_timeout против тяжёлых фильтров. Мутации ракурсов
# идут через основную SessionLocal (доменные операции API-слоя).
# Если DATABASE_URL_RO не переопределён (дефолт с localhost) — берём
# хост/порт основного URL и подставляем erp_ro (дев-контур, §9.2).
def _ro_url() -> str:
    from urllib.parse import urlsplit, urlunsplit

    ro = _settings.database_url_ro
    if urlsplit(ro).hostname not in ("localhost", "127.0.0.1"):
        return ro
    main = urlsplit(_settings.database_url)
    if main.hostname in ("localhost", "127.0.0.1"):
        return ro  # дев на хосте — как есть
    parts = urlsplit(ro)
    return urlunsplit((parts.scheme, f"erp_ro:erp_ro@{main.hostname}:{main.port or 5432}",
                       parts.path, parts.query, ""))


engine_ro = create_engine(
    _ro_url(), pool_pre_ping=True,
    connect_args={"options": "-c statement_timeout=15000 -c default_transaction_read_only=on"})


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
