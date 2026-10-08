from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tienda.models import Categoria, Cliente, Notificacion, Producto
from tienda.services.alegra_contact_import import AlegraContactReconciler
from tienda.services.alegra_import import AlegraItemImporter
from tienda.services.alegra_payloads import invoice_payload, payment_payload
from tienda.services.centro_control import _notify_once, refresh_control_alerts


class P2PayloadMinimizationTests(TestCase):
    def test_financial_payloads_keep_reconciliation_fields_without_secrets(self):
        invoice = invoice_payload(
            {
                "id": "INV-1",
                "number": "FV-1",
                "client": {"id": "C-1", "name": "Cliente", "identification": "900123"},
                "total": 100,
                "authorization": "secret",
                "headers": {"Authorization": "Bearer secret"},
            },
            [{"id": "I-1", "name": "Producto", "quantity": 1}],
        )
        payment = payment_payload(
            {
                "id": "PAY-1",
                "date": "2026-01-01",
                "client": {"id": "C-1", "identification": "900123"},
                "amount": 50,
                "headers": {"Authorization": "Bearer secret"},
            },
            ["INV-1"],
        )
        self.assertEqual(invoice["client"]["identification"], "900123")
        self.assertEqual(payment["invoice_ids"], ["INV-1"])
        self.assertNotIn("authorization", invoice)
        self.assertNotIn("headers", invoice)
        self.assertNotIn("headers", payment)


class P2BatchReconciliationTests(TestCase):
    def setUp(self):
        self.category = Categoria.objects.create(nombre="P2")
        self.product = Producto.objects.create(nombre="Producto P2", categoria=self.category)
        self.client = Cliente.objects.create(nombre="Cliente P2", identificacion="900-123")

    def test_product_index_uses_one_query_for_the_batch(self):
        with CaptureQueriesContext(connection) as queries:
            index = AlegraItemImporter._product_name_index()
        self.assertLessEqual(len(queries), 3)
        self.assertEqual(index["producto p2"][0].pk, self.product.pk)

    def test_contact_index_uses_one_query_for_the_batch(self):
        with CaptureQueriesContext(connection) as queries:
            index = AlegraContactReconciler._identification_index()
        self.assertEqual(len(queries), 1)
        self.assertEqual(index["900123"][0].pk, self.client.pk)


class P2AlertTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("p2-alert", password="test")

    def test_stable_event_prevents_duplicates_and_reopens_after_resolution(self):
        kwargs = {
            "user": self.user,
            "event_key": "control:test:1:user:1",
            "title": "Alerta",
            "message": "Revisar",
            "url": "/panel/",
        }
        self.assertTrue(_notify_once(**kwargs))
        self.assertFalse(_notify_once(**kwargs))
        self.assertEqual(Notificacion.objects.filter(event_key=kwargs["event_key"]).count(), 1)

        notification = Notificacion.objects.get(event_key=kwargs["event_key"])
        notification.estado = Notificacion.ESTADO_RESUELTA
        notification.resuelta_at = timezone.now()
        notification.save(update_fields=["estado", "resuelta_at"])
        self.assertTrue(_notify_once(**kwargs))
        notification.refresh_from_db()
        self.assertEqual(notification.estado, Notificacion.ESTADO_ABIERTA)
        self.assertFalse(notification.leida)

    def test_refresh_resolves_obsolete_control_events(self):
        notification = Notificacion.objects.create(
            usuario_destino=self.user,
            event_key="control:obsolete:1:user:%s" % self.user.pk,
            titulo="Obsoleta",
            mensaje="Ya no aplica",
        )
        refresh_control_alerts(self.user)
        notification.refresh_from_db()
        self.assertEqual(notification.estado, Notificacion.ESTADO_RESUELTA)
        self.assertTrue(notification.leida)
