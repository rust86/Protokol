#!/usr/bin/env bash
# Установка в изолированное виртуальное окружение. Системный Python не изменяется.
# Использование:  scripts/install.sh [--system-site-packages]
#   --system-site-packages  разрешить видеть системные python-пакеты (например, PySide6
#                           из репозитория ALT Linux); pip всё равно ставит только в .venv
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_ARGS=()
if [[ "${1:-}" == "--system-site-packages" ]]; then
  VENV_ARGS+=(--system-site-packages)
fi

PY="${PYTHON:-python3}"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Нужен Python 3.9 или новее: $("$PY" --version 2>&1)" >&2
  exit 1
fi
if ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  echo "Не найден модуль venv/ensurepip." >&2
  echo "Ubuntu: sudo apt install python3-venv python3-pip" >&2
  echo "ALT:    su -c 'apt-get install python3-module-pip python3-module-venv' (при необходимости)" >&2
  exit 1
fi

"$PY" -m venv "${VENV_ARGS[@]}" .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

echo
echo "== Проверка зависимостей =="
.venv/bin/python -m protocol_archive.envcheck || true
echo
echo "Запуск:  .venv/bin/protocol-archive"
echo "Тесты:   .venv/bin/python -m pytest"
