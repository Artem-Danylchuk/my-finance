"""Вигаданий другий CSV: суми, залишки, повторення й збереження при помилці."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core.data import Transaction

import marko


class MarkoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "examples").mkdir()
        self.source = self.root / "examples" / "marko.csv"
        self.source.write_bytes((marko.importer.ROOT / "examples" / "marko.csv").read_bytes())
        for name in ("household.json", "rules.csv"):
            (self.root / name).write_bytes((marko.importer.ROOT / name).read_bytes())

    def read(self):
        return marko.read_operations(self.source, opening_date=date(2026, 9, 30),
                                     opening_balance=Decimal("6200"), through=date(2026, 10, 15))

    def test_format_totals_categories_ownership_and_repeat_import(self):
        ops = self.read()
        self.assertEqual(len(ops), 16)
        self.assertEqual(sum((op.amount for op in ops if op.amount > 0), Decimal(0)),
                         Decimal("10500"))
        self.assertEqual(-sum((op.amount for op in ops if op.amount < 0), Decimal(0)),
                         Decimal("1253.69"))
        self.assertEqual(ops[-1].balance, Decimal("15446.31"))
        with contextlib.redirect_stdout(io.StringIO()):
            marko.generate_demo(self.root)
            output = self.root / "demo-marko.beancount"
            first = output.read_bytes()
            marko.generate_demo(self.root)
        self.assertEqual(output.read_bytes(), first)
        entries, errors, _ = loader.load_file(str(output))
        self.assertEqual(errors, [])
        transactions = [e for e in entries if isinstance(e, Transaction) and "owner" in e.meta]
        self.assertEqual(len(transactions), 16)
        self.assertTrue(all(e.meta["owner"] == "marko" for e in transactions))
        self.assertEqual(transactions[0].meta["source-id"], "DEMO-M001")
        self.assertIn("fixed", transactions[0].tags)
        self.assertFalse(any(p.account == "Assets:Банк" for e in transactions for p in e.postings))
        self.assertFalse((self.root / "transactions.beancount").exists())

    def test_duplicate_id_deduplication_and_conflict(self):
        lines = self.source.read_text().splitlines()
        self.source.write_text("\n".join([lines[0], lines[1], *lines[1:]]) + "\n")
        self.assertEqual(len(self.read()), 16)
        self.source.write_text("\n".join([*lines, lines[1].replace("ZTM", "Unknown")]) + "\n")
        with self.assertRaisesRegex(ValueError, "один ID"):
            self.read()

    def test_bad_balance_preserves_previous_demo_and_bad_currency_is_rejected(self):
        with contextlib.redirect_stdout(io.StringIO()):
            marko.generate_demo(self.root)
        output = self.root / "demo-marko.beancount"
        previous = output.read_bytes()
        original = self.source.read_text()
        self.source.write_text(original.replace("6090,00", "6091,00"))
        with self.assertRaisesRegex(SystemExit, "marko.csv:2.*залишок"):
            marko.generate_demo(self.root)
        self.assertEqual(output.read_bytes(), previous)
        self.source.write_text(original.replace(";PLN;", ";EUR;", 1))
        with self.assertRaisesRegex(ValueError, "PLN"):
            self.read()
        self.source.write_text(original)
        config_path = self.root / "household.json"
        config = json.loads(config_path.read_text())
        config["marko"]["demo"] = False
        config_path.write_text(json.dumps(config))
        with self.assertRaisesRegex(SystemExit, "вигаданих"):
            marko.generate_demo(self.root)
        self.assertEqual(output.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
