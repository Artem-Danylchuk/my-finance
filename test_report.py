"""Report tests use synthetic ledgers and temporary output files."""

import tempfile
import unittest
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import report


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.select_options = {}
        self.current_select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "select":
            self.current_select = attrs.get("id")
            self.select_options[self.current_select] = []
        elif tag == "option" and self.current_select is not None:
            self.select_options[self.current_select].append(attrs)

    def handle_endtag(self, tag):
        if tag == "select":
            self.current_select = None


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ledger = self.root / "main.beancount"
        main = (report.ROOT / "main.beancount").read_text(encoding="utf-8")
        transactions = '''
2026-08-01 * "Income"
  Assets:Банк 100.00 PLN
  Income:Зарплата -100.00 PLN
2026-08-03 * "Fixed expense" #fixed
  Assets:Банк -40.00 PLN
  Expenses:Житло 40.00 PLN
2026-09-15 * "Unknown expense"
  Assets:Банк -10.00 PLN
  Expenses:Без-Категорії 10.00 PLN
'''
        self.ledger.write_text(main.replace('include "transactions.beancount"', transactions),
                               encoding="utf-8")

    def test_saved_queries_numbers_offline_markup_and_months(self):
        results = report.read_results(self.ledger)
        html = report.render_report(results, date(2026, 9, 15))
        self.assertIn("60,0%", html)
        self.assertIn("60,00 zł", html)
        self.assertIn("Неповний місяць · дані до 15.09.2026", html)
        self.assertIn("норму заощаджень не визначено", html)
        self.assertIn("-10,00 zł", html)
        self.assertIn("Повний місяць · до 31.08.2026", html)
        self.assertIn("Без категорії — 10,00 zł", html)
        elements = Elements(html).elements
        panels = [attrs for tag, attrs in elements if tag == "section"]
        self.assertEqual(len(panels), 2)
        self.assertIn("hidden", panels[0])
        self.assertNotIn("hidden", panels[1])
        self.assertEqual(sum(tag == "article" for tag, _ in elements), 6)
        self.assertEqual(len(Elements(html).select_options["month"]), 2)
        self.assertFalse(any("src" in attrs or "href" in attrs for _, attrs in elements))
        self.assertIn("connect-src 'self'", html)
        self.assertIn("fetch('/api/ask'", html)
        # Coverage can extend beyond the last purchase: do not infer partiality from it.
        full = report.render_report(results, date(2026, 9, 30))
        self.assertNotIn("Неповний місяць", full)
        with self.assertRaisesRegex(ValueError, "раніша"):
            report.render_report(results, date(2026, 9, 14))

    def test_validation_failure_preserves_previous_report(self):
        output = self.root / "report.html"
        report.generate(self.ledger, output, date(2026, 9, 15))
        previous = output.read_bytes()
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write("\n2026-09-16 balance Assets:Банк 999999.00 PLN\n")
        with self.assertRaisesRegex(ValueError, "Облік містить помилки"):
            report.generate(self.ledger, output, date(2026, 9, 15))
        self.assertEqual(output.read_bytes(), previous)

    def test_empty_month_and_escaped_category(self):
        results = report.read_results(self.ledger)
        results[report.QUERY_NAMES[0]][0] = (
            date(2026, 8, 1), "Expenses:<script>alert(1)</script>", report.Decimal("40"))
        html = report.render_report(results, date(2026, 9, 15))
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        results[report.QUERY_NAMES[0]] = []
        results[report.QUERY_NAMES[1]] = [
            (date(2026, 8, 1), report.ZERO, report.ZERO, report.ZERO, None, date(2026, 8, 10))]
        results[report.QUERY_NAMES[2]] = []
        html = report.render_report(results, date(2026, 8, 31))
        self.assertIn("У цьому місяці витрат немає", html)
        self.assertIn("частка не визначена", html)


if __name__ == "__main__":
    unittest.main()
