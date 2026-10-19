"""Розподіл підтверджених спільних витрат; не змінює банківський облік.

Суми тут — витрати (додатні), повернення покупки — від’ємні.
Викликати після категоризації через rules.csv та визначення власника.
Результат охоплює лише передані записи, не підтверджує повноту виписок.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP


PEOPLE = ("olya", "marko")
SHARED_CATEGORIES = frozenset({"Expenses:Житло", "Expenses:Продукти"})
CENT = Decimal("0.01")
ZERO = Decimal("0.00")


def validate_amount(amount):
    if (not isinstance(amount, Decimal) or not amount.is_finite()
            or amount != amount.quantize(CENT)):
        raise ValueError("Сума має бути Decimal у PLN з точністю до гроша")


@dataclass(frozen=True)
class SharedExpense:
    # ID includes the source account: identical purchases at different banks are distinct.
    id: str
    day: date
    payer: str
    amount: Decimal
    category: str

    def __post_init__(self):
        validate_amount(self.amount)
        if not self.id.strip() or self.payer not in PEOPLE:
            raise ValueError("Потрібні ідентифікатор операції та відомий платник")
        if self.category not in SHARED_CATEGORIES:
            raise ValueError("Спільними можуть бути лише підтверджені оренда та продукти")


@dataclass(frozen=True)
class Compensation:
    id: str
    day: date
    payer: str
    recipient: str
    amount: Decimal

    def __post_init__(self):
        validate_amount(self.amount)
        if (not self.id.strip() or self.payer not in PEOPLE
                or self.recipient not in PEOPLE or self.payer == self.recipient
                or self.amount <= ZERO):
            raise ValueError("Компенсація — додатний переказ між різними учасниками")


@dataclass(frozen=True)
class PersonShare:
    paid: Decimal
    share: Decimal


@dataclass(frozen=True)
class SharedMonth:
    total: Decimal
    olya: PersonShare
    marko: PersonShare
    expense_count: int
    compensation_count: int
    debtor: str | None
    creditor: str | None
    due: Decimal


def split(amount):
    """Олі округлюємо половину, Маркові — залишок; так само для повернень."""
    validate_amount(amount)
    olya = (amount / 2).quantize(CENT, rounding=ROUND_HALF_UP)
    return olya, amount - olya


def summarize_month(expenses, compensations, *, month, shared_since):
    """Місячний взаєморозрахунок без перенесення боргу інших місяців.

    Дату переходу передають явно. Старі покупки не перерозподіляються.
    Особисті покупки сюди не передаються; однакові ID — помилка джерела.
    Компенсації впливають лише на борг, не на витрати чи частки.
    """
    if month.day != 1:
        raise ValueError("Місяць задається першим числом")
    expenses, compensations = tuple(expenses), tuple(compensations)
    ids = [entry.id for entry in (*expenses, *compensations)]
    if len(set(ids)) != len(ids):
        raise ValueError("Повторний ідентифікатор операції")

    def selected(entry):
        return entry.day >= shared_since and entry.day.replace(day=1) == month

    paid = dict.fromkeys(PEOPLE, ZERO)
    shares = dict.fromkeys(PEOPLE, ZERO)
    expense_count = compensation_count = 0
    for entry in expenses:
        if selected(entry):
            expense_count += 1
            paid[entry.payer] += entry.amount
            olya, marko = split(entry.amount)
            shares["olya"] += olya
            shares["marko"] += marko
    # Positive = Marko owes Olya. A payment from Marko reduces that balance.
    balance = paid["olya"] - shares["olya"]
    for entry in compensations:
        if selected(entry):
            compensation_count += 1
            balance += entry.amount if entry.payer == "olya" else -entry.amount
    return SharedMonth(
        total=sum(paid.values(), ZERO),
        olya=PersonShare(paid["olya"], shares["olya"]),
        marko=PersonShare(paid["marko"], shares["marko"]),
        expense_count=expense_count, compensation_count=compensation_count,
        debtor="marko" if balance > ZERO else "olya" if balance < ZERO else None,
        creditor="olya" if balance > ZERO else "marko" if balance < ZERO else None,
        due=abs(balance),
    )
