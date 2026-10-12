from io import StringIO
from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from tienda.models import Categoria, Producto, AlegraItemStaging, AlegraWriteOperation, Cliente, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import SafeAlegraWriteAdapter, PENDING
from tienda.services.alegra_import import AlegraItemImporter
from tienda.services.alegra_operation_queue import enqueue_create, enqueue_missing_creates, enqueue_update_if_changed, process_pending_operations, linked_client_update_candidates
from tienda.services.alegra_operation_queue import alegra_system
from tienda.services.alegra_write import AlegraContactWriteService, authorize_external_write


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

    def test_repeated_create_enqueue_reuses_single_pending_operation(self):
        client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA, nombre="Persona repetida",
            primer_nombre="Ana", primer_apellido="Prueba", identificacion="123456790",
            tipo_identificacion=Cliente.ID_CC, regimen_tributario="COMMON_REGIME",
            alegra_sync_policy=Cliente.ALEGRA_SYNC_AUTO,
        )

        first = enqueue_create(client, defer_remote_check=True)
        client.email = "actualizado@example.test"
        client.save(update_fields=["email"])
        second = enqueue_create(client, defer_remote_check=True)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            AlegraWriteOperation.objects.filter(
                client=client, operation=AlegraWriteOperation.OP_CREATE,
            ).count(),
            1,
        )

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_form_create_persists_without_remote_get(self, transport_class):
        staff = get_user_model().objects.create_user(
            username="staff-fast-create", password="Test123!", is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.post(
            "/panel/clientes/crear/",
            {
                "tipo_cliente": Cliente.TIPO_PERSONA,
                "nombre": "Cliente rápido",
                "tipo_identificacion": Cliente.ID_CC,
                "identificacion": "991001001",
                "regimen_tributario": "COMMON_REGIME",
                "direccion": "Calle 10 # 20-30",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AlegraWriteOperation.objects.count(), 1)
        transport_class.return_value.find_candidates.assert_not_called()

    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_form_edit_persists_without_remote_get(self, transport_class):
        client = self._linked_client("Cliente editable", "991001002")
        baseline = {"nombre": client.nombre, "identificacion": client.identificacion, "tipo_identificacion": client.tipo_identificacion, "email": client.email}
        self._contact_map(client, "C-fast", {"source": "alegra_write", "last_synced_fields": baseline})
        staff = get_user_model().objects.create_user(
            username="staff-fast-edit", password="Test123!", is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.post(
            f"/panel/clientes/{client.pk}/editar/",
            {
                "tipo_cliente": Cliente.TIPO_PERSONA,
                "nombre": "Cliente editable actualizado",
                "primer_nombre": "Ana",
                "primer_apellido": "Cliente",
                "tipo_identificacion": Cliente.ID_CC,
                "identificacion": client.identificacion,
                "regimen_tributario": "COMMON_REGIME",
                "email": "nuevo@example.test",
                "direccion": "Calle 10 # 20-30",
            },
        )
        self.assertEqual(response.status_code, 302)
        operation = AlegraWriteOperation.objects.get(client=client)
        self.assertTrue(operation.result_metadata.get("deferred_prepare"))
        transport_class.return_value.get_contact.assert_not_called()

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_processor_materializes_deferred_update_after_local_save(self, transport_class, execute_update):
        client = self._linked_client("Cliente diferido", "991001003")
        system = alegra_system()
        baseline = {"nombre": client.nombre, "identificacion": client.identificacion, "tipo_identificacion": client.tipo_identificacion, "email": "old@example.test"}
        self._contact_map(client, "C-deferred", {"source": "alegra_write", "last_synced_fields": baseline})
        client.email = "new@example.test"
        client.save(update_fields=["email"])
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        self.assertIsNotNone(operation)
        self.assertTrue(operation.result_metadata.get("deferred_prepare"))
        transport_class.return_value.get_contact.return_value = {
            "id": "C-deferred", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "email": "old@example.test", "type": ["client"], "status": "active",
        }
        execute_update.side_effect = lambda operation_id, authorization: AlegraWriteOperation.objects.get(pk=operation_id)
        result = process_pending_operations(limit=1, execute=True)
        operation.refresh_from_db()
        self.assertEqual(result["pending"], 1)
        self.assertFalse(operation.result_metadata.get("deferred_prepare"))
        transport_class.return_value.get_contact.assert_called_once_with("C-deferred")

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_partial_baseline_with_matching_remote_state_is_noop_not_protected(self, transport_class, execute_update):
        client = self._linked_client("Cliente sin cambio", "991001004")
        mapping = self._contact_map(client, "C-noop", {
            "source": "alegra_write",
            "last_synced_fields": {
                "nombre": client.nombre,
                "identificacion": client.identificacion,
                "tipo_identificacion": client.tipo_identificacion,
            },
        })
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        self.assertIsNotNone(operation)
        transport_class.return_value.get_contact.return_value = {
            "id": "C-noop", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "email": client.email, "type": ["client"], "status": "active",
        }
        execute_update.side_effect = AssertionError("No debe ejecutarse PUT para una intención sin cambios")
        result = process_pending_operations(limit=1, execute=True)
        operation.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(result["results"][0]["state"], AlegraWriteOperation.STATE_SYNCED)
        self.assertTrue(operation.result_metadata["no_op"])
        self.assertEqual(operation.attempts, 0)
        self.assertIn("email", mapping.metadata["last_synced_fields"])

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_deferred_followup_does_not_mark_omitted_remote_fields_as_noop(self, transport_class, execute_update):
        client = self._linked_client("Cliente seguimiento", "991001012")
        client.email = "test@gmail.com"
        client.telefono_secundario = "3334455"
        client.ciudad = "MEDELLÍN"
        client.departamento = "ANTIOQUIA"
        client.save(update_fields=["email", "telefono_secundario", "ciudad", "departamento", "fecha_actualizacion"])
        self._contact_map(client, "C-omitted", {
            "source": "alegra_write",
            "last_synced_fields": {
                "nombre": client.nombre,
                "identificacion": client.identificacion,
                "tipo_identificacion": client.tipo_identificacion,
            },
        })
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        self.assertIsNotNone(operation)
        transport_class.return_value.get_contact.return_value = {
            "id": "C-omitted", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        }
        execute_update.side_effect = lambda operation_id, authorization: AlegraWriteOperation.objects.get(pk=operation_id)
        process_pending_operations(limit=1, execute=True)
        operation.refresh_from_db()
        self.assertFalse(operation.result_metadata.get("no_op", False))
        self.assertTrue(set(("email", "telefono_secundario", "ciudad", "departamento")).issubset(
            set(operation.result_metadata["fields"])
        ))

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_explicit_empty_remote_fields_are_not_classified_as_new_noop(self, transport_class, execute_update):
        """Reproduce la operación 13: Alegra devuelve las claves, pero vacías."""
        client = self._linked_client("Persona de Prueba Test", "1212121212")
        client.direccion = "Calle 10 # 20-30"
        client.email = "test@gmail.com"
        client.telefono = "2222222"
        client.telefono_secundario = "3334455"
        client.ciudad = "MEDELLÍN"
        client.departamento = "ANTIOQUIA"
        client.pais = "Colombia"
        client.save(update_fields=[
            "email", "telefono", "telefono_secundario", "ciudad", "departamento", "pais", "direccion", "fecha_actualizacion",
        ])
        mapping = self._contact_map(client, "678", {
            "source": "alegra_write",
            "last_synced_fields": {
                "nombre": client.nombre,
                "identificacion": client.identificacion,
                "tipo_identificacion": client.tipo_identificacion,
                "email": "",
                "telefono": "",
                "telefono_secundario": "",
                "ciudad": "",
                "departamento": "",
                "pais": "",
                "direccion": "Calle 10 # 20-30",
            },
        })
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        self.assertIsNotNone(operation)
        transport_class.return_value.get_contact.return_value = {
            "id": "678",
            "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY",
            "regime": "COMMON_REGIME",
            "email": "",
            "phonePrimary": "",
            "phoneSecondary": "",
            "mobile": "",
            "address": {"address": "Calle 10 # 20-30", "city": "", "department": "", "country": ""},
            "type": ["client"],
            "status": "active",
        }
        execute_update.side_effect = lambda operation_id, authorization: AlegraWriteOperation.objects.get(pk=operation_id)

        process_pending_operations(limit=1, execute=True)

        operation.refresh_from_db()
        self.assertFalse(operation.result_metadata.get("no_op", False))
        self.assertNotEqual(operation.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertTrue(set((
            "email", "telefono", "telefono_secundario", "ciudad", "departamento", "pais",
        )).issubset(set(operation.result_metadata["fields"])))
        execute_update.assert_called_once()

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_local_phone_change_remains_authorizable(self, transport_class, execute_update):
        client = self._linked_client("Cliente teléfono", "991001005")
        self._contact_map(client, "C-phone", {
            "source": "alegra_write",
            "last_synced_fields": {"nombre": client.nombre, "identificacion": client.identificacion,
                                    "tipo_identificacion": client.tipo_identificacion, "email": client.email,
                                    "telefono": "3000000000"},
        })
        client.telefono = "3111111111"
        client.save(update_fields=["telefono", "fecha_actualizacion"])
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        transport_class.return_value.get_contact.return_value = {
            "id": "C-phone", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "phonePrimary": "3000000000", "type": ["client"], "status": "active",
        }
        execute_update.side_effect = lambda operation_id, authorization: AlegraWriteOperation.objects.get(pk=operation_id)
        process_pending_operations(limit=1, execute=True)
        operation.refresh_from_db()
        self.assertFalse(operation.result_metadata.get("deferred_prepare"))
        self.assertEqual(operation.result_metadata["fields"], ["telefono"])

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_local_geographic_change_remains_authorizable(self, transport_class, execute_update):
        client = self._linked_client("Cliente ubicación", "991001007")
        client.direccion = "Calle 10 # 20-30"
        client.ciudad = "Cali"
        client.departamento = "Valle del Cauca"
        client.pais = "Colombia"
        client.save(update_fields=["ciudad", "departamento", "pais", "direccion", "fecha_actualizacion"])
        self._contact_map(client, "C-location", {
            "source": "alegra_write",
            "last_synced_fields": {"nombre": client.nombre, "identificacion": client.identificacion,
                                    "tipo_identificacion": client.tipo_identificacion,
                                    "email": client.email,
                                    "direccion": "Calle 10 # 20-30",
                                    "ciudad": "Medellín", "departamento": "Antioquia", "pais": "Colombia"},
        })
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        transport_class.return_value.get_contact.return_value = {
            "id": "C-location", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "address": {"address": "Calle 10 # 20-30", "city": "Medellín", "department": "Antioquia", "country": "Colombia"},
            "type": ["client"], "status": "active",
        }
        execute_update.side_effect = lambda operation_id, authorization: AlegraWriteOperation.objects.get(pk=operation_id)
        process_pending_operations(limit=1, execute=True)
        operation.refresh_from_db()
        self.assertEqual(operation.result_metadata["fields"], ["ciudad", "departamento"])

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_identification_and_document_type_changes_are_protected(self, transport_class, execute_update):
        clients = []
        for index, field, value in ((8, "identificacion", "991001108"), (9, "tipo_identificacion", Cliente.ID_NIT)):
            client = self._linked_client(f"Cliente protegido {index}", f"99100100{index}")
            self._contact_map(client, f"C-protected-{index}", {
                "source": "alegra_write",
                "last_synced_fields": {"nombre": client.nombre, "identificacion": client.identificacion,
                                        "tipo_identificacion": client.tipo_identificacion, "email": client.email},
            })
            setattr(client, field, value)
            client.save(update_fields=[field, "fecha_actualizacion"])
            enqueue_update_if_changed(client, defer_remote_check=True)
            clients.append(client)
        transport_class.return_value.get_contact.side_effect = [
            {"id": "C-protected-8", "name": clients[0].nombre,
             "identificationObject": {"type": "CC", "number": "991001008"},
             "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME", "type": ["client"]},
            {"id": "C-protected-9", "name": clients[1].nombre,
             "identificationObject": {"type": "CC", "number": "991001009"},
             "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME", "type": ["client"]},
        ]
        execute_update.side_effect = AssertionError("No debe ejecutarse PUT para cambios protegidos")
        process_pending_operations(limit=2, execute=True)
        operations = list(AlegraWriteOperation.objects.filter(client__in=clients).order_by("client_id"))
        self.assertEqual([operation.error_code for operation in operations], ["protected_change", "protected_change"])

    def test_regime_change_is_not_enqueued_as_an_automatic_contact_update(self):
        client = self._linked_client("Cliente régimen", "991001010")
        self._contact_map(client, "C-regime", {
            "source": "alegra_write",
            "last_synced_fields": {"tipo_cliente": client.tipo_cliente, "nombre": client.nombre, "identificacion": client.identificacion,
                                    "tipo_identificacion": client.tipo_identificacion, "email": client.email,
                                    "pais": "Colombia"},
        })
        client.regimen_tributario = "SIMPLIFIED_REGIME"
        client.save(update_fields=["regimen_tributario", "fecha_actualizacion"])
        self.assertIsNone(enqueue_update_if_changed(client, defer_remote_check=True))
        self.assertFalse(AlegraWriteOperation.objects.filter(client=client).exists())

    @patch("tienda.management.commands.alegra_resolver_bloqueo_contacto.AlegraWriteClient")
    def test_historical_protected_block_can_be_resolved_for_safe_followup(self, transport_class):
        client = self._linked_client("Cliente reanudable", "991001011")
        client.telefono_secundario = "3334455"
        client.save(update_fields=["telefono_secundario", "fecha_actualizacion"])
        mapping = self._contact_map(client, "C-reconcile", {
            "source": "alegra_write",
            "last_synced_fields": {"tipo_cliente": client.tipo_cliente, "nombre": client.nombre,
                                    "identificacion": client.identificacion,
                                    "tipo_identificacion": client.tipo_identificacion},
        })
        old_operation = AlegraWriteOperation.objects.create(
            system=mapping.system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
            external_id="C-reconcile", state=AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            idempotency_key="a" * 64, error_code="protected_change",
            reconciliation_reason="Bloqueo histórico de prueba.",
            result_metadata={"baseline": {"tipo_cliente": client.tipo_cliente, "nombre": client.nombre,
                                           "identificacion": client.identificacion,
                                           "tipo_identificacion": client.tipo_identificacion,
                                           "telefono_secundario": "3000000000"}},
        )
        transport_class.return_value.get_contact.return_value = {
            "id": "C-reconcile", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "phoneSecondary": "3000000000", "type": ["client"], "status": "active",
        }
        output = StringIO()
        call_command("alegra_resolver_bloqueo_contacto", "--operation-id", old_operation.pk,
                     "--apply", "--confirm", "AUTORIZAR REBASE ALEGRA", stdout=output)
        old_operation.refresh_from_db()
        self.assertEqual(old_operation.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(old_operation.result_metadata["resolution_status"], "resolved_for_followup")
        follow_up = AlegraWriteOperation.objects.filter(client=client, operation=AlegraWriteOperation.OP_UPDATE).exclude(pk=old_operation.pk).get()
        self.assertEqual(follow_up.state, AlegraWriteOperation.STATE_PENDING)
        self.assertEqual(follow_up.external_id, "C-reconcile")
        transport_class.return_value.get_contact.assert_called_once_with("C-reconcile")

    @patch("tienda.management.commands.alegra_recuperar_noop_contacto.AlegraWriteClient")
    def test_noop_recovery_blocks_omitted_remote_fields(self, transport_class):
        client = self._linked_client("Cliente omitido", "991001013")
        client.email = "test@gmail.com"
        client.telefono_secundario = "3334455"
        client.save(update_fields=["email", "telefono_secundario", "fecha_actualizacion"])
        mapping = self._contact_map(client, "C-noop-omitted", {"source": "alegra_write"})
        operation = AlegraWriteOperation.objects.create(
            system=mapping.system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
            external_id="C-noop-omitted", state=AlegraWriteOperation.STATE_SYNCED,
            idempotency_key="b" * 64,
            result_metadata={
                "no_op": True, "confirmed_by_get": True,
                "local_snapshot": {"email": client.email, "telefono_secundario": client.telefono_secundario},
                "baseline": {"email": "", "telefono_secundario": ""},
            },
        )
        transport_class.return_value.get_contact.return_value = {
            "id": "C-noop-omitted", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        }
        output = StringIO()
        call_command("alegra_recuperar_noop_contacto", "--operation-id", operation.pk, stdout=output)
        self.assertIn("campos_remotos_omitidos", output.getvalue())
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 1)

    @patch("tienda.management.commands.alegra_recuperar_noop_contacto.AlegraWriteClient")
    def test_noop_recovery_is_idempotent_and_creates_one_follow_up(self, transport_class):
        client = self._linked_client("Cliente recuperable", "991001014")
        client.digito_verificacion = "0"
        client.telefono_secundario = "3334455"
        client.save(update_fields=["digito_verificacion", "telefono_secundario", "fecha_actualizacion"])
        mapping = self._contact_map(client, "C-noop-safe", {"source": "alegra_write"})
        operation = AlegraWriteOperation.objects.create(
            system=mapping.system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
            external_id="C-noop-safe", state=AlegraWriteOperation.STATE_SYNCED,
            idempotency_key="c" * 64,
            result_metadata={
                "no_op": True, "confirmed_by_get": True,
                "local_snapshot": {"telefono_secundario": client.telefono_secundario},
                "baseline": {"telefono_secundario": "3000000000"},
            },
        )
        transport_class.return_value.get_contact.return_value = {
            "id": "C-noop-safe", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion, "dv": "0"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "email": client.email, "phoneSecondary": "3000000000",
            "type": ["client"], "status": "active",
        }
        kwargs = {"operation_id": operation.pk, "apply": True,
                  "confirm": "AUTORIZAR RECUPERACION NO_OP ALEGRA"}
        call_command("alegra_recuperar_noop_contacto", **kwargs)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertTrue(operation.result_metadata["invalidated_no_op"])
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client, operation=AlegraWriteOperation.OP_UPDATE).count(), 2)
        call_command("alegra_recuperar_noop_contacto", **kwargs)
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client, operation=AlegraWriteOperation.OP_UPDATE).count(), 2)
        transport_class.return_value.get_contact.assert_called_once_with("C-noop-safe")

    @patch("tienda.management.commands.alegra_recuperar_noop_contacto.AlegraWriteClient")
    def test_noop_recovery_blocks_protected_difference_and_explicit_empty_is_not_omitted(self, transport_class):
        client = self._linked_client("Cliente protegido", "991001015")
        client.digito_verificacion = "0"
        client.save(update_fields=["digito_verificacion", "fecha_actualizacion"])
        mapping = self._contact_map(client, "C-noop-protected", {"source": "alegra_write"})
        operation = AlegraWriteOperation.objects.create(
            system=mapping.system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
            external_id="C-noop-protected", state=AlegraWriteOperation.STATE_SYNCED,
            idempotency_key="d" * 64,
            result_metadata={
                "no_op": True, "confirmed_by_get": True,
                "local_snapshot": {"email": "nuevo@example.test"},
                "baseline": {"email": "anterior@example.test", "identificacion": client.identificacion},
            },
        )
        transport_class.return_value.get_contact.return_value = {
            "id": "C-noop-protected", "name": client.nombre,
            "identificationObject": {"type": "NIT", "number": client.identificacion, "dv": "0"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "email": "", "type": ["client"], "status": "active",
        }
        output = StringIO()
        call_command("alegra_recuperar_noop_contacto", "--operation-id", operation.pk, stdout=output)
        self.assertIn("diferencias_protegidas", output.getvalue())
        self.assertEqual(AlegraWriteOperation.objects.filter(client=client).count(), 1)

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraContactWriteService.execute_update")
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_local_name_change_is_authorizable_when_remote_name_is_unchanged(self, transport_class, execute_update):
        client = self._linked_client("Cliente nombre", "991001006")
        self._contact_map(client, "C-name", {
            "source": "alegra_write",
            "last_synced_fields": {"nombre": "Nombre anterior", "identificacion": client.identificacion,
                                    "tipo_identificacion": client.tipo_identificacion, "email": client.email},
        })
        client.nombre = "Nombre nuevo"
        client.save(update_fields=["nombre", "fecha_actualizacion"])
        operation = enqueue_update_if_changed(client, defer_remote_check=True)
        transport_class.return_value.get_contact.return_value = {
            "id": "C-name", "name": "Nombre anterior",
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        }
        execute_update.side_effect = lambda operation_id, authorization: AlegraWriteOperation.objects.get(pk=operation_id)
        process_pending_operations(limit=1, execute=True)
        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        execute_update.assert_called_once()

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_name_put_confirms_and_persists_remote_name_as_baseline(self, transport_class):
        client = self._linked_client("Nombre original", "991001008")
        mapping = self._contact_map(client, "C-name-confirmed", {
            "source": "alegra_write",
            "last_confirmed": {
                "nombre": "Nombre original",
                "identificacion": client.identificacion,
                "tipo_identificacion": client.tipo_identificacion,
            },
            "last_synced_fields": {"nombre": "Nombre original"},
        })
        client.primer_nombre = "PERSON TEST"
        client.primer_apellido = "Test 222 Test Test SA"
        client.nombre = "PERSON TEST Test 222 Test Test SA"
        client.save(update_fields=["primer_nombre", "primer_apellido", "nombre", "fecha_actualizacion"])
        remote_before = {
            "id": mapping.external_id,
            "name": "Nombre original",
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        }
        service = AlegraContactWriteService(transport=transport_class.return_value)
        operation, payload = service.prepare_update(
            client, mapping.system, external_id=mapping.external_id,
            remote_row=remote_before, baseline=mapping.metadata["last_confirmed"],
        )
        self.assertIn("nombre", operation.result_metadata["fields"])
        self.assertEqual(payload["name"], client.nombre_mostrado)
        transport_class.return_value.update_contact.return_value = {"id": mapping.external_id}
        transport_class.return_value.get_contact.return_value = {
            **remote_before,
            "name": "PERSON TEST Test 222 Test Test SA",
            "nameObject": {"firstName": "PERSON TEST", "lastName": "Test 222 Test Test SA"},
        }
        result = service.execute_update(
            operation.pk,
            authorization=authorize_external_write(
                client_id=client.pk, operation="PUT", environment="production", confirmed=True,
            ),
        )
        mapping.refresh_from_db()
        self.assertEqual(result.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertEqual(mapping.metadata["last_confirmed"]["nombre"], "PERSON TEST Test 222 Test Test SA")
        self.assertEqual(mapping.metadata["last_synced_fields"]["nombre"], "PERSON TEST Test 222 Test Test SA")
        self.assertEqual(mapping.metadata["baseline_source"], "alegra_write")

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

    def test_candidate_is_stored_and_explicit_link_uses_fresh_get_without_post(self):
        client = self._linked_client("Ana Prueba", "123456790")
        operation = AlegraWriteOperation.objects.create(
            system=alegra_system(), client=client, operation=AlegraWriteOperation.OP_CREATE,
            idempotency_key="candidate-review-operation", state=AlegraWriteOperation.STATE_PENDING,
        )
        remote = {
            "id": "A-1", "name": "Contacto remoto", "identification": "123456790",
            "identificationObject": {"type": "CC", "number": "123456790"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
        }
        transport = Mock()
        transport.find_candidates.return_value = [remote]
        service = AlegraContactWriteService(transport=transport)
        result = service.execute_create(
            operation.pk,
            authorization=authorize_external_write(
                client_id=client.pk, operation="POST", environment="production", confirmed=True,
            ),
        )
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        result.refresh_from_db()
        candidate = result.result_metadata["remote_candidates"][0]
        self.assertEqual(candidate["id"], "A-1")
        self.assertEqual(candidate["identification"], "123456790")
        transport.get_contact.return_value = remote
        linked = service.link_existing_candidate(result.pk, "A-1", candidate)
        self.assertEqual(linked.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertTrue(linked.result_metadata["linked_existing"])
        self.assertTrue(ExternalObjectMap.objects.filter(
            object_id=client.pk, external_id="A-1", status=ExternalObjectMap.STATUS_ACTIVE,
        ).exists())
        transport.create_contact.assert_not_called()

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

    @patch.dict("os.environ", {"ALEGRA_AUTOMATION_WRITES_ENABLED": "true", "ALEGRA_EXTERNAL_WRITES_ENABLED": "true"}, clear=False)
    @patch("tienda.services.alegra_operation_queue.AlegraWriteClient")
    def test_deferred_update_with_incomplete_remote_context_becomes_review(self, transport_class):
        """Una validación incompleta no debe quedar pendiente indefinidamente."""
        client = self._linked_client("Cliente contexto incompleto", "991001099")
        mapping = self._contact_map(client, "C-incomplete", {
            "source": "alegra_write",
            "last_synced_fields": {
                "nombre": client.nombre,
                "identificacion": client.identificacion,
                "tipo_identificacion": client.tipo_identificacion,
                "email": "anterior@example.test",
            },
        })
        operation = AlegraWriteOperation.objects.create(
            system=mapping.system,
            client=client,
            operation=AlegraWriteOperation.OP_UPDATE,
            external_id=mapping.external_id,
            idempotency_key="deferred-incomplete-context",
            state=AlegraWriteOperation.STATE_PENDING,
            result_metadata={
                "deferred_prepare": True,
                "baseline": mapping.metadata["last_synced_fields"],
                "local_snapshot": {"email": "nuevo@example.test"},
            },
        )
        # Falta identificationObject: no hay contrato remoto suficiente para
        # construir un PUT seguro y tampoco debe realizarse ninguna escritura.
        transport_class.return_value.get_contact.return_value = {
            "id": mapping.external_id,
            "name": client.nombre,
            "type": ["client"],
            "status": "active",
        }

        result = process_pending_operations(limit=1, execute=True)

        operation.refresh_from_db()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(operation.error_code, "payload_validation")
        self.assertEqual(operation.attempts, 0)
        self.assertEqual(result["results"][0]["state"], AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        transport_class.return_value.update_contact.assert_not_called()


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

    @patch("tienda.services.alegra_write.AlegraContactWriteService.prepare_update")
    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_linked_updates_reuse_existing_pending_operation(self, transport_class, prepare_update):
        client = self._linked_client("Cliente actualización", "303")
        mapping = self._contact_map(client, "C-3", {
            "source": "alegra_write",
            "last_synced_fields": {
                "nombre": client.nombre, "identificacion": "303",
                "tipo_identificacion": "cc", "email": "old@example.test",
            },
        })
        client.email = "first@example.test"
        client.save(update_fields=["email"])
        transport_class.return_value.get_contact.return_value = {
            "id": "C-3", "name": client.nombre,
            "identificationObject": {"type": "CC", "number": "303"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "email": "old@example.test", "type": ["client"], "status": "active",
        }
        def prepare_side_effect(current_client, system, *, external_id, remote_row, baseline):
            operation = AlegraWriteOperation.objects.create(
                system=system, client=current_client, operation=AlegraWriteOperation.OP_UPDATE,
                external_id=external_id, idempotency_key="linked-update-pending-key",
                state=AlegraWriteOperation.STATE_PENDING,
            )
            return operation, {"email": current_client.email}
        prepare_update.side_effect = prepare_side_effect

        first = linked_client_update_candidates(limit=1, persist=True)
        self.assertTrue(first["prepared"], first)
        client.email = "second@example.test"
        client.save(update_fields=["email"])
        second = linked_client_update_candidates(limit=1, persist=True)

        self.assertEqual(first["prepared"], [AlegraWriteOperation.objects.get(client=client).pk])
        self.assertEqual(second["prepared"], first["prepared"])
        self.assertEqual(prepare_update.call_count, 1)
        self.assertEqual(
            AlegraWriteOperation.objects.filter(
                client=client, operation=AlegraWriteOperation.OP_UPDATE,
            ).count(),
            1,
        )

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

    def _prepare_phone_update_for_confirmation(self, transport):
        client = self._linked_client("Cliente confirmación", "991001120")
        client.telefono = "4444444"
        client.save(update_fields=["telefono", "fecha_actualizacion"])
        mapping = self._contact_map(client, "C-confirmation", {
            "source": "alegra_write",
            "last_synced_fields": {
                "nombre": client.nombre,
                "identificacion": client.identificacion,
                "tipo_identificacion": client.tipo_identificacion,
                "telefono": "2222222",
            },
        })
        remote = {
            "id": mapping.external_id,
            "name": client.nombre,
            "identificationObject": {"type": "CC", "number": client.identificacion},
            "kindOfPerson": "PERSON_ENTITY",
            "regime": "COMMON_REGIME",
            "phonePrimary": "2222222",
            "type": ["client"],
            "status": "active",
        }
        service = AlegraContactWriteService(transport=transport)
        operation, _payload = service.prepare_update(
            client,
            mapping.system,
            external_id=mapping.external_id,
            remote_row=remote,
            baseline=mapping.metadata["last_synced_fields"],
        )
        return client, mapping, operation, service, remote

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_put_get_confirma_campos_y_actualiza_baseline_solo_confirmados(self, transport_class):
        client, mapping, operation, service, remote = self._prepare_phone_update_for_confirmation(transport_class.return_value)
        transport_class.return_value.update_contact.return_value = {"id": mapping.external_id}
        transport_class.return_value.get_contact.return_value = {**remote, "phonePrimary": client.telefono}

        result = service.execute_update(
            operation.pk,
            authorization=authorize_external_write(
                client_id=client.pk, operation="PUT", environment="production", confirmed=True,
            ),
        )

        mapping.refresh_from_db()
        self.assertEqual(result.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertTrue(result.result_metadata["confirmed_by_get"])
        self.assertEqual(result.result_metadata["remote_confirmation"]["status"], "verified")
        self.assertEqual(mapping.metadata["last_confirmed"]["telefono"], client.telefono)

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_put_de_persona_incluye_name_object_aunque_solo_cambie_telefono(self, transport_class):
        client, mapping, operation, service, remote = self._prepare_phone_update_for_confirmation(transport_class.return_value)

        def update_response(external_id, payload, *, authorization):
            self.assertEqual(external_id, mapping.external_id)
            self.assertEqual(payload["nameObject"], {
                "firstName": client.primer_nombre,
                "lastName": client.primer_apellido,
            })
            self.assertEqual(payload["name"], client.nombre_mostrado)
            self.assertEqual(payload["phonePrimary"], client.telefono)
            return {"id": external_id}

        transport_class.return_value.update_contact.side_effect = update_response
        transport_class.return_value.get_contact.return_value = {**remote, "phonePrimary": client.telefono}

        result = service.execute_update(
            operation.pk,
            authorization=authorize_external_write(
                client_id=client.pk, operation="PUT", environment="production", confirmed=True,
            ),
        )

        self.assertEqual(result.state, AlegraWriteOperation.STATE_SYNCED)

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_put_get_con_valor_diferente_queda_en_revision_sin_reintento(self, transport_class):
        client, mapping, operation, service, remote = self._prepare_phone_update_for_confirmation(transport_class.return_value)
        transport_class.return_value.update_contact.return_value = {"id": mapping.external_id}
        transport_class.return_value.get_contact.return_value = {**remote, "phonePrimary": "9999999"}

        result = service.execute_update(
            operation.pk,
            authorization=authorize_external_write(
                client_id=client.pk, operation="PUT", environment="production", confirmed=True,
            ),
        )

        result.refresh_from_db()
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(result.error_code, "remote_mismatch")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.result_metadata["remote_confirmation"]["mismatched_fields"], ["telefono"])
        transport_class.return_value.update_contact.assert_called_once()

    @patch("tienda.services.alegra_write.AlegraWriteClient")
    def test_put_get_incompleto_no_confirma_ni_repite_escritura(self, transport_class):
        client, mapping, operation, service, remote = self._prepare_phone_update_for_confirmation(transport_class.return_value)
        transport_class.return_value.update_contact.return_value = {"id": mapping.external_id}
        incomplete = dict(remote)
        incomplete.pop("phonePrimary")
        transport_class.return_value.get_contact.return_value = incomplete

        result = service.execute_update(
            operation.pk,
            authorization=authorize_external_write(
                client_id=client.pk, operation="PUT", environment="production", confirmed=True,
            ),
        )

        result.refresh_from_db()
        self.assertEqual(result.state, AlegraWriteOperation.STATE_NEEDS_RECONCILIATION)
        self.assertEqual(result.error_code, "remote_incomplete")
        self.assertEqual(result.result_metadata["remote_confirmation"]["missing_fields"], ["telefono"])
        transport_class.return_value.update_contact.assert_called_once()
