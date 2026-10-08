from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tienda.models import AlegraInvoiceStaging, Cliente, ExternalSystem, SyncAuditLog
from tienda.services.sync_freshness import financial_freshness, source_freshness


class P1BFreshnessTests(TestCase):
    def setUp(self):
        self.system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        self.client_record = Cliente.objects.create(nombre="Cliente frescura")
        self.invoice = AlegraInvoiceStaging.objects.create(system=self.system, external_id="FRESH-1", number="FRESH-1", matched_client=self.client_record, fetched_at=timezone.now())
        self.user = get_user_model().objects.create_superuser(username="freshness-admin", password="pass")

    def audit(self, resource, result, *, complete=True, when=None):
        log = SyncAuditLog.objects.create(
            system=self.system,
            operation=f"sync_{resource}",
            resource=resource,
            actor=self.user,
            result=result,
            detail="Prueba de frescura",
            metadata={"complete": complete},
        )
        if when:
            SyncAuditLog.objects.filter(pk=log.pk).update(created_at=when)
            log.created_at = when
        return log

    def test_never_synced_is_not_definitive(self):
        state = source_freshness("invoices")
        self.assertEqual(state["state"], "never")
        self.assertFalse(state["is_definitive"])

    def test_recent_complete_sync_is_current(self):
        self.audit("invoices", SyncAuditLog.RESULT_SUCCESS)
        state = source_freshness("invoices")
        self.assertEqual(state["state"], "current")
        self.assertTrue(state["is_definitive"])

    @override_settings(ALEGRA_FINANCIAL_FRESHNESS_MINUTES=30)
    def test_old_complete_sync_is_stale(self):
        self.audit("invoices", SyncAuditLog.RESULT_SUCCESS, when=timezone.now() - timedelta(minutes=31))
        self.assertEqual(source_freshness("invoices")["state"], "stale")

    def test_latest_failed_or_partial_attempt_is_visible(self):
        self.audit("invoices", SyncAuditLog.RESULT_SUCCESS, when=timezone.now() - timedelta(minutes=5))
        self.audit("invoices", SyncAuditLog.RESULT_PARTIAL, complete=False)
        self.assertEqual(source_freshness("invoices")["state"], "partial")
        self.audit("payments", SyncAuditLog.RESULT_ERROR, complete=False)
        self.assertEqual(source_freshness("payments")["state"], "failed")

    def test_invoice_and_payment_freshness_are_independent(self):
        self.audit("invoices", SyncAuditLog.RESULT_SUCCESS)
        status = financial_freshness()
        self.assertTrue(status["invoices"]["is_current"])
        self.assertEqual(status["payments"]["state"], "never")
        self.assertFalse(status["current"])
        self.audit("payments", SyncAuditLog.RESULT_SUCCESS)
        status = financial_freshness()
        self.assertEqual(status["invoices"]["state"], "current")
        self.assertEqual(status["payments"]["state"], "current")
        self.assertTrue(status["current"])

    @override_settings(ALEGRA_FINANCIAL_FRESHNESS_MINUTES=30)
    def test_payments_current_does_not_refresh_stale_invoices(self):
        self.audit("invoices", SyncAuditLog.RESULT_SUCCESS, when=timezone.now() - timedelta(minutes=31))
        self.audit("payments", SyncAuditLog.RESULT_SUCCESS)
        status = financial_freshness()
        self.assertEqual(status["invoices"]["state"], "stale")
        self.assertEqual(status["payments"]["state"], "current")
        self.assertFalse(status["current"])

    def test_financial_views_show_warning_when_sources_are_not_verified(self):
        self.client.force_login(self.user)
        urls = [
            reverse("panel_cartera"),
            reverse("panel_ventas_informes"),
            reverse("panel_centro_control"),
            reverse("panel_cartera_cliente", args=[self.client_record.id]),
            reverse("alegra_facturas_catalogo"),
            reverse("alegra_factura_detalle", args=[self.invoice.id]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "No existe una sincronización completa")

    def test_local_development_security_keeps_http_functional(self):
        self.assertEqual(settings.DEPLOYMENT_ENV, "development")
        self.assertFalse(settings.SECURE_SSL_REDIRECT)
        self.assertFalse(settings.SESSION_COOKIE_SECURE)
        self.assertFalse(settings.CSRF_COOKIE_SECURE)
        self.assertTrue(settings.SITE_URL.startswith(("http://", "https://")))
