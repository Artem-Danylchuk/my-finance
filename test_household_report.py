"""Інтеграція двох джерел: частки, історія, приватні дані, помилки й покриття."""

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from household_report import load_context, month_summary
import report


class HouseholdReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "household.json"
        self.config.write_text(json.dumps({"shared_since": "2026-10-01", "marko": {
            "demo": True, "statement": "marko.csv", "opening_date": "2026-09-30",
            "opening_balance_pln": "100", "through": "2026-10-15"}}))
        self.csv = self.root / "marko.csv"
        self.csv.write_text('ID;Data księgowania;Opis;Kwota;Saldo;Waluta;Status\n'
                            'M1;02.10.2026;Lidl;-40,00;60,00;PLN;ZAKSIEGOWANA\n'
                            'M2;03.10.2026;Private merchant;-7,00;53,00;PLN;ZAKSIEGOWANA\n'
                            'M3;10.10.2026;Salary;1234,56;1287,56;PLN;ZAKSIEGOWANA\n')
        (self.root / "rules.csv").write_bytes((report.ROOT / "rules.csv").read_bytes())
        main = (report.ROOT / "main.beancount").read_text()
        transactions = '''
2026-09-02 * "History" #fixed
  Assets:Банк -100.00 PLN
  Expenses:Житло 100.00 PLN
2026-10-01 * "Rent" #fixed
  Assets:Банк -100.00 PLN
  Expenses:Житло 100.00 PLN
2026-10-02 * "Salary"
  Assets:Банк 200.00 PLN
  Income:Зарплата -200.00 PLN
2026-10-03 * "Personal taxi"
  Assets:Банк -10.00 PLN
  Expenses:Транспорт 10.00 PLN
2026-10-16 balance Assets:Банк 4490.00 PLN
'''
        self.ledger = self.root / "main.beancount"
        self.ledger.write_text(main.replace('include "transactions.beancount"', transactions))
        self.through = date(2026, 10, 15)

    def test_shared_personal_and_historical_numbers_use_saved_queries(self):
        context = load_context(self.ledger, self.config, self.through)
        summary = month_summary(context, date(2026, 10, 1))
        self.assertEqual((summary.total, summary.olya.share, summary.marko.share),
                         (D("140"), D("70"), D("70")))
        self.assertEqual((summary.debtor, summary.due), ("marko", D("30")))
        original = report.read_results(self.ledger)
        adjusted = report.read_results(self.ledger, context["adjustments"])
        for name in report.QUERY_NAMES:
            self.assertEqual([r for r in original[name] if r[0].month == 9],
                             [r for r in adjusted[name] if r[0].month == 9])
        october = adjusted[report.QUERY_NAMES[1]][1]
        self.assertEqual(october[1:5], (D("200"), D("80"), D("120"), D("60.0")))
        self.assertEqual(adjusted[report.QUERY_NAMES[2]][1][1:], (D("50"), D("30")))
        html = report.render_report(adjusted, self.through, context)
        self.assertIn("Марко → Оля: 30,00 zł", html)
        self.assertIn("Дані Марка вигадані", html)
        self.assertIn("Спільний облік ще не почався", html)
        self.assertIn("Неповний місяць", html)
        self.assertNotIn("Private merchant", html)
        self.assertNotIn("1\u00a0234,56", html)
        # The real bank balance assertion still passes after allocation.
        self.assertEqual(context["paid_by_month"][date(2026, 10, 1)], D("110"))

    def test_failed_second_source_preserves_report(self):
        output = self.root / "report.html"
        report.generate(self.ledger, output, self.through, household=self.config)
        previous = output.read_bytes()
        self.csv.write_text(self.csv.read_text().replace("60,00", "61,00"))
        with self.assertRaisesRegex(ValueError, "залишок"):
            report.generate(self.ledger, output, self.through, household=self.config)
        self.assertEqual(output.read_bytes(), previous)

    def test_different_coverage_and_missing_month_are_not_zero(self):
        config = json.loads(self.config.read_text())
        config["marko"]["through"] = "2026-10-10"
        self.config.write_text(json.dumps(config))
        context = load_context(self.ledger, self.config, self.through)
        html = report.render_shared(context, [date(2026, 10, 1), date(2026, 11, 1)])
        self.assertIn("спільне покриття до 10.10.2026", html)
        self.assertIn("Олі до 15.10.2026; Марка до 10.10.2026", html)
        self.assertIn("Загальний підсумок не визначено", html)


if __name__ == "__main__":
    unittest.main()
