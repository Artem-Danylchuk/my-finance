"""Перевірки взаєморозрахунків на вигаданих даних, без зміни історії Олі."""

import unittest
from datetime import date
from decimal import Decimal as D

from shared import Compensation, SharedExpense, split, summarize_month


class SharedTests(unittest.TestCase):
    def expense(self, id, payer, amount, day=date(2026, 10, 5)):
        return SharedExpense(id, day, payer, D(amount), "Expenses:Продукти")

    def summary(self, expenses, payments=(), month=date(2026, 10, 1)):
        return summarize_month(expenses, payments, month=month,
                               shared_since=date(2026, 10, 1))

    def test_both_payers_and_compensation_are_counted_once(self):
        expenses = [self.expense("olya:1", "olya", "100.00"),
                    self.expense("marko:1", "marko", "40.00")]
        result = self.summary(expenses)
        self.assertEqual((result.total, result.olya.share, result.marko.share),
                         (D("140"), D("70"), D("70")))
        self.assertEqual((result.debtor, result.creditor, result.due),
                         ("marko", "olya", D("30")))
        payment = Compensation("transfer:1", date(2026, 10, 6), "marko", "olya", D("30"))
        settled = self.summary(expenses, [payment])
        self.assertEqual((settled.total, settled.olya, settled.marko),
                         (result.total, result.olya, result.marko))
        self.assertEqual((settled.due, settled.debtor, settled.creditor), (D(0), None, None))
        reverse = Compensation("transfer:2", date(2026, 10, 7), "olya", "marko", D("10"))
        self.assertEqual(self.summary(expenses, [payment, reverse]).due, D("10"))
        overpaid = Compensation("transfer:3", date(2026, 10, 8), "marko", "olya", D("40"))
        result = self.summary(expenses, [overpaid])
        self.assertEqual((result.debtor, result.creditor, result.due),
                         ("olya", "marko", D("10")))

    def test_rounding_refunds_and_distinct_bank_ids(self):
        self.assertEqual(split(D("0.01")), (D("0.01"), D("0.00")))
        self.assertEqual(split(D("-0.01")), (D("-0.01"), D("0.00")))
        expenses = [self.expense("olya:1", "olya", "10.01"),
                    self.expense("marko:1", "marko", "10.01"),
                    self.expense("olya:refund", "olya", "-10.01")]
        result = self.summary(expenses)
        self.assertEqual(result.total, D("10.01"))
        self.assertEqual(result.olya.share + result.marko.share, result.total)
        self.assertEqual((result.debtor, result.due), ("olya", D("5.01")))
        with self.assertRaisesRegex(ValueError, "Повторний"):
            self.summary([expenses[0], expenses[0]])

    def test_explicit_start_month_boundaries_and_no_invented_history(self):
        expenses = [self.expense("old", "olya", "100", date(2026, 9, 30)),
                    self.expense("new", "marko", "40", date(2026, 10, 1)),
                    self.expense("future", "olya", "200", date(2027, 10, 1))]
        result = self.summary(expenses)
        self.assertEqual((result.total, result.expense_count), (D("40"), 1))
        old = self.summary(expenses, month=date(2026, 9, 1))
        self.assertEqual((old.total, old.expense_count), (D(0), 0))
        midmonth = summarize_month(expenses, (), month=date(2026, 10, 1),
                                   shared_since=date(2026, 10, 15))
        self.assertEqual(midmonth.expense_count, 0)

    def test_invalid_money_participants_and_shared_category(self):
        for amount in (D("NaN"), D("Infinity"), D("1.001"), 1.25):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                split(amount)
        with self.assertRaises(ValueError):
            self.expense("id", "stranger", "10")
        with self.assertRaises(ValueError):
            SharedExpense("id", date(2026, 10, 1), "olya", D("10"), "Expenses:Кафе")
        for payer, recipient, amount in (("olya", "olya", "1"),
                                         ("marko", "olya", "0"),
                                         ("marko", "olya", "-1")):
            with self.assertRaises(ValueError):
                Compensation("id", date(2026, 10, 1), payer, recipient, D(amount))


if __name__ == "__main__":
    unittest.main()
