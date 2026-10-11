from io import StringIO
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tienda.models import Categoria, Producto, AlegraItemStaging, AlegraWriteOperation, Cliente, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import SafeAlegraWriteAdapter, PENDING
from tienda.services.alegra_import import AlegraItemImporter
from tienda.services.alegra_operation_queue import enqueue_create, enqueue_missing_creates, enqueue_update_if_changed, process_pending_operations, linked_client_update_candidates
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
    def _contact_map(self, client, external_id, metadata):
        from django.contrib.contenttypes.models import ContentType
        return ExternalObjectMap.objects.create(
            system=alegra_system(), resource_type="contacts", external_id=external_id,
            content_type=ContentType.objects.get_for_model(Cliente), object_id=client.pk,
            status=ExternalObjectMap.STATUS_ACTIVE, metadata=metadata,
        )

    def _linked_client(self, name, identification):
        return Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre=name, primer_nombre="Ana",
            primer_apellido=name, identificacion=identification, tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME", email=f"{identification}@example.test",
        )

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
        operation = enqueue_create(client)
        self.assertIsNotNone(operation)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 1)

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_remote_check_failure_persists_create_with_backoff(self, transport_class):
        transport_class.return_value.find_candidates.side_effect = TimeoutError("timeout")
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Persona", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="123456791", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        operation = enqueue_create(client)
        self.assertIsNotNone(operation)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        self.assertEqual(operation.error_code, "remote_check")
        self.assertIn("retry_at", operation.result_metadata)

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

    @patch("tienda.management.commands.alegra_diagnosticar_clientes_historicos.AlegraWriteClient")
    def test_historical_diagnostic_reports_aggregate_without_personal_data(self, transport_class):
        alegra_system()
        Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Nombre reservado", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="12.345", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME", alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
        )
        transport_class.return_value.find_candidates.return_value = [{
            "id": "A-1", "identificationObject": {"type": "CC", "number": "12345"},
        }]
        output = StringIO()
        call_command("alegra_diagnosticar_clientes_historicos", "--all", stdout=output)
        text = output.getvalue()
        self.assertIn("inequivoca_identificacion_y_documento: 1", text)
        self.assertNotIn("Nombre reservado", text)
        self.assertNotIn("12.345", text)

    @patch("tienda.management.commands.alegra_diagnosticar_clientes_historicos.AlegraWriteClient")
    def test_historical_diagnostic_supports_single_local_id(self, transport_class):
        alegra_system()
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="No debe mostrarse", primer_nombre="Ana",
            primer_apellido="Prueba", identificacion="777", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME", alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
        )
        transport_class.return_value.find_candidates.return_value = []
        output = StringIO()
        call_command("alegra_diagnosticar_clientes_historicos", "--client-id", client.pk, stdout=output)
        text = output.getvalue()
        self.assertIn(f"ID local={client.pk}", text)
        self.assertIn("clasificacion=sin_coincidencia", text)
        self.assertIn("candidatos=0", text)
        self.assertNotIn("No debe mostrarse", text)

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_enqueue_missing_skips_future_backoff_and_reaches_next_client(self, transport_class):
        transport_class.return_value.find_candidates.return_value = []
        first = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Primero", primer_nombre="Ana",
            primer_apellido="Uno", identificacion="101", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        second = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Segundo", primer_nombre="Ana",
            primer_apellido="Dos", identificacion="102", tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
        )
        first_operation = enqueue_create(first)
        first_operation.result_metadata = {"retry_at": (timezone.now() + timedelta(hours=1)).isoformat()}
        first_operation.save(update_fields=["result_metadata"])
        transport_class.return_value.find_candidates.reset_mock()
        queued = enqueue_missing_creates(limit=1)
        self.assertEqual(len(queued), 1)
        self.assertEqual(AlegraWriteOperation.objects.get(pk=queued[0]).client_id, second.pk)
        transport_class.return_value.find_candidates.assert_called_once()

    def test_process_dry_run_respects_future_and_expired_backoff(self):
        for index, retry_at in enumerate((
            timezone.now() + timedelta(hours=1),
            timezone.now() - timedelta(minutes=1),
        ), start=1):
            client = Cliente.objects.create(
                tipo_cliente=Cliente.TIPO_PERSONA, nombre=f"Cliente {index}", primer_nombre="Ana",
                primer_apellido=f"Prueba{index}", identificacion=f"20{index}",
                tipo_identificacion=Cliente.ID_CC, regimen_tributario="COMMON_REGIME",
            )
            AlegraWriteOperation.objects.create(
                system=alegra_system(), client=client, operation=AlegraWriteOperation.OP_CREATE,
                idempotency_key=f"backoff-{index}", state=AlegraWriteOperation.STATE_PENDING,
                result_metadata={"retry_at": retry_at.isoformat()},
            )
        result = process_pending_operations(limit=10, execute=False)
        self.assertEqual(result["pending"], 1)

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_linked_updates_continue_after_missing_baseline(self, transport_class):
        first = self._linked_client("Primero", "301")
        second = self._linked_client("Segundo", "302")
        self._contact_map(first, "C-1", {"source": "alegra_initial_import"})
        self._contact_map(second, "C-2", {
            "source": "alegra_write",
            "last_synced_fields": {"nombre": "Segundo", "identificacion": "302", "tipo_identificacion": "cc", "email": "old@example.test"},
        })
        second.email = "new@example.test"
        second.save(update_fields=["email"])
        transport_class.return_value.get_contact.return_value = {
            "id": "C-2", "name": "Segundo", "identificationObject": {"type": "CC", "number": "302"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME", "email": "old@example.test",
            "type": ["client"], "status": "active",
        }
        result = linked_client_update_candidates(limit=1)
        self.assertEqual(result["prepared"], ["preview:C-2"])
        self.assertIn({"external_id": "C-1", "reason": "missing_baseline"}, result["diagnostics"])
        transport_class.return_value.get_contact.assert_called_once_with("C-2")

    def test_baseline_info_distinguishes_missing_partial_and_confirmed(self):
        from tienda.services.alegra_inbound_sync import InboundClientSyncService
        self.assertEqual(InboundClientSyncService.baseline_info({})[0], "missing")
        self.assertEqual(InboundClientSyncService.baseline_info({"last_synced_fields": {}})[0], "incomplete")
        self.assertEqual(InboundClientSyncService.baseline_info({
            "source": "alegra_write", "last_synced_fields": {"nombre": "Cliente"},
        })[0], "confirmed")
        self.assertEqual(InboundClientSyncService.baseline_info({
            "baseline_source": "alegra_get_reconciliation",
            "last_confirmed": {"nombre": "Cliente"},
            "baseline_protected": {"regime": "COMMON_REGIME"},
        })[0], "incomplete")

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_linked_updates_use_stable_cursor_across_multiple_missing_baselines(self, transport_class):
        clients = [self._linked_client(f"Cliente {index}", f"40{index}") for index in range(3)]
        for index, client in enumerate(clients, start=1):
            self._contact_map(client, f"C-{index}", {"source": "alegra_initial_import"})
        transport_class.return_value.get_contact.assert_not_called()
        result = linked_client_update_candidates(limit=1)
        self.assertEqual(result["prepared"], [])
        self.assertEqual(len([item for item in result["diagnostics"] if item["reason"] == "missing_baseline"]), 3)
        transport_class.return_value.get_contact.assert_not_called()
