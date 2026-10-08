from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tienda.models import AlegraInvoiceStaging, AlegraPaymentStaging, Cliente, CompromisoPago, ExternalSystem, SyncAuditLog
from tienda.services.alegra_client import AlegraResponse
from tienda.services.alegra_payment_import import AlegraPaymentImporter
from tienda.services.cartera import STATUS_INSUFFICIENT, STATUS_OVERDUE, STATUS_PAID, STATUS_PARTIAL, STATUS_PENDING, cartera_rows, cartera_summary


class FakePaymentClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def paged_get(self, path, *, limit, params):
        self.calls.append((path, limit, params))
        return [AlegraResponse(200, {"data": self.rows}, "https://example.test/payments")]


class CarteraTests(TestCase):
    def setUp(self):
        self.system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        self.client_a = Cliente.objects.create(nombre="Cliente cartera", identificacion="9001234567")
        self.client_b = Cliente.objects.create(nombre="Otro cliente", identificacion="8001234561")
        self.user = get_user_model().objects.create_superuser(username="cartera-admin", password="test-pass")

    def invoice(self, external_id, *, due, total=100, balance=None, status="open", client=None):
        return AlegraInvoiceStaging.objects.create(system=self.system, external_id=external_id, number=external_id, matched_client=client or self.client_a, issue_date=due - timedelta(days=30), due_date=due, total=Decimal(str(total)), balance=Decimal(str(balance)) if balance is not None else None, external_status=status, fetched_at=timezone.now())

    def test_cartera_states_and_aging_use_due_date(self):
        cutoff = date(2026, 1, 31)
        self.invoice("P", due=date(2026, 2, 5), balance=100)
        self.invoice("O", due=date(2026, 1, 1), balance=100)
        self.invoice("R", due=date(2026, 1, 1), total=100, balance=40)
        self.invoice("D", due=date(2026, 1, 1), total=100, balance=0)
        self.invoice("U", due=date(2026, 1, 1), balance=None)
        self.invoice("V", due=date(2026, 1, 1), balance=100, status="void")
        rows = {row["invoice"].external_id: row for row in cartera_rows(cutoff=cutoff)}
        self.assertEqual(rows["P"]["status"], STATUS_PENDING)
        self.assertEqual(rows["O"]["status"], STATUS_OVERDUE)
        self.assertEqual(rows["O"]["aging"], "1_30")
        self.assertEqual(rows["R"]["status"], STATUS_PARTIAL)
        self.assertEqual(rows["D"]["status"], STATUS_PAID)
        self.assertEqual(rows["U"]["status"], STATUS_INSUFFICIENT)
        self.assertNotIn("V", rows)
        summary = cartera_summary(cutoff=cutoff)
        self.assertEqual(summary["known_balance"], Decimal("240"))
        self.assertEqual(summary["overdue_balance"], Decimal("140"))

    def test_payment_sync_is_income_only_paged_and_idempotent(self):
        rows = [{"id": "PAY-1", "date": "2026-01-10", "client": {"id": "C-1", "name": "Cliente cartera", "identification": "9001234567"}, "amount": 40, "currency": {"code": "COP"}, "paymentMethod": "transfer", "invoices": [{"id": "INV-1"}]}]
        fake = FakePaymentClient(rows)
        first = AlegraPaymentImporter(fake).sync(limit=30, actor=self.user)
        second = AlegraPaymentImporter(fake).sync(limit=30, actor=self.user)
        payment = AlegraPaymentStaging.objects.get()
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(payment.invoice_external_ids, ["INV-1"])
        self.assertEqual(payment.matched_client_id, self.client_a.id)
        self.assertEqual(fake.calls[0][2]["type"], "in")
        self.assertEqual(SyncAuditLog.objects.filter(operation="sync_payments").count(), 2)

    def test_payment_error_does_not_delete_previous_rows(self):
        AlegraPaymentStaging.objects.create(system=self.system, external_id="OLD", amount=Decimal("10"), fetched_at=timezone.now())
        class ErrorClient:
            def paged_get(self, *args, **kwargs):
                from tienda.services.alegra_client import AlegraError
                raise AlegraError("error controlado")
        result = AlegraPaymentImporter(ErrorClient()).sync(limit=30, actor=self.user)
        self.assertEqual(result["total"], 0)
        self.assertTrue(AlegraPaymentStaging.objects.filter(external_id="OLD").exists())

    def test_commitment_validates_invoice_client_and_does_not_change_balance(self):
        invoice = self.invoice("INV", due=date(2026, 1, 1), balance=70)
        commitment = CompromisoPago(cliente=self.client_b, factura=invoice, responsable=self.user, creado_por=self.user, fecha_comprometida=date(2026, 2, 1), valor_comprometido=Decimal("70"))
        with self.assertRaises(ValidationError):
            commitment.full_clean()
        commitment.cliente = self.client_a
        commitment.full_clean()
        commitment.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.balance, Decimal("70"))

    def test_cartera_panel_requires_staff_and_uses_post_for_payment_sync(self):
        client = Client()
        regular = get_user_model().objects.create_user(username="regular-cartera", password="test-pass")
        client.force_login(regular)
        self.assertEqual(client.get(reverse("panel_cartera")).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(client.get(reverse("alegra_sync_payments")).status_code, 405)
