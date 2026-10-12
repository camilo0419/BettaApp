from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from tienda.models import Cliente, ExternalObjectMap, AlegraWriteOperation
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_inbound_sync import InboundClientSyncService
from tienda.services.alegra_operation_queue import enqueue_update_if_changed
from tienda.services.alegra_write import AlegraContactWriteService, authorize_external_write


class DiegoFixtureTransport:
    def __init__(self, row):
        self.row = dict(row)
        self.update_calls = []

    def get_contact(self, external_id):
        return {**self.row, "id": str(external_id)}

    def update_contact(self, external_id, payload, *, authorization):
        self.update_calls.append((str(external_id), dict(payload), authorization))
        if payload.get("email"):
            self.row["email"] = payload["email"]
        return {"id": str(external_id)}


class AlegraFinalValidationTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.client = Cliente.objects.create(
            pk=3,
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Diego Armando Garzon",
            razon_social="Diego Armando Garzon",
            identificacion="900123456",
            tipo_identificacion=Cliente.ID_NIT,
            digito_verificacion="1",
            regimen_tributario="COMMON_REGIME",
            email="diego@example.test",
            direccion="Calle 1",
            ciudad="Medellin",
            departamento="Antioquia",
            pais="Colombia",
        )
        self.remote = {
            "id": "479",
            "name": "Diego Armando Garzon",
            "identification": "900123456",
            "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
            "kindOfPerson": "LEGAL_ENTITY",
            "regime": "COMMON_REGIME",
            "type": ["client"],
            "status": "active",
            "email": "diego@example.test",
            "address": {"address": "Calle 1", "city": "Medellín", "department": "Antioquia", "country": "Colombia"},
        }
        self.mapping = ExternalObjectMap.objects.create(
            system=self.system,
            resource_type="contacts",
            external_id="479",
            content_type=ContentType.objects.get_for_model(Cliente),
            object_id=3,
            metadata={"source": "alegra_initial_import"},
        )

    def test_semantically_equivalent_location_is_safe_and_real_difference_is_review(self):
        service = InboundClientSyncService(DiegoFixtureTransport(self.remote))
        candidate = service.baseline_candidate(self.client, self.mapping, self.remote)
        self.assertEqual(candidate["state"], "SAFE")

        different = {**self.remote, "address": {**self.remote["address"], "city": "Cali"}}
        review = service.baseline_candidate(self.client, self.mapping, different)
        self.assertEqual(review["state"], "REVIEW")
        self.assertIn("ciudad", review["differences"])

    def test_linked_edit_enqueues_update_processes_and_refreshes_baseline(self):
        baseline_service = InboundClientSyncService(DiegoFixtureTransport(self.remote))
        candidate = baseline_service.baseline_candidate(self.client, self.mapping, self.remote)
        self.assertEqual(candidate["state"], "SAFE")
        baseline_service.set_baseline(self.mapping, candidate)

        self.client.email = "diego.actualizado@example.test"
        self.client.save(update_fields=["email", "fecha_actualizacion"])
        transport = DiegoFixtureTransport(self.remote)
        with patch("tienda.services.alegra_operation_queue.AlegraWriteClient", return_value=transport):
            operation = enqueue_update_if_changed(self.client)

        self.assertIsNotNone(operation)
        self.assertEqual(operation.operation, AlegraWriteOperation.OP_UPDATE)
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        payload = operation.result_metadata["prepared_payload"]
        self.assertEqual(payload["identificationObject"], {"type": "NIT", "number": "900123456", "dv": "1"})
        self.assertEqual(payload["kindOfPerson"], "LEGAL_ENTITY")
        self.assertEqual(payload["regime"], "COMMON_REGIME")
        self.assertEqual(payload["email"], "diego.actualizado@example.test")

        authorization = authorize_external_write(
            client_id=self.client.pk, operation="PUT", environment="production", confirmed=True,
        )
        result = AlegraContactWriteService(transport=transport).execute_update(
            operation.pk, authorization=authorization,
        )
        self.assertEqual(result.state, AlegraWriteOperation.STATE_SYNCED)
        self.assertEqual(transport.update_calls[0][0], "479")

        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.metadata["last_confirmed"]["email"], "diego.actualizado@example.test")
        self.assertEqual(self.mapping.metadata["last_confirmed"]["identificacion"], "900123456")
        self.assertEqual(self.mapping.external_id, "479")

