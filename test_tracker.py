"""Assignment acceptance tests: bank controls, durable cash and server privacy."""

from datetime import date
from decimal import Decimal
from pathlib import Path
import re
import secrets
import tempfile
import unittest

from werkzeug.security import generate_password_hash

import cash
import homework
import marko
from tracker import create_app


class StatementTests(unittest.TestCase):
    def test_official_statement_controls_and_bank_order(self):
        rows, start, through, initial = marko.read_homework_statement(
            homework.ROOT / "fixtures" / "marko-statement.csv")
        self.assertEqual((len(rows), start, through, initial),
                         (109, date(2026, 8, 1), date(2026, 10, 15), Decimal("2860.40")))
        self.assertEqual(rows[-1].balance, Decimal("11212.47"))
        all_rows = homework.records()
        for owner, months in homework.CONTROL.items():
            for month, amount in months.items():
                self.assertEqual(homework.monthly(all_rows, owner, date(2026, month, 1))["bank_variable"], amount)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "marko.csv"
            source = (homework.ROOT / "fixtures" / "marko-statement.csv").read_text()
            path.write_text(source.replace("11 212,47", "11 212,48", 1))
            with self.assertRaisesRegex(ValueError, "залишок"):
                marko.read_homework_statement(path)


class TrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.passwords = {owner: secrets.token_urlsafe(20) for owner in ("olya", "marko")}
        cls.hashes = {owner: generate_password_hash(password) for owner, password in cls.passwords.items()}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = {"SECRET_KEY": secrets.token_urlsafe(40), "PASSWORDS": self.hashes,
                       "DATABASE": str(Path(self.temp.name) / "tracker.sqlite3"),
                       "LOCAL_ONLY": True, "TESTING": True}
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.base = "http://127.0.0.1"

    def get(self, path, client=None):
        return (client or self.client).get(path, base_url=self.base)

    def post(self, path, data, client=None):
        return (client or self.client).post(path, data=data, base_url=self.base)

    @staticmethod
    def field(response, name):
        return re.search(r'name="' + name + r'" value="([^"]+)"', response.get_data(as_text=True))[1]

    def login(self, owner, client=None):
        client = client or self.client
        page = self.get("/login", client)
        return self.post("/login", {"username": owner, "password": self.passwords[owner],
                                   "csrf": self.field(page, "csrf")}, client)

    def payload(self, page, **changes):
        data = {"csrf": self.field(page, "csrf"), "id": self.field(page, "id"),
                "day": "2026-10-15", "description": "Тестова готівка 1 PLN", "amount": "1,00",
                "category": "Expenses:Продукти", "shared": "personal"}
        data.update(changes)
        return data

    def test_anonymous_has_no_finances_or_exports(self):
        for path in ("/", "/cash.beancount"):
            response = self.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertNotIn("2432", response.get_data(as_text=True))
        for path in ("/fixtures/marko-statement.csv", "/.env.local", "/marko.beancount"):
            self.assertEqual(self.get(path).status_code, 404)
        self.assertEqual(self.get("/login").status_code, 200)
        self.assertEqual(self.post("/cash", {}).status_code, 403)

    def test_cash_survives_reload_restart_and_duplicate_submission(self):
        self.assertEqual(self.login("marko").status_code, 303)
        page = self.get("/")
        store = self.app.extensions["cash_store"]
        before = homework.monthly(homework.records() + store.records(), "marko", date(2026, 10, 1))
        data = self.payload(page)
        for _ in range(2):
            self.assertEqual(self.post("/cash", data).status_code, 303)
        self.assertEqual(len(store.records()), 1)
        after = homework.monthly(homework.records() + store.records(), "marko", date(2026, 10, 1))
        self.assertEqual(after["expenses"], before["expenses"] + 1)
        self.assertEqual(after["variable"], before["variable"] + 1)
        self.assertEqual(after["bank_variable"], before["bank_variable"])
        restarted = create_app(self.config).test_client()
        self.login("marko", restarted)
        page = self.get("/", restarted)
        self.assertIn("Тестова готівка 1 PLN", page.get_data(as_text=True))
        exported = self.get("/cash.beancount", restarted).get_data(as_text=True)
        homework.validate_ledger(exported)
        self.assertIn("1.00 PLN", exported)

    def test_owner_is_server_selected_and_partner_private_cash_is_absent(self):
        self.login("marko")
        data = self.payload(self.get("/"), description="МАРКО-ПРИВАТНИЙ-ЗАПИС", owner="olya")
        self.post("/cash", data)
        self.assertEqual(self.app.extensions["cash_store"].records()[0].owner, "marko")
        other = self.app.test_client()
        self.login("olya", other)
        for path in ("/?owner=marko", "/cash.beancount"):
            text = self.get(path, other).get_data(as_text=True)
            self.assertNotIn("МАРКО-ПРИВАТНИЙ-ЗАПИС", text)
        self.assertIn("2\u00a0195,21", self.get("/", other).get_data(as_text=True))

    def test_forged_csrf_bad_money_and_nonshared_category_do_not_write(self):
        self.login("marko")
        data = self.payload(self.get("/"))
        self.assertEqual(self.post("/cash", {**data, "csrf": "forged"}).status_code, 403)
        for changes in ({"amount": "NaN"}, {"amount": "-1"}, {"amount": "0.001"},
                        {"shared": "shared", "category": "Expenses:Транспорт"},
                        {"day": "1990-01-01"}):
            self.assertEqual(self.post("/cash", {**data, **changes}).status_code, 303)
        self.assertEqual(self.app.extensions["cash_store"].records(), [])

    def test_shared_cash_updates_settlement_and_cannot_be_replayed_with_changed_amount(self):
        self.login("marko")
        store = self.app.extensions["cash_store"]
        before = homework.shared_month(homework.records(), date(2026, 10, 1))
        data = self.payload(self.get("/"), shared="shared")
        self.post("/cash", data)
        after = homework.shared_month(homework.records() + store.records(), date(2026, 10, 1))
        self.assertEqual(after.total, before.total + 1)
        self.assertEqual(after.marko.paid, before.marko.paid + 1)
        self.post("/cash", {**data, "amount": "2"})
        self.assertEqual(store.records()[0].amount, Decimal("1"))
        homework.validate_ledger(cash.ledger(store.records()))

    def test_production_enforces_https_host_and_cookie_security(self):
        app = create_app({**self.config, "LOCAL_ONLY": False, "PUBLIC_HOST": "finance.example.invalid"})
        client = app.test_client()
        http = client.get("/", base_url="http://finance.example.invalid", headers={"X-Forwarded-Proto": "https"})
        self.assertEqual(http.status_code, 308)
        self.assertTrue(http.headers["Location"].startswith("https://finance.example.invalid/"))
        self.assertEqual(client.post("/login", base_url="http://finance.example.invalid").status_code, 400)
        secure = client.get("/login", base_url="https://finance.example.invalid")
        cookie = secure.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(flag, cookie)
        self.assertEqual(client.get("/login", base_url="https://evil.example").status_code, 400)

    def test_login_requires_csrf_and_throttles_password_guessing(self):
        self.assertEqual(self.post("/login", {"username": "marko", "password": "wrong"}).status_code, 403)
        page = self.get("/login")
        data = {"username": "marko", "password": "wrong", "csrf": self.field(page, "csrf")}
        for _ in range(10):
            self.assertEqual(self.post("/login", data).status_code, 401)
        self.assertEqual(self.post("/login", data).status_code, 429)


if __name__ == "__main__":
    unittest.main()
