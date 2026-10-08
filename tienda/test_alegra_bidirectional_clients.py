from types import SimpleNamespace

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import (
    BidirectionalClientSync,
    CLASS_CONFLICT,
    CLASS_INCOMPLETE,
    CLASS_LINKED,
    CLASS_NEW,
    CLASS_PROBABLE,
    NEEDS_RECONCILIATION,
    PENDING,
    SafeAlegraWriteAdapter,
)
from tienda.services.alegra_import import get_alegra_system


class BidirectionalClientSyncTests(TestCase):
    def setUp(self):
        self.service = BidirectionalClientSync()

    def test_initial_import_classifies_identity_without_writing(self):
        linked_client = Cliente.objects.create(nombre="Vinculado", identificacion="900123456")
        probable_client = Cliente.objects.create(nombre="Coincidencia", identificacion="800111222")
        content_type = ContentType.objects.get_for_model(Cliente)
        system = get_alegra_system()
        ExternalObjectMap.objects.create(
            system=system,
            resource_type="contacts",
            external_id="C-LINKED",
            content_type=content_type,
            object_id=linked_client.pk,
        )
        rows = [
            {"id": "C-LINKED", "name": "Otro nombre", "identification": "900123456"},
            {"id": "C-PROBABLE", "name": "Coincidencia", "identification": "800-111-222"},
            {"id": "C-NEW", "name": "Nuevo", "identification": "700333444"},
            {"id": "C-INCOMPLETE", "name": "Sin documento"},
        ]
        before = (Cliente.objects.count(), ExternalObjectMap.objects.count())
        result = self.service.classify_initial_import(
            rows,
            local_clients=Cliente.objects.all(),
            mapped_external_ids={"C-LINKED"},
        )
        self.assertEqual([item["classification"] for item in result], [CLASS_LINKED, CLASS_PROBABLE, CLASS_NEW, CLASS_INCOMPLETE])
        self.assertEqual(before, (Cliente.objects.count(), ExternalObjectMap.objects.count()))
        self.assertEqual(result[1]["candidate_ids"], [probable_client.pk])

    def test_repeated_external_identification_is_conflict_not_merge(self):
        rows = [
            {"id": "C-1", "name": "Empresa Uno", "identification": "900123456"},
            {"id": "C-2", "name": "Empresa Dos", "identification": "900-123-456"},
        ]
        result = self.service.classify_initial_import(rows)
        self.assertEqual([item["classification"] for item in result], [CLASS_CONFLICT, CLASS_CONFLICT])
        self.assertTrue(all(item["identity_repeated_externally"] for item in result))

    def test_name_alone_is_not_a_link(self):
        local = Cliente.objects.create(nombre="Acme Colombia")
        result = self.service.classify_initial_import(
            [{"id": "C-1", "name": "Acme Colombia"}],
            local_clients=[local],
        )
        self.assertEqual(result[0]["classification"], CLASS_INCOMPLETE)

    def test_betta_creation_is_only_a_pending_plan(self):
        client = Cliente.objects.create(nombre="Nuevo Betta", identificacion="900999888", email="safe@example.test")
        before = (Cliente.objects.count(), ExternalObjectMap.objects.count())
        plan = self.service.plan_betta_creation(client)
        self.assertEqual(plan.state, PENDING)
        self.assertTrue(plan.simulated)
        self.assertNotIn("Authorization", plan.payload)
        self.assertEqual(before, (Cliente.objects.count(), ExternalObjectMap.objects.count()))

    def test_external_creation_is_only_a_pending_plan(self):
        plan = self.service.plan_alegra_creation({"id": "C-NEW", "name": "Contacto nuevo"})
        self.assertEqual(plan.state, PENDING)
        self.assertEqual(plan.external_id, "C-NEW")

    def test_idempotency_key_is_stable(self):
        client = SimpleNamespace(pk=42, nombre="Cliente", identificacion="900123")
        first = self.service.plan_betta_creation(client)
        second = self.service.plan_betta_creation(client)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_lost_response_requires_reconciliation_and_never_retries(self):
        plan = self.service.lost_create_response(stable_id="42")
        self.assertEqual(plan.state, NEEDS_RECONCILIATION)
        self.assertIn("conciliar", plan.reason)

    def test_concurrent_field_changes_are_conflicts(self):
        conflicts = self.service.detect_update_conflicts(
            {"nombre": "Original", "email": "old@example.test"},
            {"nombre": "Betta", "email": "old@example.test"},
            {"nombre": "Alegra", "email": "old@example.test"},
        )
        self.assertEqual(conflicts, ["nombre"])

    def test_simulated_adapter_has_no_external_write_method(self):
        adapter = SafeAlegraWriteAdapter()
        self.assertFalse(adapter.enabled)
        self.assertFalse(any(name in dir(adapter) for name in ("post", "put", "patch", "delete")))

