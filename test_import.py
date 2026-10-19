"""Synthetic statements only: never modifies data/."""

import contextlib
import csv
import importlib
import io
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from beancount import loader

importer = importlib.import_module("import")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "data").mkdir()
        (self.root / "rules.csv").write_text(
            "шаблон,рахунок,фіксована\nBolt Food,Expenses:Доставка-Їжі,ні\n"
            "Bolt,Expenses:Транспорт,ні\nZTM,Expenses:Транспорт,так\n"
            "Salary,Income:Зарплата,ні\n", encoding="utf-8")
        self.rules = importer.read_rules(self.root / "rules.csv")
        self.rows = [
            ["2026-08-31 20:00:00", 'Bolt Food "обід"', "-10.00", "4490.00"],
            ["2026-08-31 09:00:00", "ZTM", "-20.00", "4470.00"],
            ["2026-09-01 10:00:00", "Salary", "100.00", "4570.00"],
            ["2026-10-02 10:00:00", "Unknown", "-5.00", "4565.00"],
        ]

    def statement(self, name, rows):
        with (self.root / "data" / name).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["Completed Date", "Description", "Amount", "Balance",
                             "Currency", "State", "Fee"])
            for row in rows:
                writer.writerow([*row, "PLN", "COMPLETED", "0.00"])

    def test_overlaps_preserve_bank_order_and_month_end_balances(self):
        # Later statement sorts first by filename; the shared row restores bank order.
        self.statement("a.csv", self.rows[1:])
        self.statement("z.csv", self.rows[:3])
        ops = importer.read_operations(self.root / "data")
        self.assertEqual([op.description for op in ops], [row[1] for row in self.rows])
        self.assertEqual(len(ops), 4)
        content = importer.render(ops, self.rules)
        for assertion in ["2026-09-01 balance Assets:Банк 4470.00 PLN",
                          "2026-10-01 balance Assets:Банк 4570.00 PLN",
                          "2026-10-03 balance Assets:Банк 4565.00 PLN"]:
            self.assertIn(assertion, content)
        main = (importer.ROOT / "main.beancount").read_text(encoding="utf-8")
        ledger = main.replace('include "transactions.beancount"', content)
        _, errors, _ = loader.load_string(ledger)
        self.assertEqual(errors, [])
        _, bad_errors, _ = loader.load_string(ledger.replace("4470.00 PLN", "4471.00 PLN"))
        self.assertTrue(bad_errors, "A wrong bank balance must fail validation")
        self.assertEqual(importer.classify(ops[0], self.rules), ("Expenses:Доставка-Їжі", False))
        self.assertEqual(importer.classify(ops[1], self.rules), ("Expenses:Транспорт", True))
        self.assertEqual(importer.classify(ops[3], self.rules), ("Expenses:Без-Категорії", False))
        unmatched_income = replace(ops[3], amount=Decimal("5.00"))
        self.assertEqual(importer.classify(unmatched_income, self.rules), ("Income:Інше", False))

    def test_repeat_import_and_failed_input_leave_previous_output_intact(self):
        self.statement("statement.csv", self.rows)
        output = self.root / "transactions.beancount"
        with patch.object(importer, "ROOT", self.root), contextlib.redirect_stdout(io.StringIO()):
            importer.main()
            first = output.read_bytes()
            importer.main()
            self.assertEqual(output.read_bytes(), first)
            bad = [row[:] for row in self.rows]
            bad[0][2] = "invalid"
            self.statement("statement.csv", bad)
            with self.assertRaisesRegex(SystemExit, "statement.csv:2"):
                importer.main()
            self.assertEqual(output.read_bytes(), first)

    def test_empty_and_conflicting_statements_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "немає CSV"):
            importer.read_operations(self.root / "data")
        self.statement("a.csv", self.rows[:2])
        self.statement("b.csv", list(reversed(self.rows[:2])))
        with self.assertRaisesRegex(ValueError, "суперечливий порядок"):
            importer.read_operations(self.root / "data")


if __name__ == "__main__":
    unittest.main()
