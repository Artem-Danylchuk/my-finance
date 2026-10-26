"""Прості локальні налаштування: файл не виконується як shell-код."""

import os
import re
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parent
_write_lock = threading.Lock()


def setting(name, default=None):
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
                if value:
                    return value
    return os.environ.get(name) or default


def save_openai_key(key):
    if not isinstance(key, str) or not re.fullmatch(r"sk-[A-Za-z0-9_-]{20,1000}", key.strip()):
        raise ValueError("Потрібен API-ключ OpenAI, який починається з sk-. Токен входу ChatGPT не підходить.")
    updates = {"AI_PROVIDER": "openai", "OPENAI_API_KEY": key.strip(),
               "OPENAI_MODEL": setting("OPENAI_MODEL", "gpt-6-luna")}
    with _write_lock:
        path = ROOT / ".env.local"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = [line for line in old.splitlines()
                 if line.split("=", 1)[0].strip() not in updates]
        lines.extend(name + "=" + value for name, value in updates.items())
        temporary = None
        try:
            with NamedTemporaryFile("w", encoding="utf-8", dir=ROOT,
                                    prefix=".env.local.", delete=False) as stream:
                temporary = Path(stream.name)
                temporary.chmod(0o600)
                stream.write("\n".join(lines) + "\n")
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
