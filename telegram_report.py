"""Попередній перегляд і явне надсилання спільного підсумку в Telegram.

Розклад не встановлюється. Повторне надсилання місяця блокується локально.
API: https://core.telegram.org/bots/api#sendmessage
"""

import argparse
import calendar
import json
import sqlite3
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import report
from household_report import load_context, month_summary
from local_settings import setting


def preview(month, through, root=report.ROOT):
    context = load_context(root / "main.beancount", root / "household.json", through)
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    if end < max(context["since"], context["marko_start"]) or month > context["marko_through"]:
        raise ValueError("За цей місяць немає спільних даних.")
    summary = month_summary(context, month)
    complete = (end <= min(through, context["marko_through"])
                and month >= max(context["since"], context["marko_start"]))
    names = {"olya": "Оля", "marko": "Марко"}
    settlement = (f"{names[summary.debtor]} → {names[summary.creditor]}: {report.money(summary.due)}"
                  if summary.debtor else "Компенсація не потрібна")
    text = (f"Наші гроші · {report.month_label(month)}\n"
            f"{'Повний' if complete else 'Неповний'} місяць\n"
            "ДЕМО: дані Марка вигадані\n"
            f"Спільні витрати: {report.money(summary.total)}\n"
            f"Оля оплатила: {report.money(summary.olya.paid)}\n"
            f"Марко оплатив: {report.money(summary.marko.paid)}\n{settlement}\n"
            f"Дані Олі до {min(end, through):%d.%m.%Y}; "
            f"Марка до {min(end, context['marko_through']):%d.%m.%Y}.\n"
            "До внесення переказів компенсацій; борг інших місяців не включено.")
    return text, complete


def send(text, month, root=report.ROOT):
    token, chat = setting("TELEGRAM_BOT_TOKEN"), setting("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise ValueError("Налаштуйте TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID у .env.local.")
    private = root / ".private"
    private.mkdir(mode=0o700, exist_ok=True)
    database = sqlite3.connect(private / "telegram.sqlite3")
    try:
        database.execute("CREATE TABLE IF NOT EXISTS deliveries (chat TEXT, month TEXT, status TEXT, PRIMARY KEY(chat, month))")
        try:
            database.execute("INSERT INTO deliveries VALUES (?, ?, 'pending')", (chat, month.isoformat()))
            database.commit()
        except sqlite3.IntegrityError:
            raise ValueError("Цей місяць уже надсилали або результат попередньої спроби невідомий. Автоматичний дубль заблоковано.") from None
        request = Request("https://api.telegram.org/bot" + token + "/sendMessage",
                          data=json.dumps({"chat_id": chat, "text": text}).encode(),
                          headers={"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                result = json.load(response)
            if result.get("ok") is not True:
                raise ValueError("Telegram не підтвердив надсилання.")
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise ValueError("Надсилання не підтверджено. Перевірте чат перед повтором; автоматичний повтор заблоковано.") from None
        database.execute("UPDATE deliveries SET status='sent' WHERE chat=? AND month=?", (chat, month.isoformat()))
        database.commit()
    finally:
        database.close()


def main():
    parser = argparse.ArgumentParser(description="Спільний місячний підсумок Telegram")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--through", required=True, type=date.fromisoformat)
    parser.add_argument("--send", action="store_true", help="надіслати; без прапорця лише перегляд")
    args = parser.parse_args()
    try:
        month = date.fromisoformat(args.month + "-01")
        text, complete = preview(month, args.through)
        if args.send:
            if not complete:
                raise ValueError("Щомісячне надсилання очікує повних виписок обох людей; зараз доступний попередній перегляд.")
            send(text, month)
            print("Спільний підсумок надіслано.")
        else:
            print(text)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
