from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tienda.models import AlegraInvoiceStaging, Cliente, ExternalSystem
from tienda.services.cartera import (
    STATUS_INSUFFICIENT,
    STATUS_OVERDUE,
    STATUS_PAID,
    STATUS_PARTIAL,
    cartera_summary,
    facturacion_summary,
    invoice_cartera_row,
)


class FinancialIntegrityP0Tests(TestCase):
    def setUp(self):
        self.system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        self.client_a = Cliente.objects.create(nombre="Cliente P0", identificacion="9001234567")
        self.admin = get_user_model().objects.create_superuser(username="p0-admin", password="pass")

    def invoice(self, external_id, *, due, total=100, balance=100, status="open"):
        return AlegraInvoiceStaging.objects.create(
            system=self.system,
            external_id=external_id,
            number=external_id,
            matched_client=self.client_a,
            issue_date=due - timedelta(days=30) if due else date(2026, 1, 1),
            due_date=due,
            total=Decimal(str(total)) if total is not None else None,
            balance=Decimal(str(balance)) if balance is not None else None,
            external_status=status,
            fetched_at=timezone.now(),
        )

    def test_partial_payment_is_not_overdue_when_due_date_is_future(self):
        invoice = self.invoice("PARTIAL-CURRENT", due=date(2026, 2, 15), total=100, balance=40)
        row = invoice_cartera_row(invoice, cutoff=date(2026, 1, 31))
        self.assertEqual(row["status"], STATUS_PARTIAL)
        self.assertEqual(row["payment_status"], STATUS_PARTIAL)
        self.assertEqual(row["due_status"], "pendiente")
        self.assertFalse(row["is_overdue"])
        self.assertEqual(cartera_summary(cutoff=date(2026, 1, 31))["overdue_balance"], Decimal("0"))

    def test_partial_payment_is_overdue_only_after_due_date(self):
        invoice = self.invoice("PARTIAL-OVERDUE", due=date(2026, 1, 1), total=100, balance=40)
        row = invoice_cartera_row(invoice, cutoff=date(2026, 1, 31))
        summary = cartera_summary(cutoff=date(2026, 1, 31))
        self.assertEqual(row["status"], STATUS_PARTIAL)
        self.assertEqual(row["due_status"], STATUS_OVERDUE)
        self.assertTrue(row["is_overdue"])
        self.assertEqual(summary["overdue_balance"], Decimal("40"))

    def test_paid_invoice_has_no_overdue_balance(self):
        invoice = self.invoice("PAID", due=date(2026, 1, 1), total=100, balance=0)
        row = invoice_cartera_row(invoice, cutoff=date(2026, 1, 31))
        self.assertEqual(row["status"], STATUS_PAID)
        self.assertEqual(row["due_status"], "no_vencida")
        self.assertFalse(row["is_overdue"])

    def test_missing_balance_or_due_date_is_not_classified_as_overdue(self):
        no_balance = self.invoice("NO-BALANCE", due=date(2026, 1, 1), balance=None)
        no_due = self.invoice("NO-DUE", due=None, total=100, balance=40)
        no_balance_row = invoice_cartera_row(no_balance, cutoff=date(2026, 1, 31))
        no_due_row = invoice_cartera_row(no_due, cutoff=date(2026, 1, 31))
        self.assertEqual(no_balance_row["status"], STATUS_INSUFFICIENT)
        self.assertFalse(no_balance_row["is_overdue"])
        self.assertEqual(no_due_row["due_status"], STATUS_INSUFFICIENT)
        self.assertEqual(no_due_row["aging"], "sin_informacion")
        self.assertFalse(no_due_row["is_overdue"])

    def test_void_draft_and_unknown_are_excluded_without_deleting_staging(self):
        for status in ("void", "draft", "unknown"):
            self.invoice(status.upper(), due=date(2026, 1, 1), total=100, balance=100, status=status)
        summary = cartera_summary(cutoff=date(2026, 1, 31))
        billing = facturacion_summary()
        self.assertEqual(summary["known_balance"], Decimal("0"))
        self.assertEqual(summary["overdue_balance"], Decimal("0"))
        self.assertEqual(len(summary["excluded_documents"]), 3)
        self.assertEqual(billing["reportable_count"], 0)
        self.assertEqual(billing["eligible_total"], Decimal("0"))
        self.assertEqual(AlegraInvoiceStaging.objects.count(), 3)

    def test_billing_summary_and_panels_exclude_nonverifiable_documents(self):
        valid = self.invoice("VALID", due=date(2026, 2, 1), total=100, balance=100, status="open")
        self.invoice("VOID", due=date(2026, 1, 1), total=900, balance=900, status="void")
        self.invoice("DRAFT", due=date(2026, 1, 1), total=800, balance=800, status="draft")
        self.invoice("UNKNOWN", due=date(2026, 1, 1), total=700, balance=700, status="unknown")
        self.client.force_login(self.admin)
        report = self.client.get(reverse("panel_ventas_informes"))
        dashboard = self.client.get(reverse("panel_cartera"))
        detail = self.client.get(reverse("alegra_factura_detalle", args=[valid.id]))
        self.assertEqual(report.status_code, 200)
        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(report, "Facturas Alegra verificables")
        self.assertContains(report, "Documentos excluidos de indicadores definitivos")
        self.assertContains(dashboard, "Documentos excluidos de cartera activa")
        self.assertContains(detail, "Estado de vencimiento")

