"""Перевірки інтеграцій без реальних AI-запитів і повідомлень."""

import io
import json
import unittest
from datetime import date
from unittest.mock import patch

import ai_features as ai
import telegram_report as telegram
import test_household_report as fixtures


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.HouseholdReportTests.setUp(self)

    def test_assistant_computes_personal_taxi_and_protects_other_person(self):
        self.ledger.write_text(self.ledger.read_text().replace('Personal taxi', 'Uber Warszawa'))
        filters = {"supported": True, "month": "2026-10", "scope": "personal",
                   "kind": "taxi", "category": None}
        with patch.object(ai, "structured", return_value=filters) as model:
            result = ai.answer_question("Скільки я витратила на таксі у жовтні?",
                                        through=self.through, root=self.root)
        self.assertEqual(result["amount_pln"], "10.00")
        self.assertNotIn("Private merchant", str(model.call_args))
        self.assertNotIn("1234.56", str(model.call_args))
        with patch.object(ai, "structured", return_value={**filters, "scope": "shared"}):
            result = ai.answer_question("Скільки ми витратили на таксі?", through=self.through, root=self.root)
        self.assertEqual(result["amount_pln"], "0")
        self.assertIn("таксі", result["answer"].lower())
        with patch.object(ai, "structured", return_value={**filters, "supported": False}):
            result = ai.answer_question("Яка зарплата Марка?", through=self.through, root=self.root)
        self.assertIn("закриті", result["answer"])
        with patch.object(ai, "structured", return_value={**filters, "month": "2025-01"}):
            result = ai.answer_question("Таксі у січні 2025", through=self.through, root=self.root)
        self.assertNotIn("amount_pln", result)

    def test_category_and_receipt_are_only_validated_drafts(self):
        before = (self.root / "rules.csv").read_bytes()
        with patch.object(ai, "structured", return_value={"category": "Expenses:Кафе", "confidence": "low", "reason": "Кафе"}):
            proposed = ai.propose_category("New Cafe", self.root)
        self.assertEqual(proposed["status"], "proposal_requires_confirmation")
        self.assertEqual((self.root / "rules.csv").read_bytes(), before)
        image = self.root / "receipt.png"
        image.write_bytes(b'\x89PNG\r\n\x1a\n' + b'synthetic')
        valid = {"date": "2026-10-01", "amount": "10.01", "currency": "PLN", "merchant": "Cafe"}
        with patch.object(ai, "structured", return_value=valid.copy()):
            self.assertEqual(ai.receipt_draft(image, today=self.through)["amount"], "10.01")
        for values in ({**valid, "amount": None}, {**valid, "currency": "EUR"},
                       {**valid, "amount": "NaN"}, {**valid, "date": "2027-01-01"}):
            with self.subTest(values=values), patch.object(ai, "structured", return_value=values), self.assertRaises(ValueError):
                ai.receipt_draft(image, today=self.through)
        self.assertFalse((self.root / "transactions.beancount").exists())

    def test_local_transport_has_no_cloud_fallback_and_validates_output(self):
        fields = ai.schema({"category": {"type": "string"}})
        config = {"AI_PROVIDER": "ollama", "AI_MODEL": "installed-local-model"}
        def setting(name, default=None):
            return config.get(name, default)
        response = {"done": True, "done_reason": "stop", "message": {"content": '{"category":"Expenses:Кафе"}'}}
        with patch.object(ai, "setting", side_effect=setting), patch.object(ai, "urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as network:
            result = ai.structured("instructions", "merchant", fields)
        self.assertEqual(result["category"], "Expenses:Кафе")
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
        self.assertNotIn("Authorization", request.headers)
        self.assertFalse(json.loads(request.data)["stream"])
        config["AI_PROVIDER"] = "lmstudio"
        lm_result = {"choices": [{"finish_reason": "stop", "message": {"content": '{"category":"Expenses:Кафе"}'}}]}
        with patch.object(ai, "setting", side_effect=setting), patch.object(ai, "urlopen", return_value=io.BytesIO(json.dumps(lm_result).encode())) as network:
            self.assertEqual(ai.structured("instructions", "merchant", fields)["category"], "Expenses:Кафе")
        self.assertEqual(network.call_args.args[0].full_url, "http://127.0.0.1:1234/v1/chat/completions")
        self.assertEqual(json.loads(network.call_args.args[0].data)["response_format"]["type"], "json_schema")
        config["AI_BASE_URL"] = "https://external.example"
        with patch.object(ai, "setting", side_effect=setting), patch.object(ai, "urlopen") as network, self.assertRaises(ValueError):
            ai.structured("instructions", "merchant", fields)
        network.assert_not_called()
        with self.assertRaises(ValueError):
            ai.validate_output({"category": ["bad"]}, fields)

    def test_openai_transport_and_missing_credentials(self):
        fields = ai.schema({"answer": {"type": "string"}})
        config = {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "fake-test-key", "OPENAI_MODEL": "configured-model"}
        result = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"answer":"ok"}'}]}]}
        with patch.object(ai, "setting", side_effect=lambda name, default=None: config.get(name, default)), patch.object(ai, "urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as network:
            self.assertEqual(ai.structured("system", "question", fields), {"answer": "ok"})
        payload = json.loads(network.call_args.args[0].data)
        self.assertFalse(payload["store"])
        self.assertTrue(payload["text"]["format"]["strict"])
        with patch.object(ai, "setting", side_effect=lambda name, default=None: "openai" if name == "AI_PROVIDER" else None), patch.object(ai, "urlopen") as network, self.assertRaises(ValueError):
            ai.structured("system", "question", fields)
        network.assert_not_called()

    def test_telegram_preview_is_shared_only_and_send_is_not_repeated(self):
        text, complete = telegram.preview(date(2026, 10, 1), self.through, self.root)
        self.assertFalse(complete)
        self.assertIn("140,00", text)
        self.assertIn("30,00", text)
        self.assertNotIn("1234", text)
        self.assertNotIn("Private merchant", text)
        settings = {"TELEGRAM_BOT_TOKEN": "fake", "TELEGRAM_CHAT_ID": "123"}
        with patch.object(telegram, "setting", side_effect=lambda name: settings[name]), patch.object(telegram, "urlopen", return_value=io.BytesIO(b'{"ok":true}')) as network:
            telegram.send(text, date(2026, 10, 1), self.root)
            with self.assertRaisesRegex(ValueError, "дубль"):
                telegram.send(text, date(2026, 10, 1), self.root)
        self.assertEqual(network.call_count, 1)


if __name__ == '__main__':
    unittest.main()
