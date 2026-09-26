"""Оркестратор обновления (updates-and-backups-spec, этап C; ADR-004).

Запуск на хосте:  python deploy/update.py [--manifest URL] [--pull] [--yes]

Порядок (спека, шаги 1-7): подпись/min_supported ДО действий → pre-flight →
обязательный pre_update-бэкап → образы по дайджестам → up (миграции применит
api транзакционно) → health-check → при провале авто-откат: stop, восстановление
pre_update-бэкапа в основную БД, прежние образы, повторный health-check.
Откат данными через alembic downgrade запрещён (ADR-004) — только бэкап.

Зависимости хоста: docker CLI + docker compose; подпись проверяет контейнер
(единая функция src.core.update) — на хосте только stdlib.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "deploy" / ".erp-state.json"
LOG_PATH = ROOT / "deploy" / "update.log"
LOCAL_MANIFEST = ROOT / "deploy" / ".update-manifest.json"
SERVICES = ("api", "worker", "beat", "web")
HEALTH_TIMEOUT = 120


def log(message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def sh(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    print("  $ " + " ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(cmd)}\n{result.stdout}\n{result.stderr}"
        )
    return result


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return sh(["docker", "compose", *args], check=check)


# ---------- Шаг 1: манифест и подпись (до каких-либо действий) ----------

def fetch_manifest(url: str) -> tuple[Path, Path]:
    """Скачать манифест и подпись в deploy/.update-manifest.json[.sig]."""
    if url.startswith("file://"):
        source = Path(url[len("file://"):])
        data = source.read_bytes()
        sig = Path(str(source) + ".sig").read_bytes() if Path(str(source) + ".sig").exists() else b""
    else:
        with urlopen(Request(url), timeout=30) as response:
            data = response.read()
        sig = b""
        try:
            with urlopen(Request(url + ".sig"), timeout=30) as response:
                sig = response.read()
        except OSError:
            pass
    LOCAL_MANIFEST.write_bytes(data)
    sig_path = Path(str(LOCAL_MANIFEST) + ".sig")
    sig_path.write_bytes(sig)
    return LOCAL_MANIFEST, sig_path


def verify_via_container() -> dict:
    """Подпись проверяет контейнер текущего образа (единая функция, ADR-004)."""
    result = sh(["docker", "compose", "run", "--rm", "--no-deps",
                 "-v", f"{LOCAL_MANIFEST}:/tmp/manifest.json:ro",
                 "-v", f"{Path(str(LOCAL_MANIFEST) + '.sig')}:/tmp/manifest.json.sig:ro",
                 "api", "python", "-m", "src.core.update.verify", "/tmp/manifest.json"],
                check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ПОДПИСЬ НЕ ПРОШЛА, обновление отклонено: {result.stderr.strip()}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def current_version() -> str:
    result = compose("exec", "-T", "api", "python", "-c", "from src import __version__; print(__version__)")
    return result.stdout.strip()


def check_min_supported(ver: dict, current: str) -> None:
    def key(version: str) -> tuple:
        return tuple(int(part) for part in version.split("."))

    if key(current) < key(ver["min_supported"]):
        raise RuntimeError(
            f"Версия {current} ниже min_supported={ver['min_supported']}: "
            f"обновляйтесь по цепочке (см. changelog/releases, поддержка N-2)"
        )


# ---------- Шаги 2-3: pre-flight и обязательный pre_update-бэкап ----------

def preflight() -> dict:
    # место: >= 2x размера БД на разделе тома
    db_size = int(compose("exec", "-T", "db", "psql", "-U", "erp", "-d", "erp", "-t", "-A",
                          "-c", "SELECT pg_database_size('erp')").stdout.strip())
    df = compose("exec", "-T", "db", "sh", "-c", "df -k /var/lib/postgresql/data | tail -1").stdout.split()
    free = int(df[3]) * 1024
    if free < db_size * 2:
        raise RuntimeError(f"мало места: free={free} < 2x БД ({db_size * 2})")
    # сервисы зелёные
    ps = compose("ps", "--format", "json", check=False).stdout.strip()
    states = {}
    for line in ps.splitlines():
        try:
            row = json.loads(line)
            states[row.get("Service", row.get("Name", "?"))] = row.get("State", "?")
        except ValueError:
            continue
    bad = {name: state for name, state in states.items() if state not in ("running", "healthy")}
    if bad:
        raise RuntimeError(f"стек не зелёный: {bad}")
    # БД доступна
    compose("exec", "-T", "db", "pg_isready", "-U", "erp")
    return {"db_size": db_size, "free": free, "services": states}


def preupdate_backup() -> dict:
    result = compose("exec", "-T", "api", "python", "-m", "src.backup", "create",
                     "--kind", "pre_update", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"pre_update-бэкап не создан — СТОП: {result.stderr.strip()}")
    info = json.loads(result.stdout.strip().splitlines()[-1])
    log(f"pre_update-бэкап: {info['file_name']} ({info['id']})")
    return info


# ---------- Шаг 4-5: образы по дайджестам ----------

def save_state(version: str, images: dict) -> None:
    STATE_PATH.write_text(json.dumps({"current_version": version, "previous_images": images},
                                     ensure_ascii=False, indent=2), encoding="utf-8")


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}


def switch_images(manifest_images: dict, pull: bool) -> dict:
    """Проверить дайджесты, запомнить текущие образы, перетегить latest."""
    previous = {}
    for service in SERVICES:
        current_id = sh(["docker", "inspect", "--format", "{{.Id}}", f"erp-{service}:latest"]).stdout.strip()
        previous[service] = current_id
    save_state(current_version(), previous)

    for service in SERVICES:
        entry = manifest_images[service]
        reference = entry["repo"]
        if pull and "/" in reference and not reference.startswith("erp-"):
            sh(["docker", "pull", reference])
        actual = sh(["docker", "inspect", "--format", "{{.Id}}", reference], check=False)
        if actual.returncode != 0:
            raise RuntimeError(f"образ {reference} недоступен локально (и не pull) — СТОП")
        digest = actual.stdout.strip()
        if digest != entry["digest"]:
            raise RuntimeError(f"дайджест {service} не совпал: {digest} != {entry['digest']} — СТОП")
    for service in SERVICES:
        sh(["docker", "tag", manifest_images[service]["repo"], f"erp-{service}:latest"])
        log(f"образ {service}: {manifest_images[service]['repo']} -> erp-{service}:latest")
    return previous


# ---------- Шаг 4b: compose-файл из манифеста (§10.6) ----------

COMPOSE_KEY = "compose_file"


def apply_compose_file(manifest: dict, compose_dir: Path) -> None:
    """docker-compose.box.yml в манифесте с sha256: сверить и заменить.

    Манифест: {"compose_file": {"url": "https://…/docker-compose.box.yml",
    "sha256": "…"}}. Отсутствие ключа — старый манифест, пропускаем.
    Текущий файл сохраняем рядом (.bak) для отката вручную."""
    entry = manifest.get(COMPOSE_KEY)
    if not entry:
        log("манифест без compose_file — файл стека не меняем")
        return
    target = compose_dir / "docker-compose.box.yml"
    data: bytes
    url = entry["url"]
    if url.startswith("file://"):
        data = Path(url[len("file://"):]).read_bytes()
    else:
        with urlopen(Request(url), timeout=30) as response:
            data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise RuntimeError(f"sha256 compose-файла не совпал: {digest} != {entry['sha256']} — СТОП")
    if target.exists():
        current = hashlib.sha256(target.read_bytes()).hexdigest()
        if current == digest:
            log("compose-файл актуален (sha256 совпал)")
            return
        target.rename(target.with_suffix(".yml.bak"))
        log("compose-файл изменён — прежний сохранён как .bak")
    target.write_bytes(data)
    log(f"compose-файл обновлён из манифеста ({digest[:12]}…)")


# ---------- Шаги 6-7: health-check и авто-откат ----------

def wait_health(timeout: int = HEALTH_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(Request("http://127.0.0.1:8000/health"), timeout=5) as response:
                if response.status == 200:
                    with urlopen(Request("http://127.0.0.1:8000/api/v1/modules"), timeout=5) as modules:
                        if modules.status == 200:
                            return True
        except OSError:
            pass
        time.sleep(5)
    return False


def emit_system_event(action: str, payload: dict) -> None:
    """Событие system.updated/system.rollback в новую (или откаченную) систему."""
    code = (
        "from src.core import events\n"
        "from src.core.models import AuditEvent\n"
        "from src.db import SessionLocal\n"
        f"payload = {payload!r}\n"
        "db = SessionLocal()\n"
        f"db.add(AuditEvent(action={action!r}, entity_type='system', payload=payload))\n"
        f"events.publish(db, {action!r}, payload)\n"
        "db.commit()\n"
    )
    compose("exec", "-T", "api", "python", "-c", code, check=False)


def backup_volume_name() -> str:
    """Полное имя volume бэкапов (compose добавляет префикс проекта)."""
    names = sh(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout.split()
    # детерминированно: сначала точное имя проекта, затем любой суффикс, затем голое
    preferred = f"{ROOT.name}_erp_backups"
    if preferred in names:
        return preferred
    for name in names:
        if name.endswith("_erp_backups"):
            return name
    if "erp_backups" in names:
        return "erp_backups"
    raise RuntimeError("backup volume erp_backups not found")


def rollback(previous_images: dict, backup_id: str, target_version: str) -> None:
    log("HEALTH-CHECK ПРОВАЛЕН — авто-откат (ADR-004: восстановление бэкапа, не downgrade)")
    compose("stop", "api", "worker", "beat", "web")
    # восстановление pre_update-бэкапа в основную БД — прежним образом api
    api_image = previous_images.get("api")
    env = ["-e", f"BACKUP_KEY={os.environ.get('BACKUP_KEY', 'KbPbCqtmutVQDaMHVvNSte7n0_F8VkalqpAkmWed5js=')}",
           "-e", "DATABASE_URL=postgresql+psycopg2://erp:erp@db:5432/erp",
           "-e", "BACKUP_DIR=/backups"]
    sh(["docker", "run", "--rm", "--network", "erp_default",
        "-v", f"{backup_volume_name()}:/backups",
        *env, api_image, "python", "-m", "src.backup", "restore",
        "--backup-id", backup_id, "--target", "erp", "--drop"])
    for service in SERVICES:
        sh(["docker", "tag", previous_images[service], f"erp-{service}:latest"])
    compose("up", "-d")
    if wait_health():
        log(f"откат завершён: версия {target_version}, бэкап {backup_id} восстановлен, health OK")
        emit_system_event("system.rollback", {"from_version": target_version, "backup_id": backup_id})
    else:
        log("КРИТИЧНО: откат не поднялся — требуется ручное вмешательство (см. README)")


# ---------- Оркестрация ----------

def main() -> int:
    parser = argparse.ArgumentParser(prog="update.py")
    parser.add_argument("--manifest", default=os.environ.get(
        "UPDATE_MANIFEST_URL", "file://" + str(ROOT / "deploy" / "test-manifest.json")))
    parser.add_argument("--pull", action="store_true", help="тянуть образы из registry (не дев-теги)")
    parser.add_argument("--yes", action="store_true", help="без вопроса перед применением")
    args = parser.parse_args()

    try:
        log(f"=== обновление: манифест {args.manifest} ===")
        fetch_manifest(args.manifest)
        verified = verify_via_container()  # подпись — ДО любых действий
        current = current_version()
        log(f"подпись OK: манифест {verified['version']} (канал {verified['channel']}), текущая {current}")
        check_min_supported(verified, current)

        facts = preflight()
        log(f"pre-flight OK: БД {facts['db_size'] // 1024} КБ, свободно {facts['free'] // (1024 * 1024)} МБ")
        backup = preupdate_backup()

        manifest = json.loads(LOCAL_MANIFEST.read_text(encoding="utf-8"))
        previous = switch_images(manifest["images"], args.pull)
        stack_dir = ROOT / "stack" if (ROOT / "stack").exists() else ROOT
        apply_compose_file(manifest, stack_dir)

        if not args.yes:
            answer = input(f"Применить {manifest['version']} (короткий простой)? [y/N] ")
            if answer.strip().lower() not in ("y", "yes", "д", "да"):
                log("отменено пользователем (образы уже переключены — перетегируйте вручную)")
                return 1

        compose("up", "-d")
        log("стек поднят, ждём health-check (миграции применяет api при старте)…")
        if wait_health():
            new_version = current_version()
            log(f"ОБНОВЛЕНИЕ УСПЕШНО: {current} -> {new_version}, health OK")
            emit_system_event("system.updated", {"from_version": current, "to_version": new_version,
                                                 "backup_id": backup["id"]})
            return 0
        rollback(previous, backup["id"], manifest["version"])
        return 2
    except RuntimeError as exc:
        log(f"ОТКАЗ: {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
