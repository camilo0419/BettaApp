from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from tienda.management.commands.alegra_validacion_real import Command
from tienda.services.alegra_client import AlegraReadOnlyClient, AlegraResponse


class AlegraRealValidationTests(SimpleTestCase):
    def test_analysis_is_aggregated_and_does_not_require_models_or_persistence(self):
        rows = [{
            "id": "1",
            "date": "2026-01-01",
            "dueDate": "2026-01-31",
            "status": "closed",
            "client": {"id": "C-1", "name": "Cliente", "identification": "900-1"},
            "subtotal": 100,
            "tax": 19,
            "total": 119,
            "balance": 0,
            "items": [],
        }]
        response = SimpleNamespace(status=200, data={"data": rows})
        result = Command._analyze("facturas", "/invoices", {}, [response], rows, 5)
        self.assertEqual(result["records_examined"], 1)
        self.assertEqual(result["details"]["statuses"], {"closed": 1})
        self.assertEqual(result["details"]["financial_eligibility"], {"eligible": 1})
        self.assertEqual(result["details"]["aging"], {"no_aplica_sin_saldo": 1})

    def test_sample_limit_is_marked_as_non_exhaustive(self):
        result = Command._analyze("productos", "/items", {}, [SimpleNamespace(status=200, data={"data": [{"id": "1"}]})], [{"id": "1"}], 1)
        self.assertTrue(result["limit_is_sample_only"])

    def test_reconciliation_supports_multiple_allocations_and_missing_references(self):
        result = Command._reconcile(
            [{"id": "I-1", "status": "closed"}, {"id": "I-2", "status": "open"}],
            [
                {"id": "P-1", "invoices": [{"id": "I-1", "amount": 40}, {"id": "I-2", "amount": 60}]},
                {"id": "P-2", "invoices": [{"id": "I-1", "amount": 10}]},
                {"id": "P-3", "invoices": [{"id": "I-404", "amount": 5}]},
            ],
        )
        self.assertEqual(result["invoice_payment_relations"], 3)
        self.assertEqual(result["payments_with_multiple_invoices"], 1)
        self.assertEqual(result["invoices_with_multiple_payments"], 1)
        self.assertEqual(result["unmatched_invoice_references"], 1)
        self.assertTrue(result["application_reconstructible_from_response"])

    def test_reconciliation_counts_multiple_payments_for_one_invoice(self):
        result = Command._directed_payment_analysis(
            [
                {"id": "P-1", "date": "2026-01-01", "amount": 40, "invoices": [{"id": "I-1", "amount": 40}]},
                {"id": "P-2", "date": "2026-01-02", "amount": 60, "invoices": [{"id": "I-1", "amount": 60}]},
            ],
            [{"id": "I-1", "date": "2025-12-01"}],
            {},
        )
        self.assertEqual(result["invoices_with_multiple_payments"], 1)
        self.assertEqual(result["exact_applications"], 0)

    @patch("tienda.services.alegra_client.time.sleep")
    def test_pagination_honors_max_pages_without_short_page_assumption(self, sleep):
        client = object.__new__(AlegraReadOnlyClient)
        client.get = Mock(side_effect=[
            AlegraResponse(200, {"data": [{"id": "1"}]}, "https://example.test"),
            AlegraResponse(200, {"data": [{"id": "2"}]}, "https://example.test"),
        ])
        responses = client.paged_get("/invoices", limit=100, max_pages=2, pause=0.01, stop_on_short_page=False)
        self.assertEqual(len(responses), 2)
        self.assertEqual(client.get.call_count, 2)
        sleep.assert_called_once_with(0.01)
