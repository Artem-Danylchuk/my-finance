"""Адаптер вигаданого формату Марка. Приклад не є форматом реального банку."""

import csv
import importlib
import json
import hashlib
from datetime import date, datetime
from decimal import InvalidOperation

from beancount import loader

importer = importlib.import_module("import")
BANK = "Assets:Банк-Марко"


def read_homework_statement(path):
    """Read the assignment's synthetic bank export, newest booking first.

    Reverse the bank's rows rather than sorting dates: same-day balance order
    is significant. Operation date is informational; accounting uses booking.
    """
    def money(text):
        return importer.money(text.replace(" ", "").replace("\u00a0", "").replace(",", "."))

    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream, delimiter=";"))
    expected = ["Data operacji", "Data księgowania", "Typ operacji", "Opis", "Kwota", "Saldo po operacji"]
    header = next((i for i, row in enumerate(rows) if row == expected), None)
    if header is None:
        raise ValueError("У виписці Марка немає очікуваного заголовка")
    meta = {row[0]: row[1] for row in rows[:header] if len(row) == 2}
    if meta.get("Waluta") != "PLN":
        raise ValueError("Підтримується лише виписка Марка у PLN")
    try:
        start, end = meta["Okres"].split(" - ")
        start, through = (datetime.strptime(x, "%d.%m.%Y").date() for x in (start, end))
        balance = initial = money(meta["Saldo początkowe"])
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise ValueError("Некоректний період або початковий залишок Марка") from exc
    operations, seen = [], set()
    previous = start
    for line, row in reversed(list(enumerate(rows[header + 1:], header + 2))):
        if not row:
            continue
        try:
            if len(row) != 6:
                raise ValueError("очікуються шість колонок")
            completed = datetime.strptime(row[1], "%d.%m.%Y")
            datetime.strptime(row[0], "%d.%m.%Y")
            identifier = hashlib.sha256(json.dumps(row, ensure_ascii=False).encode()).hexdigest()
            if identifier in seen:
                continue
            if not start <= completed.date() <= through or completed.date() < previous:
                raise ValueError("порушено період або порядок банку")
            amount, after = money(row[4]), money(row[5])
            if balance + amount != after:
                raise ValueError("залишок не узгоджується із сумою операції")
            if not row[3].strip():
                raise ValueError("порожній опис")
            operations.append(importer.Operation(completed, row[3].strip(), amount, after, identifier))
            seen.add(identifier)
            balance, previous = after, completed.date()
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"{path}:{line}: {exc}") from exc
    if not operations:
        raise ValueError("Виписка Марка не містить операцій")
    return operations, start, through, initial


def read_operations(path, *, opening_date, opening_balance, through):
    operations, seen = [], {}
    balance = importer.money(opening_balance)
    previous_day = opening_date
    required = {"ID", "Data księgowania", "Opis", "Kwota", "Saldo", "Waluta", "Status"}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path}: бракує колонок прикладу Марка")
        for line, row in enumerate(reader, 2):
            try:
                if row["Waluta"] != "PLN" or row["Status"] != "ZAKSIEGOWANA":
                    raise ValueError("підтримуються лише проведені операції PLN")
                identifier = row["ID"].strip()
                op = importer.Operation(
                    datetime.strptime(row["Data księgowania"], "%d.%m.%Y"),
                    row["Opis"].strip(), importer.money(row["Kwota"].replace(",", ".")),
                    importer.money(row["Saldo"].replace(",", ".")), source_id=identifier)
                if not identifier or not op.description:
                    raise ValueError("порожній ідентифікатор або опис")
                if identifier in seen:
                    if seen[identifier] != op:
                        raise ValueError("один ID містить різні операції")
                    continue
                if not opening_date < op.day <= through or op.day < previous_day:
                    raise ValueError("дата поза покриттям або порушено порядок банку")
                if balance + op.amount != op.balance:
                    raise ValueError("залишок не узгоджується із сумою операції")
                seen[identifier] = op
                operations.append(op)
                balance, previous_day = op.balance, op.day
            except (ValueError, TypeError, AttributeError, InvalidOperation) as exc:
                raise ValueError(f"{path}:{line}: {exc}") from exc
    if not operations:
        raise ValueError(f"{path}: немає операцій")
    return operations


def generate_demo(root):
    try:
        config = json.loads((root / "household.json").read_text(encoding="utf-8"))["marko"]
        if config["demo"] is not True:
            raise ValueError("ця команда призначена лише для вигаданих даних")
        opening = date.fromisoformat(config["opening_date"])
        through = date.fromisoformat(config["through"])
        initial = importer.money(config["opening_balance_pln"])
        operations = read_operations(root / config["statement"], opening_date=opening,
                                     opening_balance=initial, through=through)
        rules = importer.read_rules(root / "rules.csv")
        accounts = sorted({BANK, "Equity:Початковий-Залишок-Марко"}
                          | {importer.classify(op, rules)[0] for op in operations})
        header = ['; ДЕМОНСТРАЦІЯ: усі дані Марка вигадані.',
                  'option "title" "Марко — вигаданий приклад"',
                  'option "operating_currency" "PLN"', ""]
        header.extend(f"{opening} open {account} PLN" for account in accounts)
        header.extend(["", f'{opening} * "Демонстраційний початковий залишок"',
                       f"  {BANK} {initial:.2f} PLN",
                       f"  Equity:Початковий-Залишок-Марко {-initial:.2f} PLN", ""])
        content = "\n".join(header) + "\n" + importer.render(
            operations, rules, bank=BANK, owner="marko")
        _, errors, _ = loader.load_string(content)
        if errors:
            raise ValueError(f"демооблік не пройшов перевірку: {len(errors)} помилок")
        output = root / "demo-marko.beancount"
        importer.write_atomic(output, content)
    except (ValueError, OSError, KeyError) as exc:
        raise SystemExit(f"Помилка демоімпорту Марка: {exc}") from exc
    print(f"ДЕМОНСТРАЦІЯ: імпортовано {len(operations)} вигаданих операцій у {output.name}.")
    print(f"Покриття після {opening:%d.%m.%Y} до {through:%d.%m.%Y}; "
          f"залишок {operations[-1].balance:.2f} PLN.")
