"""One command for a fresh clone: rebuild both banks, bean-check, print controls."""

import subprocess
import sys
from datetime import date
from pathlib import Path

import homework
import cash


def main():
    root = Path(__file__).resolve().parent
    homework.build_ledgers(root)
    checker = str(Path(sys.executable).with_name("bean-check"))
    for name in ("main.beancount", "marko.beancount"):
        subprocess.run([checker, str(root / name)], check=True)
    database = root / ".private" / "tracker.sqlite3"
    if database.exists():
        records = cash.Store(database).records()
        output = database.parent / "cash-check.beancount"
        homework.marko.importer.write_atomic(output, cash.ledger(records))
        subprocess.run([checker, str(output)], check=True)
        print(f"Готівковий облік: {len(records)} збережених операцій, bean-check без помилок.")
    print("bean-check: обидві виписки перевірено, помилок немає.")
    rows = homework.records(root)
    for owner, months in homework.CONTROL.items():
        for month, expected in months.items():
            actual = homework.monthly(rows, owner, date(2026, month, 1))["bank_variable"]
            if actual != expected:
                raise SystemExit(f"ПОМИЛКА: {owner} 2026-{month:02}: {actual:.2f} != {expected:.2f}")
            print(f"{'Оля' if owner == 'olya' else 'Марко'} 2026-{month:02}: {actual:.2f} PLN — OK")
    print("Джерела: лише штучні виписки домашнього завдання; готівка не змінює банківських контрольних сум.")


if __name__ == "__main__":
    main()
