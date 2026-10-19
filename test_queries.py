"""Check saved query calculations with synthetic transactions."""

import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

import beanquery
from beancount import loader
from beancount.core.data import Query


class QueryTests(unittest.TestCase):
    def test_months_signs_zero_income_and_fixed_split(self):
        main = Path(__file__).with_name("main.beancount").read_text(encoding="utf-8")
        synthetic = '''
2026-08-01 * "Income"
  Assets:Банк 100.00 PLN
  Income:Зарплата -100.00 PLN
2026-08-02 * "Fixed expense" #fixed
  Assets:Банк -40.00 PLN
  Expenses:Житло 40.00 PLN
2026-08-20 * "Unknown expense"
  Assets:Банк -80.00 PLN
  Expenses:Без-Категорії 80.00 PLN
2026-09-01 * "Expense without income"
  Assets:Банк -10.00 PLN
  Expenses:Продукти 10.00 PLN
2027-08-10 * "Income next year"
  Assets:Банк 50.00 PLN
  Income:Зарплата -50.00 PLN
'''
        entries, errors, options = loader.load_string(
            main.replace('include "transactions.beancount"', synthetic))
        self.assertEqual(errors, [])
        connection = beanquery.connect("beancount:", entries=entries, errors=errors, options=options)
        queries = {entry.name: entry.query_string for entry in entries if isinstance(entry, Query)}
        self.assertEqual(len(queries), 4)

        def run(name):
            return connection.execute(queries[name]).fetchall()

        savings = run("Норма заощаджень")
        self.assertEqual(savings, [
            (date(2026, 8, 1), Decimal("100"), Decimal("120"), Decimal("-20"),
             Decimal("-20.0"), date(2026, 8, 20)),
            (date(2026, 9, 1), Decimal("0"), Decimal("10"), Decimal("-10"),
             None, date(2026, 9, 1)),
            (date(2027, 8, 1), Decimal("50"), Decimal("0"), Decimal("50"),
             Decimal("100.0"), date(2027, 8, 10)),
        ])
        self.assertEqual(run("Фіксовані і змінні"), [
            (date(2026, 8, 1), Decimal("40"), Decimal("80")),
            (date(2026, 9, 1), Decimal("0"), Decimal("10")),
        ])
        self.assertEqual(run("Без категорії"), [
            (date(2026, 8, 1), "Unknown expense", 1, Decimal("80")),
        ])
        categories = run("Витрати за категоріями")
        totals = {}
        for month, _, amount in categories:
            totals[month] = totals.get(month, Decimal(0)) + amount
        self.assertEqual(totals, {date(2026, 8, 1): Decimal("120"),
                                  date(2026, 9, 1): Decimal("10")})


if __name__ == "__main__":
    unittest.main()
