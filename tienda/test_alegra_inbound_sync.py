from django.contrib.contenttypes.models import ContentType
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client as DjangoClient, TestCase
from django.urls import reverse
from django.template.loader import render_to_string
from unittest.mock import patch

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_client import AlegraError
from tienda.services.alegra_bidirectional_clients import CONFLICT, PENDING, SYNCED
from tienda.services.alegra_inbound_sync import InboundClientSyncService, InboundSyncConflict
from tienda.services.alegra_operation_queue import _materialize_deferred_update
from tienda.services.alegra_write import AlegraContactWriteService


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

    def test_zero_verification_digit_is_equivalent_to_string_zero(self):
        self.client.digito_verificacion = "0"
        self.client.save(update_fields=["digito_verificacion", "fecha_actualizacion"])
        row = {**self.remote, "verificationDigit": 0}
        row["identificationObject"] = {"type": "NIT", "number": "900123456"}
        candidate = self.service(row).baseline_candidate(self.client, self.mapping, row)
        self.assertEqual(candidate["state"], "SAFE")

    def test_network_error_is_propagated_without_local_change(self):
        class ErrorTransport:
            def get_contact(self, external_id):
                raise AlegraError("GET falló")

        with self.assertRaises(AlegraError):
            InboundClientSyncService(ErrorTransport()).apply(
                self.client.pk, self.mapping.pk, {}, ["email"],
            )

    def _name_conflict(self, *, remote_name="Nombre remoto", transport=None):
        self.client.razon_social = "Nombre local"
        self.client.nombre = "Nombre local"
        self.client.save(update_fields=["razon_social", "nombre", "fecha_actualizacion"])
        row = {
            **self.remote,
            "name": remote_name,
            "nameObject": None,
        }
        service = InboundClientSyncService(transport or ReadOnlyTransport(row))
        review = service.review(self.client, self.mapping, row)
        self.assertEqual(review["state"], CONFLICT)
        self.assertEqual(review["conflict_fields"], ["nombre"])
        return service, review, row

    def test_keep_remote_name_updates_local_and_baseline_after_fresh_get(self):
        service, review, row = self._name_conflict(remote_name="Nombre remoto")
        result = service.resolve_name_conflict(self.client.pk, self.mapping.pk, review, "keep_remote")
        self.assertEqual(result["status"], "remote_kept")
        self.client.refresh_from_db()
        self.mapping.refresh_from_db()
        self.assertEqual(self.client.razon_social, "Nombre remoto")
        self.assertEqual(self.mapping.metadata["last_confirmed"]["nombre"], "Nombre remoto")
        self.assertEqual(self.mapping.metadata["last_synced_fields"]["nombre"], "Nombre remoto")
        self.assertEqual(SyncAuditLog.objects.filter(operation="resolve_alegra_name_conflict").count(), 1)

    def test_apply_local_name_creates_one_authorized_operation_idempotently(self):
        service, review, _row = self._name_conflict(remote_name="Nombre remoto")
        first = service.resolve_name_conflict(self.client.pk, self.mapping.pk, review, "apply_local")
        second = service.resolve_name_conflict(self.client.pk, self.mapping.pk, review, "apply_local")
        self.assertEqual(first["status"], "local_queued")
        self.assertEqual(second["operation_id"], first["operation_id"])
        operations = AlegraWriteOperation.objects.filter(
            client=self.client, operation=AlegraWriteOperation.OP_UPDATE,
        )
        self.assertEqual(operations.count(), 1)
        operation = operations.get()
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        self.assertEqual(operation.result_metadata["authorized_conflict_fields"], ["nombre"])
        self.assertEqual(operation.result_metadata["local_snapshot"]["nombre"], "Nombre local")

    def test_remote_name_change_during_resolution_invalidates_decision(self):
        first_row = {**self.remote, "name": "Nombre remoto", "nameObject": None}
        changed_row = {**self.remote, "name": "Otro nombre remoto", "nameObject": None}

        class SequenceTransport:
            def __init__(self):
                self.rows = [first_row, changed_row]

            def get_contact(self, external_id):
                return dict(self.rows.pop(0))

        transport = SequenceTransport()
        service = InboundClientSyncService(transport)
        self.client.razon_social = "Nombre local"
        self.client.nombre = "Nombre local"
        self.client.save(update_fields=["razon_social", "nombre", "fecha_actualizacion"])
        review = service.review(self.client, self.mapping, first_row)
        with self.assertRaises(InboundSyncConflict):
            service.resolve_name_conflict(self.client.pk, self.mapping.pk, review, "keep_remote")
        self.assertFalse(AlegraWriteOperation.objects.filter(client=self.client).exists())

    def test_authorized_name_update_materializes_without_repeating_review_conflict(self):
        service, review, row = self._name_conflict(remote_name="Nombre remoto")
        result = service.resolve_name_conflict(self.client.pk, self.mapping.pk, review, "apply_local")
        operation = AlegraWriteOperation.objects.get(pk=result["operation_id"])
        materialized = _materialize_deferred_update(
            operation,
            AlegraContactWriteService(transport=ReadOnlyTransport(row)),
        )
        self.assertEqual(materialized.state, AlegraWriteOperation.STATE_PENDING)
        materialized.refresh_from_db()
        self.assertEqual(materialized.result_metadata["fields"], ["nombre"])
        self.assertNotEqual(materialized.state, AlegraWriteOperation.STATE_CONFLICT)

    def test_authorized_name_update_is_invalidated_if_remote_changes_before_processing(self):
        service, review, row = self._name_conflict(remote_name="Nombre remoto")
        result = service.resolve_name_conflict(self.client.pk, self.mapping.pk, review, "apply_local")
        operation = AlegraWriteOperation.objects.get(pk=result["operation_id"])
        changed = {**row, "name": "Otro nombre remoto"}
        materialized = _materialize_deferred_update(
            operation,
            AlegraContactWriteService(transport=ReadOnlyTransport(changed)),
        )
        self.assertEqual(materialized.state, AlegraWriteOperation.STATE_CONFLICT)
        self.assertEqual(materialized.error_code, "stale_conflict_review")

    def test_name_resolution_requires_authenticated_staff_panel_access(self):
        response = DjangoClient().post(
            reverse("panel_cliente_alegra_resolver_nombre", kwargs={"cliente_id": self.client.pk}),
            {"decision": "keep_remote", "review_token": "invalid"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/panel/login/", response.url)

    def test_name_resolution_actions_render_without_external_applicable_changes(self):
        rendered = render_to_string(
            "tienda/panel/cliente_alegra_cambios.html",
            {
                "cliente": self.client,
                "alegra_map": self.mapping,
                "review_token": "signed-review-token",
                "review": {
                    "state": CONFLICT,
                    "reason": "Cambios simultáneos requieren revisión manual.",
                    "changes": [],
                    "remote_fields": [],
                    "local_fields": [],
                    "conflict_fields": ["nombre"],
                    "protected_fields": [],
                    "name_conflict_only": True,
                },
            },
        )
        self.assertIn("Conservar nombre de Alegra", rendered)
        self.assertIn("Aplicar nombre de BettaApp", rendered)

    def test_review_view_exposes_name_actions_for_conflict_constant(self):
        _service, _review, row = self._name_conflict(remote_name="Nombre remoto")
        user = get_user_model().objects.create_user(
            username="staff-name-review", password="test-password", is_staff=True,
        )
        browser = DjangoClient()
        browser.force_login(user)
        with patch("tienda.views.AlegraWriteClient.get_contact", return_value=row):
            response = browser.get(
                reverse("panel_cliente_alegra_revisar_cambios", kwargs={"cliente_id": self.client.pk}),
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conservar nombre de Alegra")
        self.assertContains(response, "Aplicar nombre de BettaApp")
