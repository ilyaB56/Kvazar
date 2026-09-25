#!/bin/sh
# Аудит безопасности (security-plan P1 п.10): pip-audit + npm audit +
# ruff banned-api. Отчёт в stdout и SECURITY-AUDIT.md.
#
# Запуск из корня проекта: sh scripts/security_audit.sh
# (pip-audit ставится во временный venv контейнера api, npm audit —
# в frontend/; вне контейнера нужен локальный python/npm)

set -e
REPORT="SECURITY-AUDIT.md"
{
echo "# Security audit — $(date -u +%Y-%m-%dT%H:%MZ)"
echo

echo "## pip-audit (python)"
if docker compose exec -T api sh -c "pip install -q pip-audit 2>/dev/null; pip-audit --skip-editable" 2>&1; then
  echo "(pip-audit: чисто)"
else
  echo "(pip-audit: см. вывод выше — уязвимости зафиксированы ниже)"
fi
echo

echo "## npm audit (frontend)"
(cd frontend && npm audit --audit-level=high 2>&1) || true
echo

echo "## ruff (banned-api и весь линт)"
docker compose exec -T api python -m ruff check src/ 2>&1 || true
echo
} | tee "$REPORT"

echo "Отчёт: $REPORT"
