"""Rebuild the ledger from read-only bank CSVs and rules.csv."""

import argparse
import csv
import heapq
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile

from beancount.core import account as bean_account


ROOT = Path(__file__).resolve().parent
BANK = "Assets:Банк"


@dataclass(frozen=True)
class Rule:
    pattern: str
    account: str
    fixed: bool


@dataclass(frozen=True)
class Operation:
    completed: datetime
    description: str
    amount: Decimal
    balance: Decimal
    source_id: str = ""

    @property
    def day(self):
        return self.completed.date()


def money(value):
    result = Decimal(value)
    if not result.is_finite() or result != result.quantize(Decimal("0.01")):
        raise ValueError(f"Некоректна сума PLN: {value!r}")
    return result


def read_rules(path):
    rules = []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["шаблон", "рахунок", "фіксована"]:
            raise ValueError(f"{path}: очікуються колонки шаблон,рахунок,фіксована")
        for line, row in enumerate(reader, 2):
            pattern = (row["шаблон"] or "").strip()
            account = (row["рахунок"] or "").strip()
            fixed = (row["фіксована"] or "").strip()
            if (not pattern or not bean_account.is_valid(account)
                    or not account.startswith(("Expenses:", "Income:"))
                    or fixed not in {"так", "ні"}):
                raise ValueError(f"{path}:{line}: некоректне правило")
            rules.append(Rule(pattern.casefold(), account, fixed == "так"))
    return rules


def read_operations(data_dir):
    paths = sorted(data_dir.glob("*.csv"))
    if not paths:
        raise ValueError(f"{data_dir}: немає CSV-виписок")
    # CSV adjacency preserves the bank's ordering, including times that go backwards.
    # Shared rows join overlapping statements; the key is exactly the four bank fields.
    indices, operations, edges, indegree = {}, [], [], []
    required = {"Completed Date", "Description", "Amount", "Balance", "Currency", "State", "Fee"}
    for path in paths:
        previous = None
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not required.issubset(reader.fieldnames or []):
                raise ValueError(f"{path}: бракує обов’язкових колонок виписки")
            for line, row in enumerate(reader, 2):
                try:
                    if row["Currency"] != "PLN" or row["State"] != "COMPLETED":
                        raise ValueError("підтримуються лише завершені операції у PLN")
                    if money(row["Fee"]) != 0:
                        raise ValueError("ненульова комісія потребує окремого правила обліку")
                    op = Operation(datetime.fromisoformat(row["Completed Date"]),
                                   row["Description"], money(row["Amount"]), money(row["Balance"]))
                    if not op.description or op.day <= date(2026, 7, 31):
                        raise ValueError("порожній опис або дата до початку обліку")
                except (ValueError, TypeError, InvalidOperation) as exc:
                    raise ValueError(f"{path}:{line}: {exc}") from exc
                if op not in indices:
                    indices[op] = len(operations)
                    operations.append(op)
                    edges.append(set())
                    indegree.append(0)
                index = indices[op]
                if previous is not None and previous != index and index not in edges[previous]:
                    edges[previous].add(index)
                    indegree[index] += 1
                previous = index
    ready = [(op.day, i) for i, op in enumerate(operations) if not indegree[i]]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, index = heapq.heappop(ready)
        ordered.append(operations[index])
        for following in edges[index]:
            indegree[following] -= 1
            if not indegree[following]:
                heapq.heappush(ready, (operations[following].day, following))
    if len(ordered) != len(operations):
        raise ValueError("Виписки містять суперечливий порядок операцій")
    if not ordered:
        raise ValueError("Виписки не містять операцій")
    for before, after in zip(ordered, ordered[1:]):
        if before.day > after.day:
            raise ValueError("Дати у виписці йдуть у зворотному порядку")
    return ordered


def classify(op, rules):
    root = "Income:" if op.amount > 0 else "Expenses:"
    for rule in rules:
        if rule.account.startswith(root) and rule.pattern in op.description.casefold():
            return rule.account, rule.fixed and root == "Expenses:"
    return ("Income:Інше" if op.amount > 0 else "Expenses:Без-Категорії"), False


def render(operations, rules, *, bank=BANK, owner=None, source="demo-marko-csv"):
    lines = ["; Згенеровано import.py. Не редагувати вручну.", ""]
    month_ends = {}
    for op in operations:
        account, fixed = classify(op, rules)
        tag = " #fixed" if fixed else ""
        description = json.dumps(op.description, ensure_ascii=False)
        lines.append(f"{op.day} * {description}{tag}")
        if owner is not None:
            lines.append(f'  owner: {json.dumps(owner)}')
            lines.append(f'  source: {json.dumps(source)}')
            if op.source_id:
                lines.append(f'  source-id: {json.dumps(op.source_id)}')
        lines.extend([f"  {bank}  {op.amount:.2f} PLN",
                      f"  {account}  {-op.amount:.2f} PLN", ""])
        # Last row in BANK order, never maximum timestamp within the day.
        month_ends[op.day.replace(day=1)] = op
    last_month = max(month_ends)
    for month, op in sorted(month_ends.items()):
        assertion_day = (op.day + timedelta(days=1) if month == last_month
                         else (month.replace(day=28) + timedelta(days=4)).replace(day=1))
        lines.append(f"{assertion_day} balance {bank} {op.balance:.2f} PLN")
    return "\n".join(lines) + "\n"


def write_atomic(path, content):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=()):
    parser = argparse.ArgumentParser(description="Імпорт банківських виписок")
    parser.add_argument("--demo-marko", action="store_true",
                        help="імпортувати вигаданий приклад Марка в окремий демооблік")
    args = parser.parse_args(argv)
    if args.demo_marko:
        from marko import generate_demo
        generate_demo(ROOT)
        return
    try:
        rules = read_rules(ROOT / "rules.csv")
        operations = read_operations(ROOT / "data")
        write_atomic(ROOT / "transactions.beancount", render(operations, rules))
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Помилка імпорту: {exc}") from exc
    print(f"Імпортовано {len(operations)} операцій у transactions.beancount.")
    unknown = [op for op in operations if classify(op, rules)[0] == "Expenses:Без-Категорії"]
    print(f"Без категорії: {len(unknown)} операцій.")
    for op in unknown:
        print(f"  {op.day} | {op.description} | {op.amount:.2f} PLN")


if __name__ == "__main__":
    main(None)
