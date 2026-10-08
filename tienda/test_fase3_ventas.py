from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tienda.models import AlegraInvoiceStaging, Categoria, Cliente, Cotizacion, CotizacionItem, Producto, SyncAuditLog, Venta, VentaItem
from tienda.services.alegra_client import AlegraResponse
from tienda.services.alegra_invoice_import import AlegraInvoiceImporter


class FakeInvoiceClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def paged_get(self, path, *, limit, params):
        self.calls.append((path, limit, params))
        return [AlegraResponse(200, {"data": self.rows, "metadata": {"total": len(self.rows)}}, "https://example.test/invoices")]


class SalesTests(TestCase):
    def setUp(self):
        self.category = Categoria.objects.create(nombre="Ventas fase 3")
        self.product = Producto.objects.create(nombre="Producto venta", categoria=self.category, precio_base_unidad=Decimal("100"), activo=True)
        self.client_obj = Cliente.objects.create(nombre="Cliente venta", identificacion="900.123.456-7")
        self.user = get_user_model().objects.create_superuser(username="ventas-admin", password="test-pass")

    def test_direct_sale_and_item_keep_snapshot(self):
        sale = Venta.objects.create(cliente=self.client_obj, creado_por=self.user, responsable=self.user)
        VentaItem.objects.create(venta=sale, producto=self.product, descripcion="Snapshot inicial", cantidad=2, precio_unitario=Decimal("100"))
        self.product.nombre = "Producto cambiado"
        self.product.precio_base_unidad = Decimal("999")
        self.product.save()
        item = sale.items.get()
        self.assertEqual(item.descripcion, "Snapshot inicial")
        self.assertEqual(item.precio_unitario, Decimal("100.00"))
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("200.00"))

    def test_quote_conversion_is_idempotent_and_copies_items(self):
        quote = Cotizacion.objects.create(cliente=self.client_obj, titulo="Cotización aprobada", estado=Cotizacion.ESTADO_APROBADA, creada_por=self.user)
        CotizacionItem.objects.create(cotizacion=quote, producto=self.product, descripcion="Ítem aceptado", cantidad=2, valor_unitario=Decimal("100"))
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse("panel_cotizacion_convertir", kwargs={"cotizacion_id": quote.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Venta.objects.filter(cotizacion=quote).count(), 1)
        client.post(reverse("panel_cotizacion_convertir", kwargs={"cotizacion_id": quote.pk}))
        self.assertEqual(Venta.objects.filter(cotizacion=quote).count(), 1)
        self.assertEqual(Venta.objects.get(cotizacion=quote).items.count(), 1)

    def test_invoice_pagination_idempotence_and_client_match(self):
        rows = [{"id": "INV-1", "number": "FV-001", "date": "2026-01-02", "dueDate": "2026-02-01", "client": {"id": "C-1", "name": "Cliente venta", "identification": "9001234567"}, "subtotal": 100, "tax": 19, "total": 119, "balance": 119, "status": "open", "items": [{"name": "Producto venta"}]}]
        fake = FakeInvoiceClient(rows)
        first = AlegraInvoiceImporter(fake).sync(limit=30, actor=self.user)
        second = AlegraInvoiceImporter(fake).sync(limit=30, actor=self.user)
        invoice = AlegraInvoiceStaging.objects.get()
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(AlegraInvoiceStaging.objects.count(), 1)
        self.assertEqual(invoice.total, Decimal("119"))
        self.assertEqual(invoice.line_items[0]["name"], "Producto venta")
        self.assertEqual(invoice.matched_client_id, self.client_obj.id)
        self.assertEqual(SyncAuditLog.objects.filter(operation="sync_invoices").count(), 2)
        self.assertEqual(fake.calls[0][0], "/invoices")

    def test_invoice_api_error_preserves_existing_staging(self):
        class ErrorClient:
            def paged_get(self, *args, **kwargs):
                from tienda.services.alegra_client import AlegraError
                raise AlegraError("fallo controlado")
        result = AlegraInvoiceImporter(ErrorClient()).sync(limit=30, actor=self.user)
        self.assertEqual(result["total"], 0)
        self.assertEqual(AlegraInvoiceStaging.objects.count(), 0)
        self.assertEqual(SyncAuditLog.objects.get().result, SyncAuditLog.RESULT_ERROR)

    def test_invoice_link_requires_evidence_and_is_idempotent(self):
        invoice = AlegraInvoiceImporter(FakeInvoiceClient([{"id": "INV-2", "number": "FV-002", "client": {"identification": "9001234567"}}])).sync(limit=30)["total"]
        staging = AlegraInvoiceStaging.objects.get(external_id="INV-2")
        sale = Venta.objects.create(cliente=self.client_obj)
        client = Client()
        client.force_login(self.user)
        url = reverse("alegra_factura_vincular_venta", kwargs={"staging_id": staging.pk})
        client.post(url, {"venta_id": sale.pk})
        self.assertEqual(Venta.objects.get(pk=sale.pk).facturas_alegra.count(), 0)
        client.post(url, {"venta_id": sale.pk, "evidencia": "Orden interna 123"})
        client.post(url, {"venta_id": sale.pk, "evidencia": "Orden interna 123"})
        self.assertEqual(Venta.objects.get(pk=sale.pk).facturas_alegra.count(), 1)

    def test_invoice_sync_view_is_post_only_and_csrf_protected(self):
        client = Client()
        client.force_login(self.user)
        self.assertEqual(client.get(reverse("alegra_sync_invoices")).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        with patch("tienda.views.AlegraInvoiceImporter.sync", return_value={"total": 0, "created": 0, "updated": 0, "changed": 0, "errors": [], "pages": 0}):
            self.assertEqual(csrf_client.post(reverse("alegra_sync_invoices"), {"limit": 1}).status_code, 403)

    def test_sales_panel_and_reports_require_staff_and_keep_sources_separate(self):
        sale = Venta.objects.create(cliente=self.client_obj, total=Decimal("200"))
        client = Client()
        client.force_login(self.user)
        response = client.get(reverse("panel_ventas_informes"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ventas confirmadas")
        regular = get_user_model().objects.create_user(username="regular-sales", password="test-pass")
        client.force_login(regular)
        self.assertEqual(client.get(reverse("panel_ventas")).status_code, 403)
