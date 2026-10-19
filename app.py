"""Локальний звіт Олі з AI-асистентом. Доступ лише з цього комп’ютера.

Запуск: uv run python app.py --through 2026-10-15
Це не сервер двох користувачів: персональний перегляд тут завжди Олин.
"""

import argparse
import json
import secrets
import threading
from datetime import date
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import ai_features
import report
from local_settings import setting


def ai_status():
    provider = setting("AI_PROVIDER", "openai")
    labels = {"openai": "OpenAI", "ollama": "Ollama", "lmstudio": "LM Studio"}
    if provider == "openai":
        configured = bool(setting("OPENAI_API_KEY") and setting("OPENAI_MODEL"))
        message = "Додай API-ключ і назву моделі у .env.local, потім онови сторінку."
    elif provider in {"ollama", "lmstudio"}:
        configured = bool(setting("AI_MODEL"))
        message = "Вкажи встановлену модель у .env.local, запусти її локальний сервер і онови сторінку."
    else:
        configured = False
        message = "Вибери підтримуваний AI_PROVIDER у .env.local."
    return {"configured": configured, "provider": labels.get(provider, "Не налаштовано"),
            "message": "Можна поставити питання про свої або спільні витрати." if configured else message}


def make_handler(through, port):
    origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    csrf = secrets.token_urlsafe(32)
    busy = threading.BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # Questions and credentials are never written to the access log.
            pass

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def local_host(self):
            if self.headers.get("Host") not in hosts:
                self.respond(403, {"error": "Цей звіт доступний лише локально."})
                return False
            return True

        def do_GET(self):
            if not self.local_host():
                return
            if self.path not in {"/", "/report.html"}:
                self.respond(404, {"error": "Сторінку не знайдено."})
                return
            try:
                html = (report.ROOT / "report.html").read_text(encoding="utf-8")
            except OSError:
                self.respond(503, {"error": "Звіт недоступний. Перезапусти локальний застосунок."})
                return
            metadata = {**ai_status(), "csrf": csrf}
            meta = '<meta name="ai-server" content="' + escape(json.dumps(metadata, ensure_ascii=False), quote=True) + '">'
            self.respond(200, html.replace("</head>", meta + "</head>").encode("utf-8"), "text/html; charset=utf-8")

        def do_POST(self):
            if not self.local_host():
                return
            if urlsplit(self.path).path != "/api/ask":
                self.respond(404, {"error": "Невідомий запит."})
                return
            if (self.headers.get("Origin") not in origins
                    or not secrets.compare_digest(self.headers.get("X-Finance-CSRF", ""), csrf)):
                self.respond(403, {"error": "Онови локальну сторінку й повтори запит."})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                self.respond(415, {"error": "Очікується питання у форматі JSON."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    self.respond(413, {"error": "Питання надто велике."})
                    return
                self.connection.settimeout(10)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {"question", "month", "scope"}:
                    raise ValueError("Некоректні поля питання.")
                question, scope = payload["question"], payload["scope"]
                if not isinstance(question, str) or not question.strip() or len(question) > 1000:
                    raise ValueError("Напиши питання від 1 до 1000 символів.")
                if scope not in ("personal", "shared"):
                    raise ValueError("Доступні лише твої та спільні витрати.")
                if not isinstance(payload["month"], str):
                    raise ValueError("Вибери місяць звіту.")
                month = date.fromisoformat(payload["month"])
                if month.day != 1 or month > through:
                    raise ValueError("Цей місяць ще не покритий звітом.")
            except (ValueError, TypeError, UnicodeError, OSError):
                self.respond(400, {"error": "Перевір питання, обраний місяць і вкладку."})
                return
            status = ai_status()
            if not status["configured"]:
                self.respond(503, {"error": status["message"]})
                return
            if not busy.acquire(blocking=False):
                self.respond(429, {"error": "Асистент уже відповідає. Дочекайся завершення запиту."})
                return
            try:
                result = ai_features.answer_question(question.strip(), through=through,
                                                     month_hint=month, scope_hint=scope)
                self.respond(200, result)
            except ValueError as exc:
                self.respond(422, {"error": str(exc)})
            except Exception:
                self.respond(503, {"error": "Не вдалося отримати відповідь. Перевір підключення моделі та облік."})
            finally:
                busy.release()

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Локальний фінансовий звіт з AI-асистентом")
    parser.add_argument("--through", required=True, type=date.fromisoformat)
    parser.add_argument("--port", type=int, default=5051)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("порт має бути від 1024 до 65535")
    try:
        report.generate(report.ROOT / "main.beancount", report.ROOT / "report.html", args.through,
                        household=report.ROOT / "household.json")
        with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.through, args.port)) as server:
            server.daemon_threads = True
            print(f"Звіт з AI: http://127.0.0.1:{args.port}/ · зупинити: Ctrl+C", flush=True)
            print("Локальний перегляд Олі. Секрети читаються з .env.local; у браузер не передаються.", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError) as exc:
        raise SystemExit(f"Не вдалося запустити локальний звіт: {exc}") from None


if __name__ == "__main__":
    main()
