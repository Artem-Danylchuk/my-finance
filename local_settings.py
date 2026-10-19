"""Прості локальні налаштування: файл не виконується як shell-код."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def setting(name, default=None):
    if name in os.environ:
        return os.environ[name]
    path = ROOT / ".env.local"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value or default
    return default
