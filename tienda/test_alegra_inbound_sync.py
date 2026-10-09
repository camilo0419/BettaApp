from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import TestCase

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_client import AlegraError
from tienda.services.alegra_bidirectional_clients import CONFLICT, PENDING, SYNCED
from tienda.services.alegra_inbound_sync import InboundClientSyncService, InboundSyncConflict


class ReadOnlyTransport:
    def __init__(self, row):
        self.row = row
        self.get_calls = []

    def get_contact(self, external_id):
        self.get_calls.append(str(external_id))
        return dict(self.row)


class InboundAlegraSyncTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Cliente local",
            razon_social="Cliente local",
            identificacion="900123456",
            tipo_identificacion=Cliente.ID_NIT,
            digito_verificacion="1",
            regimen_tributario="COMMON_REGIME",
            email="local@example.test",
            telefono="3000000000",
        )
        self.remote = {
            "id": "A-1", "name": "Cliente local", "identification": "900123456",
            "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
            "kindOfPerson": "LEGAL_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active", "email": "local@example.test",
            "phonePrimary": "3000000000",
        }
        sync = InboundClientSyncService(ReadOnlyTransport(self.remote)).sync
        self.mapping = ExternalObjectMap.objects.create(
            system=self.system, resource_type="contacts", external_id="A-1",
            content_type=ContentType.objects.get_for_model(Cliente), object_id=self.client.pk,
            metadata={"last_synced_fields": sync.build_betta_payload(self.client)},
        )

    def service(self, row=None):
        return InboundClientSyncService(ReadOnlyTransport(row or self.remote))

    def test_remote_only_change_is_reviewable_and_applied_once(self):
        row = {**self.remote, "email": "remoto@example.test"}
        service = self.service(row)
        review = service.review(self.client, self.mapping, row)
        self.assertEqual(review["state"], PENDING)
        self.assertEqual(review["remote_fields"], ["email"])
        result = service.apply(self.client.pk, self.mapping.pk, review, ["email"])
        self.assertEqual(result["status"], "updated")
        self.client.refresh_from_db()
        self.assertEqual(self.client.email, "remoto@example.test")
        self.assertEqual(SyncAuditLog.objects.filter(operation="apply_alegra_contact_changes").count(), 1)
        again = service.apply(self.client.pk, self.mapping.pk, review, ["email"])
        self.assertEqual(again["status"], "noop")
        self.assertEqual(SyncAuditLog.objects.filter(operation="apply_alegra_contact_changes").count(), 1)

    def test_local_only_change_is_not_overwritten(self):
        self.client.email = "local-cambio@example.test"
        self.client.save(update_fields=["email", "fecha_actualizacion"])
        review = self.service().review(self.client, self.mapping, self.remote)
        self.assertEqual(review["state"], "local_only")
        self.assertEqual(review["remote_fields"], [])
        self.assertIn("email", review["local_fields"])

    def test_conflict_blocks_application(self):
        self.client.email = "local-cambio@example.test"
        self.client.save(update_fields=["email", "fecha_actualizacion"])
        row = {**self.remote, "email": "remoto-cambio@example.test"}
        service = self.service(row)
        review = service.review(self.client, self.mapping, row)
        self.assertEqual(review["state"], CONFLICT)
        with self.assertRaises(InboundSyncConflict):
            service.apply(self.client.pk, self.mapping.pk, review, ["email"])
        self.client.refresh_from_db()
        self.assertEqual(self.client.email, "local-cambio@example.test")

    def test_protected_difference_is_visible_and_blocked(self):
        row = {**self.remote, "regime": "SIMPLIFIED_REGIME"}
        review = self.service(row).review(self.client, self.mapping, row)
        self.assertEqual(review["state"], CONFLICT)
        self.assertIn("regimen_tributario", review["protected_fields"])
        with self.assertRaises(InboundSyncConflict):
            self.service(row).apply(self.client.pk, self.mapping.pk, review, [])

    def test_no_change_is_idempotent_without_local_write(self):
        service = self.service()
        review = service.review(self.client, self.mapping, self.remote)
        self.assertEqual(review["state"], SYNCED)
        result = service.apply(self.client.pk, self.mapping.pk, review, [])
        self.assertEqual(result["status"], "noop")
        self.assertEqual(SyncAuditLog.objects.filter(operation="apply_alegra_contact_changes").count(), 0)

    def test_concurrent_local_change_invalidates_review(self):
        row = {**self.remote, "email": "remoto@example.test"}
        service = self.service(row)
        review = service.review(self.client, self.mapping, row)
        self.client.telefono = "3111111111"
        self.client.save(update_fields=["telefono", "fecha_actualizacion"])
        with self.assertRaises(InboundSyncConflict):
            service.apply(self.client.pk, self.mapping.pk, review, ["email"])

    def test_local_hash_invalidates_review_even_if_timestamp_is_reused(self):
        row = {**self.remote, "email": "remoto@example.test"}
        service = self.service(row)
        review = service.review(self.client, self.mapping, row)
        original_timestamp = self.client.fecha_actualizacion
        self.client.telefono = "3222222222"
        self.client.fecha_actualizacion = original_timestamp
        self.client.save(update_fields=["telefono", "fecha_actualizacion"])
        with self.assertRaises(InboundSyncConflict):
            service.apply(self.client.pk, self.mapping.pk, review, ["email"])

    def test_missing_baseline_and_transport_errors_are_blocked(self):
        self.mapping.metadata = {}
        self.mapping.save(update_fields=["metadata"])
        with self.assertRaises(InboundSyncConflict):
            self.service().review(self.client, self.mapping, self.remote)
        self.mapping.metadata = {"last_synced_fields": {"nombre": "Cliente local"}}
        self.mapping.save(update_fields=["metadata"])
        incomplete = self.service().review(self.client, self.mapping, {"id": "A-1", "name": "Cliente local"})
        self.assertEqual(incomplete["state"], CONFLICT)
        self.assertIn("identificacion", incomplete["protected_fields"])

    def test_safe_baseline_candidate_can_be_initialized_without_changing_client(self):
        service = self.service()
        original_name = self.client.nombre
        candidate = service.baseline_candidate(self.client, self.mapping, self.remote)
        self.assertEqual(candidate["state"], "SAFE")
        service.set_baseline(self.mapping, candidate)
        self.mapping.refresh_from_db()
        self.client.refresh_from_db()
        self.assertTrue(self.mapping.metadata.get("last_synced_fields"))
        self.assertEqual(self.mapping.metadata.get("baseline_source"), "alegra_get_reconciliation")
        self.assertEqual(self.client.nombre, original_name)

    def test_baseline_difference_is_review_only(self):
        row = {**self.remote, "email": "otro-remoto@example.test"}
        self.mapping.metadata = {}
        self.mapping.save(update_fields=["metadata"])
        candidate = self.service(row).baseline_candidate(self.client, self.mapping, row)
        self.assertEqual(candidate["state"], "REVIEW")
        self.assertIn("email", candidate["differences"])
        self.assertNotIn("last_synced_fields", self.mapping.metadata)

    def test_network_error_is_propagated_without_local_change(self):
        class ErrorTransport:
            def get_contact(self, external_id):
                raise AlegraError("GET falló")

        with self.assertRaises(AlegraError):
            InboundClientSyncService(ErrorTransport()).apply(
                self.client.pk, self.mapping.pk, {}, ["email"],
            )
