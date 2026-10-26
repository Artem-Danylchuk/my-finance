"""Assignment data, independently checked ledgers and exact-precision reports."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core.data import Transaction

import marko
from shared import SharedExpense, SHARED_CATEGORIES, summarize_month

ROOT = Path(__file__).resolve().parent
THROUGH = date(2026, 10, 15)
START = date(2026, 8, 1)
ZERO = Decimal("0.00")
CONTROL = {"olya": {8: Decimal("2195.21"), 9: Decimal("2582.42")},
           "marko": {8: Decimal("1855.57"), 9: Decimal("2432.83")}}


@dataclass(frozen=True)
class Record:
    id: str
    owner: str
    day: date
    description: str
    category: str
    amount: Decimal
    fixed: bool
    source: str = "bank"
    shared: bool = False


def statement_data(root=ROOT):
    rules = marko.importer.read_rules(root / "rules.csv")
    olya = marko.importer.read_operations(root / "fixtures" / "olya")
    marko_ops, start, through, initial = marko.read_homework_statement(root / "fixtures" / "marko-statement.csv")
    if start != START or through != THROUGH:
        raise ValueError("Потрібна повна виписка Марка за 01.08–15.10.2026")
    return rules, olya, marko_ops, initial


def validate_ledger(content):
    entries, errors, _ = loader.load_string(content)
    if errors:
        raise ValueError("Облік не пройшов bean-check: " + "; ".join(e.message for e in errors))
    return entries


def ledger_data(root=ROOT):
    rules, olya, marko_ops, initial = statement_data(root)
    transactions = marko.importer.render(olya, rules, owner="olya", source="assignment-olya-csv")
    main = (root / "main.beancount").read_text(encoding="utf-8")
    olya_content = main.replace('include "transactions.beancount"', transactions)
    accounts = sorted({marko.BANK, "Equity:Початковий-Залишок-Марко"}
                      | {rule.account for rule in rules}
                      | {marko.importer.classify(op, rules)[0] for op in marko_ops})
    header = ['option "title" "Марко — штучна виписка домашнього завдання"',
              'option "operating_currency" "PLN"']
    header += [f"2026-07-31 open {account} PLN" for account in accounts]
    header += ['2026-07-31 * "Початковий залишок"', f"  {marko.BANK} {initial:.2f} PLN",
               f"  Equity:Початковий-Залишок-Марко {-initial:.2f} PLN", ""]
    marko_content = "\n".join(header) + marko.importer.render(
        marko_ops, rules, bank=marko.BANK, owner="marko", source="assignment-marko-csv")
    entries = {"olya": validate_ledger(olya_content), "marko": validate_ledger(marko_content)}
    return transactions, marko_content, entries


def build_ledgers(root=ROOT):
    transactions, marko_content, entries = ledger_data(root)
    # Validate both sources before replacing either generated file.
    marko.importer.write_atomic(root / "transactions.beancount", transactions)
    marko.importer.write_atomic(root / "marko.beancount", marko_content)
    return entries


def records(root=ROOT):
    _, _, ledgers = ledger_data(root)
    result = []
    for owner, entries in ledgers.items():
        for index, entry in enumerate(entries):
            if not isinstance(entry, Transaction):
                continue
            for posting in entry.postings:
                if posting.account.startswith(("Income:", "Expenses:")):
                    result.append(Record(
                        f"{owner}:{index}", owner, entry.date, entry.narration, posting.account,
                        posting.units.number, "fixed" in entry.tags, "bank",
                        posting.account in SHARED_CATEGORIES))
    return result


def monthly(all_records, owner, month):
    if owner not in CONTROL or month.day != 1:
        raise ValueError("Невідомий користувач або місяць")
    own = [r for r in all_records if r.owner == owner and r.day.replace(day=1) == month]
    expenses = [r for r in own if r.category.startswith("Expenses:")]
    income = -sum((r.amount for r in own if r.category.startswith("Income:")), ZERO)
    total = sum((r.amount for r in expenses), ZERO)
    fixed = sum((r.amount for r in expenses if r.fixed), ZERO)
    bank_variable = sum((r.amount for r in expenses if not r.fixed and r.source == "bank"), ZERO)
    categories = {}
    for r in expenses:
        categories[r.category] = categories.get(r.category, ZERO) + r.amount
    return {"income": income, "expenses": total, "fixed": fixed, "variable": total - fixed,
            "bank_variable": bank_variable, "saved": income - total,
            "rate": (income - total) / income * 100 if income else None,
            "categories": sorted(categories.items(), key=lambda pair: -pair[1]),
            "cash": [r for r in expenses if r.source == "cash"], "operations": len(expenses)}


def shared_month(all_records, month):
    shared = [SharedExpense(r.id, r.day, r.owner, r.amount, r.category)
              for r in all_records if r.shared and r.category in SHARED_CATEGORIES]
    return summarize_month(shared, (), month=month, shared_since=START)
