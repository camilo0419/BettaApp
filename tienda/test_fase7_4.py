from types import SimpleNamespace
from unittest.mock import Mock

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, SimpleTestCase

from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import (
    BidirectionalClientSync,
    NEEDS_RECONCILIATION,
    PENDING,
)
from tienda.management.commands.alegra_sync_clientes import ExecutionLock
from tienda.services.alegra_client import AlegraResponse
from tienda.services.alegra_preimport_clients import fetch_contacts_by_id
from tienda.services.alegra_import import get_alegra_system


class ClientIdentityTests(TestCase):
    def test_two_clients_can_share_email_and_no_email_is_valid(self):
        first = Cliente.objects.create(nombre="Uno", email="shared@example.test")
        second = Cliente.objects.create(nombre="Dos", email="shared@example.test")
        third = Cliente.objects.create(nombre="Sin correo")
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(Cliente.objects.filter(email="shared@example.test").count(), 2)
        self.assertEqual(third.email, "")

    def test_external_identity_remains_unique_per_contact(self):
        system = get_alegra_system()
        first = Cliente.objects.create(nombre="Uno")
        second = Cliente.objects.create(nombre="Dos")
        content_type = ContentType.objects.get_for_model(Cliente)
        ExternalObjectMap.objects.create(system=system, resource_type="contacts", external_id="C-1", content_type=content_type, object_id=first.pk)
        with self.assertRaises(Exception):
            ExternalObjectMap.objects.create(system=system, resource_type="contacts", external_id="C-1", content_type=content_type, object_id=second.pk)


class DirectedRecoveryTests(SimpleTestCase):
    def test_directed_recovery_deduplicates_and_never_writes(self):
        client = Mock()
        client.get.return_value = AlegraResponse(200, {"id": "C-1", "name": "Contacto", "identification": "9001", "type": ["client"]}, "url")
        result = fetch_contacts_by_id(client, ["C-1", "C-1"])
        self.assertEqual(result["requested"], ["C-1"])
        self.assertEqual(len(result["recovered"]), 1)
        client.get.assert_called_once()

    def test_incomplete_directed_response_is_not_recovered(self):
        client = Mock()
        client.get.return_value = AlegraResponse(200, {"name": "Sin ID"}, "url")
        result = fetch_contacts_by_id(client, ["C-1"])
        self.assertEqual(result["incomplete"], ["C-1"])


class BidirectionalEngineTests(TestCase):
    def test_pending_local_client_plan_is_simulated_and_stable(self):
        service = BidirectionalClientSync()
        client = Cliente.objects.create(nombre="Local", email="shared@example.test")
        plans = service.pending_local_clients([client])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].state, PENDING)
        self.assertTrue(plans[0].simulated)
        self.assertEqual(service.pending_local_clients([client], [client.pk]), [])

    def test_retry_does_not_change_idempotency_or_reconcile_lost_response(self):
        service = BidirectionalClientSync()
        client = SimpleNamespace(pk=10, nombre="Local", email="shared@example.test")
        plan = service.plan_betta_creation(client)
        retry = service.retry_plan(plan)
        self.assertEqual(retry.idempotency_key, plan.idempotency_key)
        lost = service.lost_create_response(stable_id="10")
        self.assertEqual(service.retry_plan(lost).state, NEEDS_RECONCILIATION)

    def test_capability_registry_keeps_external_writes_disabled(self):
        capabilities = BidirectionalClientSync.capabilities()
        self.assertFalse(any(item["external_writes_enabled"] for item in capabilities.values()))
        self.assertIn("contacts", capabilities)

    def test_remote_change_requires_baseline_and_never_overwrites_with_empty(self):
        service = BidirectionalClientSync()
        client = Cliente.objects.create(nombre="Local", email="local@example.test", telefono="300")
        no_baseline = service.compare_linked_client(client, {"id": "C-1", "name": "Remoto", "email": "", "phonePrimary": "300"})
        self.assertEqual(no_baseline["state"], "CONFLICT")
        with_baseline = service.compare_linked_client(
            client,
            {"id": "C-1", "name": "Remoto", "email": "", "phonePrimary": "300"},
            {"nombre": "Local", "email": "local@example.test", "telefono": "300"},
        )
        self.assertIn("nombre", with_baseline["remote_fields"])
        self.assertNotIn("email", with_baseline["remote_fields"])

    def test_execution_lock_rejects_overlap_and_releases(self):
        first = ExecutionLock(name="bettaapp_test_sync_lock.lock")
        with first:
            with self.assertRaises(Exception):
                with ExecutionLock(name="bettaapp_test_sync_lock.lock"):
                    pass
        with ExecutionLock(name="bettaapp_test_sync_lock.lock"):
            pass
