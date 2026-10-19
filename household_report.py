"""Спільні підсумки та проводки розподілу для наявних запитів звіту."""

import json
from datetime import date, timedelta
from decimal import Decimal

from beancount import loader
from beancount.core.data import Transaction

import marko
from shared import SHARED_CATEGORIES, SharedExpense, split, summarize_month


def load_context(ledger, config_path, through):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    since = date.fromisoformat(config["shared_since"])
    demo = config["marko"]
    if demo["demo"] is not True:
        raise ValueError("Підключення Марка поки підтримує лише демонстраційний CSV")
    opening = date.fromisoformat(demo["opening_date"])
    marko_through = date.fromisoformat(demo["through"])
    operations = marko.read_operations(config_path.parent / demo["statement"],
                                       opening_date=opening,
                                       opening_balance=demo["opening_balance_pln"],
                                       through=marko_through)
    if marko_through > through:
        raise ValueError("Покриття Марка пізніше дати звіту; збільште --through")
    entries, errors, _ = loader.load_file(str(ledger))
    if errors:
        raise ValueError("Облік Олі містить помилки")
    rules = marko.importer.read_rules(config_path.parent / "rules.csv")
    expenses, adjustments = [], [f"{since} open Equity:Розподіл-Спільного PLN"]
    paid_by_month = {}

    def add(identifier, day, payer, amount, category, fixed):
        if day < since or category not in SHARED_CATEGORIES:
            return
        expenses.append(SharedExpense(identifier, day, payer, amount, category))
        month_end = day.replace(day=28) + timedelta(days=4)
        month_end = month_end.replace(day=1) - timedelta(days=1)
        if month_end < opening + timedelta(days=1) or day.replace(day=1) > marko_through:
            return
        olya_share, _ = split(amount)
        delta = olya_share - (amount if payer == "olya" else Decimal(0))
        if delta:
            adjustments.extend([
                f'{day} * "Розподіл спільної витрати"' + (' #fixed' if fixed else ''),
                f"  {category} {delta:.2f} PLN",
                f"  Equity:Розподіл-Спільного {-delta:.2f} PLN", ""])

    for index, entry in enumerate(entries):
        if not isinstance(entry, Transaction):
            continue
        for posting_index, posting in enumerate(entry.postings):
            if posting.account.startswith("Expenses:") and posting.units.currency == "PLN":
                month = entry.date.replace(day=1)
                paid_by_month[month] = paid_by_month.get(month, Decimal(0)) + posting.units.number
            if posting.account in SHARED_CATEGORIES and posting.units.currency == "PLN":
                add(f"olya:{index}:{posting_index}", entry.date, "olya",
                    posting.units.number, posting.account, "fixed" in entry.tags)
    for op in operations:
        category, fixed = marko.importer.classify(op, rules)
        if category in SHARED_CATEGORIES:
            add(f"marko:{op.source_id}", op.day, "marko", -op.amount, category, fixed)
    return {
        "since": since, "marko_start": opening + timedelta(days=1),
        "marko_through": marko_through, "olya_through": through,
        "expenses": expenses, "adjustments": "\n".join(adjustments),
        "paid_by_month": paid_by_month,
    }


def month_summary(context, month):
    return summarize_month(context["expenses"], (), month=month,
                           shared_since=context["since"])
