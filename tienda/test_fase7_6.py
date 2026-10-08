from unittest.mock import Mock

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from tienda.models import Cliente, ClientePuntoVenta, ExternalObjectMap
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync, CONFLICT, PENDING
from tienda.services.alegra_client_import import apply_create_plan, apply_remote_updates
from tienda.services.alegra_import import get_alegra_system


def contact(external_id="C-NEW", identification="900123456"):
    return {
        "id": external_id,
        "name": "Cliente remoto",
        "identification": identification,
        "identificationType": "NIT",
        "type": ["client", "company"],
        "email": "shared@example.test",
        "phonePrimary": "3001112222",
        "address": {"address": "Calle 1", "city": "Bogota"},
        "status": "active",
    }


class OperationalInboundTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.content_type = ContentType.objects.get_for_model(Cliente)

    def test_new_contact_creates_one_client_and_one_map_then_repeats_without_duplicates(self):
        row = contact()
        first = apply_create_plan([row], system=self.system)
        second = apply_create_plan([row], system=self.system)
        self.assertEqual(first["counts"]["CREATE_LOCAL"], 1)
        self.assertEqual(second["counts"]["NO_ACTION"], 1)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(ExternalObjectMap.objects.filter(resource_type="contacts", status="active").count(), 1)

    def test_remote_update_changes_shared_fields_and_preserves_local_value_when_remote_empty(self):
        client = Cliente.objects.create(
            nombre="Local", identificacion="900123456", tipo_identificacion="nit",
            email="shared@example.test", telefono="3000000000", direccion="Local 1",
        )
        point = ClientePuntoVenta.objects.create(cliente=client, nombre="Principal")
        mapping = ExternalObjectMap.objects.create(
            system=self.system, resource_type="contacts", external_id="C-1",
            content_type=self.content_type, object_id=client.pk,
            metadata={"last_confirmed": {"nombre": "Local", "identificacion": "900123456", "tipo_identificacion": "nit", "email": "old@example.test", "telefono": "3000000000", "direccion": "Local 1"}},
        )
        row = contact("C-1")
        row.update({"name": "Remoto actualizado", "email": "", "phonePrimary": "3009998888", "address": {"address": "Nueva 2", "city": "Cali"}})
        service = BidirectionalClientSync()
        comparison = service.prepare_inbound_updates([row], {"C-1": client}, {"C-1": mapping.metadata["last_confirmed"]})
        self.assertEqual(comparison[0]["state"], PENDING)
        self.assertNotIn("email", comparison[0]["remote_fields"])
        result = apply_remote_updates([row], comparison, system=self.system)
        client.refresh_from_db()
        self.assertEqual(result["counts"]["UPDATED"], 1)
        self.assertEqual(client.nombre, "Remoto actualizado")
        self.assertEqual(client.email, "shared@example.test")
        self.assertEqual(client.telefono, "3009998888")
        self.assertEqual(ClientePuntoVenta.objects.get(pk=point.pk).cliente_id, client.pk)

    def test_simultaneous_changes_are_conflict_and_not_applied(self):
        client = Cliente.objects.create(nombre="Local", identificacion="900123456")
        row = contact("C-1")
        comparison = BidirectionalClientSync().compare_linked_client(
            client,
            {**row, "name": "Remoto"},
            {"nombre": "Original", "identificacion": "900123456"},
        )
        self.assertEqual(comparison["state"], CONFLICT)
        self.assertIn("nombre", comparison["conflict_fields"])

    def test_shared_email_does_not_create_identity_conflict(self):
        first = Cliente.objects.create(nombre="Uno", email="shared@example.test")
        second = Cliente.objects.create(nombre="Dos", email="shared@example.test")
        self.assertNotEqual(first.pk, second.pk)
