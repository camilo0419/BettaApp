from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from tienda.models import Categoria, Producto, AlegraItemStaging, AlegraWriteOperation, Cliente, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import SafeAlegraWriteAdapter, PENDING
from tienda.services.alegra_import import AlegraItemImporter
from tienda.services.alegra_operation_queue import enqueue_create, enqueue_missing_creates, enqueue_update_if_changed, process_pending_operations
from tienda.services.alegra_operation_queue import alegra_system


class Response:
    status = 200
    data = {"data": [{"id": "I-1", "name": "Producto remoto", "reference": "REF-1", "type": "product", "price": [{"price": "12.50", "main": True}]}]}


class ItemsClient:
    def __init__(self):
        self.calls = []

    def paged_get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return [Response()]


class AutomationTests(TestCase):
    def test_product_dry_run_only_updates_staging(self):
        categoria = Categoria.objects.create(nombre="Catálogo prueba")
        Producto.objects.create(nombre="Local existente", categoria=categoria, precio_base_m2="1")
        result = AlegraItemImporter(client=ItemsClient()).sync(limit=30, max_pages=2)
        self.assertEqual(result["total"], 1)
        self.assertEqual(AlegraItemStaging.objects.count(), 1)
        self.assertEqual(Producto.objects.count(), 1)
        self.assertEqual(AlegraItemStaging.objects.first().reference, "REF-1")

    def test_product_outbound_adapter_is_simulation_only(self):
        plan = SafeAlegraWriteAdapter().create_item({"name": "P"}, idempotency_key="k")
        self.assertEqual(plan.state, PENDING)
        self.assertTrue(plan.simulated)
        self.assertIn("simulada", plan.reason)

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_new_client_queues_after_empty_remote_check(self, transport_class):
        transport_class.return_value.find_candidates.return_value = []
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Persona", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="123456789", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        operation = enqueue_create(client)
        self.assertIsNotNone(operation)
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 1)
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        transport_class.return_value.find_candidates.assert_called_once_with(identification="123456789")

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_new_client_with_remote_candidate_is_not_queued(self, transport_class):
        transport_class.return_value.find_candidates.return_value = [{"id": "A-1"}]
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Persona", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="123456790", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        self.assertIsNone(enqueue_create(client))
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 0)

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_remote_check_failure_does_not_queue_create(self, transport_class):
        transport_class.return_value.find_candidates.side_effect = TimeoutError("timeout")
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Persona", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="123456791", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        self.assertIsNone(enqueue_create(client))
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 0)

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_historical_pending_clients_are_excluded_but_new_clients_are_eligible(self, transport_class):
        transport_class.return_value.find_candidates.return_value = []
        historical = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Histórico pendiente", primer_nombre="Histórico",
            primer_apellido="Pendiente", identificacion="123456792", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME", alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
        )
        new_client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Cliente nuevo", primer_nombre="Cliente",
            primer_apellido="Nuevo", identificacion="123456793", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        queued = enqueue_missing_creates(limit=10)
        self.assertEqual(len(queued), 1)
        self.assertEqual(AlegraWriteOperation.objects.get(pk=queued[0]).client_id, new_client.pk)
        self.assertFalse(AlegraWriteOperation.objects.filter(client=historical).exists())
        self.assertIsNone(enqueue_create(historical))

    def test_outbound_processor_is_dry_run_by_default(self):
        result = process_pending_operations(execute=False)
        self.assertEqual(result["mode"], "dry_run")
        self.assertEqual(result["results"], [])

    def test_outbound_command_reports_dry_run_mode(self):
        output = StringIO()
        call_command("alegra_procesar_operaciones", stdout=output)
        self.assertIn("Modo dry-run; no se ejecutaron escrituras.", output.getvalue())

    def test_local_edit_without_baseline_stays_local_and_is_audited(self):
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Persona", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="123456789", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        system = alegra_system()
        from django.contrib.contenttypes.models import ContentType
        ExternalObjectMap.objects.create(
            system=system, resource_type="contacts", external_id="C-1",
            content_type=ContentType.objects.get_for_model(Cliente), object_id=client.pk,
            metadata={"source": "import"},
        )
        self.assertIsNone(enqueue_update_if_changed(client))
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 0)
