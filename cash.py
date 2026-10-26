"""Durable manual expenses: SQLite is authoritative; Beancount export is derived."""

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import sqlite3

import homework


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS cash (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, day TEXT NOT NULL,
                description TEXT NOT NULL, category TEXT NOT NULL,
                cents INTEGER NOT NULL CHECK(cents > 0), shared INTEGER NOT NULL CHECK(shared IN (0,1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                address TEXT NOT NULL, happened INTEGER NOT NULL)""")
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def records(self):
        with self.connect() as db:
            return self._records(db)

    @staticmethod
    def _records(db):
        return [homework.Record("cash:" + row["id"], row["owner"], date.fromisoformat(row["day"]),
                                row["description"], row["category"], Decimal(row["cents"]) / 100,
                                False, "cash", bool(row["shared"]))
                for row in db.execute("SELECT * FROM cash ORDER BY day, created_at, id")]

    def add(self, owner, identifier, day, description, category, amount, shared, categories):
        if owner not in homework.CONTROL or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Некоректний користувач або ідентифікатор форми")
        parsed_day = date.fromisoformat(day)
        if not homework.START <= parsed_day <= date.today():
            raise ValueError("Дата має бути від 01.08.2026 до сьогодні")
        description = description.strip()
        if not 1 <= len(description) <= 160 or any(ord(c) < 32 for c in description):
            raise ValueError("Опис: від 1 до 160 символів без перенесення рядка")
        if category not in categories or shared not in (True, False):
            raise ValueError("Вибери категорію та вид витрати")
        if shared and category not in homework.SHARED_CATEGORIES:
            raise ValueError("Спільними в цій версії можуть бути оренда та продукти")
        value = homework.marko.importer.money(amount.replace(",", "."))
        if not 0 < value <= Decimal("100000"):
            raise ValueError("Сума має бути від 0,01 до 100 000 PLN")
        with self.connect() as db:
            existing = db.execute("SELECT * FROM cash WHERE id = ?", (identifier,)).fetchone()
            data = (owner, day, description, category, int(value * 100), int(shared))
            if existing:
                previous = tuple(existing[k] for k in ("owner", "day", "description", "category", "cents", "shared"))
                if previous != data:
                    raise ValueError("Цю форму вже використано для іншої операції; онови сторінку")
                return False
            db.execute("INSERT INTO cash (id, owner, day, description, category, cents, shared) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (identifier, *data))
            # Validate the proposed full cash ledger before committing the database.
            homework.validate_ledger(ledger(self._records(db)))
        return True


def ledger(records):
    accounts = {r.category for r in records} | {"Assets:Готівка-Оля", "Assets:Готівка-Марко"}
    lines = ['; Штучні готівкові витрати. Залишок готівки не визначено.',
             'option "operating_currency" "PLN"']
    lines += [f"2026-07-31 open {account} PLN" for account in sorted(accounts)]
    for r in records:
        account = "Assets:Готівка-Оля" if r.owner == "olya" else "Assets:Готівка-Марко"
        lines += [f"{r.day} * {json.dumps(r.description, ensure_ascii=False)}",
                  f'  owner: "{r.owner}"', '  source: "cash"',
                  f'  source-id: "{r.id}"', f"  {r.category} {r.amount:.2f} PLN",
                  f"  {account} {-r.amount:.2f} PLN"]
    return "\n".join(lines) + "\n"
