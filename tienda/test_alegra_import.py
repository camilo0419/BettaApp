from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tienda.models import (
    AlegraItemStaging,
    Categoria,
    ExternalObjectMap,
    Producto,
    SyncAuditLog,
)
from tienda.services.alegra_client import AlegraResponse
from tienda.services.alegra_import import AlegraItemImporter


class FakeAlegraClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def paged_get(self, path, *, limit, params):
        self.calls.append((path, limit, params))
        return [AlegraResponse(200, self.rows, "https://example.test/items")]


class AlegraImportTests(TestCase):
    def setUp(self):
        self.category = Categoria.objects.create(nombre="Importados")
        self.rows = [{
            "id": "A-1",
            "name": "Lona exterior",
            "reference": "LONA-01",
            "description": "Descripción externa",
            "category": {"id": "C-1", "name": "Lonas"},
            "status": "active",
            "type": "product",
            "price": [{"main": True, "price": 125000}],
        }]

    def test_pagination_and_idempotence(self):
        fake = FakeAlegraClient(self.rows)
        importer = AlegraItemImporter(fake)
        first = importer.sync(limit=30)
        second = importer.sync(limit=30)
        self.assertEqual(first["total"], 1)
        self.assertEqual(second["total"], 1)
        self.assertEqual(AlegraItemStaging.objects.count(), 1)
        self.assertEqual(SyncAuditLog.objects.filter(operation="sync_items").count(), 2)
        self.assertEqual(fake.calls[0][2]["mode"], "advanced")

    def test_duplicate_local_names_are_conflicts(self):
        Producto.objects.create(nombre="Lona exterior", categoria=self.category)
        Producto.objects.create(nombre="Lona exterior", categoria=self.category)
        result = AlegraItemImporter(FakeAlegraClient(self.rows)).sync(limit=30)
        staging = AlegraItemStaging.objects.get()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(staging.review_status, AlegraItemStaging.REVIEW_CONFLICT)

    def test_import_requires_decisions_and_keeps_product_inactive(self):
        importer = AlegraItemImporter(FakeAlegraClient(self.rows))
        importer.sync(limit=30)
        staging = AlegraItemStaging.objects.get()
        product = importer.import_item(staging.pk, category_id=self.category.pk, calculation_type=Producto.CALCULO_UNIDAD)
        self.assertFalse(product.activo)
        self.assertTrue(product.requiere_revision)
        self.assertEqual(product.precio_base_unidad, 0)
        self.assertEqual(ExternalObjectMap.objects.count(), 1)
        self.assertEqual(staging.refresh_from_db(), None)
        self.assertEqual(staging.review_status, AlegraItemStaging.REVIEW_IMPORTED)

    def test_import_can_remain_pending_without_category_or_calculation(self):
        importer = AlegraItemImporter(FakeAlegraClient(self.rows))
        importer.sync(limit=30)
        staging = AlegraItemStaging.objects.get()
        product = importer.import_item(staging.pk)
        self.assertFalse(product.activo)
        self.assertIsNone(product.categoria)
        self.assertEqual(product.tipo_calculo, "")
        self.assertTrue(product.requiere_revision)
        self.assertEqual(ExternalObjectMap.objects.count(), 1)

    def test_imported_product_can_be_classified_later(self):
        importer = AlegraItemImporter(FakeAlegraClient(self.rows))
        importer.sync(limit=30)
        staging = AlegraItemStaging.objects.get()
        product = importer.import_item(staging.pk)
        updated = importer.classify_imported_item(
            staging.pk, category_id=self.category.pk, calculation_type=Producto.CALCULO_UNIDAD,
        )
        updated.refresh_from_db()
        self.assertEqual(updated.pk, product.pk)
        self.assertEqual(updated.categoria_id, self.category.pk)
        self.assertEqual(updated.tipo_calculo, Producto.CALCULO_UNIDAD)
        self.assertFalse(updated.requiere_revision)
        self.assertFalse(updated.activo)

    def test_manual_link_does_not_change_existing_product(self):
        product = Producto.objects.create(nombre="Lona local", categoria=self.category, activo=True, precio_base_unidad=90000)
        importer = AlegraItemImporter(FakeAlegraClient(self.rows))
        importer.sync(limit=30)
        importer.link_item(AlegraItemStaging.objects.get().pk, product.pk)
        product.refresh_from_db()
        self.assertTrue(product.activo)
        self.assertEqual(product.precio_base_unidad, 90000)
        self.assertEqual(ExternalObjectMap.objects.count(), 1)

    def test_api_error_is_audited_without_local_items(self):
        class ErrorClient:
            def paged_get(self, path, **kwargs):
                from tienda.services.alegra_client import AlegraError
                raise AlegraError("fallo controlado")

        result = AlegraItemImporter(ErrorClient()).sync(limit=30)
        self.assertEqual(result["total"], 0)
        self.assertEqual(AlegraItemStaging.objects.count(), 0)
        self.assertEqual(SyncAuditLog.objects.get().result, SyncAuditLog.RESULT_ERROR)


class AlegraPanelSecurityTests(TestCase):
    def test_non_staff_cannot_access_panel(self):
        user = get_user_model().objects.create_user(username="client", password="test-pass")
        self.client.force_login(user)
        response = self.client.get(reverse("alegra_integraciones"))
        self.assertEqual(response.status_code, 403)

    @patch("tienda.views.AlegraItemImporter.sync")
    def test_sync_requires_post_and_csrf(self, sync):
        user = get_user_model().objects.create_superuser(username="admin", email="admin@example.com", password="test-pass")
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        response = client.get(reverse("alegra_sync_items"))
        self.assertEqual(response.status_code, 405)
        response = client.post(reverse("alegra_sync_items"), {"limit": 1})
        self.assertEqual(response.status_code, 403)
