"""Local launch without saving credentials to disk or source control."""

from getpass import getpass
import secrets

from werkzeug.security import generate_password_hash

from tracker import create_app


def main():
    passwords = {}
    for owner, label in (("olya", "Оля"), ("marko", "Марко")):
        password = getpass(f"Новий локальний пароль — {label} (мінімум 12 символів): ")
        if len(password) < 12:
            raise SystemExit("Пароль має містити щонайменше 12 символів")
        if password != getpass("Повтори пароль: "):
            raise SystemExit("Паролі не збігаються")
        passwords[owner] = generate_password_hash(password)
    import os
    os.environ["TRACKER_SECRET_KEY"] = secrets.token_urlsafe(48)
    for owner, value in passwords.items():
        os.environ[f"TRACKER_{owner.upper()}_PASSWORD_HASH"] = value
    print("Логіни: olya і marko. Локальний звіт: http://127.0.0.1:5052/", flush=True)
    print("Для публічної HTTPS-адреси дивись DEPLOYMENT.md.", flush=True)
    create_app({"LOCAL_ONLY": True}).run(host="127.0.0.1", port=5052, debug=False)


if __name__ == "__main__":
    main()
