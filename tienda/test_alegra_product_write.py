import json
import os
from unittest.mock import patch
from urllib.error import URLError

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from tienda.models import (
    AlegraProductWriteOperation,
    Categoria,
    ExternalObjectMap,
    ExternalSystem,
    Producto,
)
from tienda.services.alegra_product_write import (
    ProductSyncBlocked,
    build_item_payload,
    enqueue_product_sync,
    process_pending_product_operations,
)


class FakeResponse:
    status = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.data).encode()


class ProductWriteTests(TestCase):
    def setUp(self):
        self.system = ExternalSystem.objects.create(code="alegra", name="Alegra")
        self.category = Categoria.objects.create(nombre="Pruebas")

    def product(self, **kwargs):
        defaults = {
            "nombre": "Producto de prueba",
            "categoria": self.category,
            "tipo_calculo": Producto.CALCULO_UNIDAD,
            "precio_base_unidad": "125.00",
        }
        defaults.update(kwargs)
        return Producto.objects.create(**defaults)

    def test_payload_is_allowlisted_and_uses_unit_price(self):
        product = self.product(unspsc_id=None)
        payload = build_item_payload(product)
        self.assertEqual(payload["type"], "product")
        self.assertEqual(payload["price"], [{"price": 125.0}])
        self.assertNotIn("inventory", payload)
        self.assertNotIn("category", payload)
        self.assertNotIn("unspsc", payload)

    def test_manual_without_catalog_price_is_blocked_without_zero_payload(self):
        product = self.product(tipo_calculo=Producto.CALCULO_MANUAL, precio_base_unidad="0", precio_base_m2="0")
        with self.assertRaises(ProductSyncBlocked):
            build_item_payload(product)
        operation = enqueue_product_sync(product)
        self.assertEqual(operation.state, AlegraProductWriteOperation.STATE_BLOCKED)
        self.assertEqual(operation.payload, {})

    def test_area_product_can_use_positive_technical_catalog_price(self):
        product = self.product(tipo_calculo=Producto.CALCULO_AREA, precio_base_m2="80.00", precio_base_unidad="0")
        payload = build_item_payload(product)
        self.assertEqual(payload["price"], [{"price": 80.0}])

    def test_update_payload_omits_catalog_price(self):
        product = self.product()
        payload = build_item_payload(product, include_price=False)
        self.assertNotIn("price", payload)

    def test_save_intention_is_idempotent_for_same_payload(self):
        product = self.product()
        first = enqueue_product_sync(product)
        second = enqueue_product_sync(product)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(AlegraProductWriteOperation.objects.filter(product=product).count(), 1)

    def test_dry_run_never_calls_transport(self):
        product = self.product()
        enqueue_product_sync(product)
        with patch("tienda.services.alegra_product_write.AlegraWriteClient") as client:
            result = process_pending_product_operations(execute=False)
        client.assert_not_called()
        self.assertEqual(result["pending"], 1)

    @patch.dict(os.environ, {
        "ALEGRA_EMAIL": "test@example.test",
        "ALEGRA_API_TOKEN": "token",
        "ALEGRA_AUTOMATION_WRITES_ENABLED": "true",
        "ALEGRA_EXTERNAL_WRITES_ENABLED": "true",
    }, clear=False)
    def test_create_uses_post_and_creates_map_after_confirmed_id(self):
        product = self.product()
        operation = enqueue_product_sync(product)
        with patch("tienda.services.alegra_write.urlopen", return_value=FakeResponse({"id": "A-ITEM-1"})) as opener:
            result = process_pending_product_operations(execute=True)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraProductWriteOperation.STATE_SYNCED, operation.error_message)
        self.assertEqual(result["results"][0]["state"], AlegraProductWriteOperation.STATE_SYNCED)
        self.assertEqual(opener.call_args.args[0].method, "POST")
        self.assertEqual(ExternalObjectMap.objects.filter(resource_type="items", external_id="A-ITEM-1", object_id=product.pk).count(), 1)
        self.assertEqual(AlegraProductWriteOperation.objects.get(pk=operation.pk).attempts, 1)

    @patch.dict(os.environ, {
        "ALEGRA_EMAIL": "test@example.test",
        "ALEGRA_API_TOKEN": "token",
        "ALEGRA_AUTOMATION_WRITES_ENABLED": "true",
        "ALEGRA_EXTERNAL_WRITES_ENABLED": "true",
    }, clear=False)
    def test_timeout_becomes_reconciliation_without_retry(self):
        product = self.product()
        operation = enqueue_product_sync(product)
        with patch("tienda.services.alegra_write.urlopen", side_effect=URLError("timeout")):
            result = process_pending_product_operations(execute=True)
        operation.refresh_from_db()
        self.assertEqual(result["results"][0]["state"], AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(operation.state, AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(operation.attempts, 1)

    @patch.dict(os.environ, {
        "ALEGRA_EMAIL": "test@example.test",
        "ALEGRA_API_TOKEN": "token",
        "ALEGRA_AUTOMATION_WRITES_ENABLED": "true",
        "ALEGRA_EXTERNAL_WRITES_ENABLED": "true",
    }, clear=False)
    def test_update_requires_active_map_and_uses_put(self):
        product = self.product()
        ct = ContentType.objects.get_for_model(Producto)
        ExternalObjectMap.objects.create(
            system=self.system, resource_type="items", external_id="A-ITEM-2",
            content_type=ct, object_id=product.pk, status=ExternalObjectMap.STATUS_ACTIVE,
        )
        operation = enqueue_product_sync(product)
        self.assertEqual(operation.operation, AlegraProductWriteOperation.OP_UPDATE)
        with patch("tienda.services.alegra_write.urlopen", return_value=FakeResponse({"id": "A-ITEM-2"})) as opener:
            process_pending_product_operations(execute=True)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraProductWriteOperation.STATE_SYNCED, operation.error_message)
        self.assertEqual(opener.call_args.args[0].method, "PUT")
        self.assertEqual(AlegraProductWriteOperation.objects.get(pk=operation.pk).state, AlegraProductWriteOperation.STATE_SYNCED)
