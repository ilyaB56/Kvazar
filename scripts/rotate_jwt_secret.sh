#!/bin/sh
# Ротация JWT_SECRET (security-plan P1 п.9).
# Все сессии инвалидируются (token_version/подпись меняется) —
# документированное поведение: пользователи просто перелогиниваются.
#
# Запуск из корня проекта: sh scripts/rotate_jwt_secret.sh [--apply]
#   без --apply — печатает новый секрет и инструкции;
#   с --apply — обновляет .env (бэкап .env.bak) и перезапускает api.

set -e

NEW=$(openssl rand -hex 32 2>/dev/null || python -c "import secrets; print(secrets.token_hex(32))")
echo "Новый JWT_SECRET: $NEW"
echo "После применения ВСЕ сессии будут завершены (пользователи перелогинятся)."

if [ "$1" = "--apply" ]; then
    if [ ! -f .env ]; then
        echo ".env не найден — добавьте строку вручную:" >&2
        echo "  JWT_SECRET=$NEW" >&2
        exit 1
    fi
    cp .env .env.bak
    if grep -q '^JWT_SECRET=' .env; then
        sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$NEW|" .env
    else
        printf '\nJWT_SECRET=%s\n' "$NEW" >> .env
    fi
    rm -f .env.bak.bak
    echo ".env обновлён (бэкап: .env.bak). Перезапуск api…"
    docker compose up -d api
    echo "Готово. Прежний секрет: см. .env.bak (удалите после проверки)."
else
    echo "Применить: sh scripts/rotate_jwt_secret.sh --apply"
    echo "Вручную: JWT_SECRET=$NEW в .env && docker compose up -d api"
fi
