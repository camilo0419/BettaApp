from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tienda.models import AlegraContactStaging, Cliente, ClientePuntoVenta, ExternalObjectMap, Categoria
from tienda.services.alegra_client import AlegraResponse
from tienda.services.alegra_contact_import import AlegraContactImporter, AlegraContactReconciler, normalize_identity
from tienda.services.alegra_import import get_alegra_system


class FakeContactsClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def paged_get(self, path, *, limit, params):
        self.calls.append((path, limit, params))
        return [AlegraResponse(200, self.rows, "https://example.test/contacts")]


class AlegraContactsServiceTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.rows = [{
            "id": "C-1", "uuid": "uuid-1", "name": "Acme Colombia", "identification": "900.123.456-7",
            "phonePrimary": "6010000000", "phoneSecondary": "6010000001", "mobile": "3000000000",
            "email": "contacto@example.com", "status": "active", "type": ["company"],
            "address": {"zipCode": "110111", "department": "Bogotá D.C.", "country": "Colombia", "address": "Calle 1", "city": "Bogotá"},
            "internalContacts": [{"name": "No debe convertirse en punto"}],
        }]

    def test_paginates_and_stages_without_personal_payload(self):
        importer = AlegraContactImporter(FakeContactsClient(self.rows))
        result = importer.sync(limit=30)
        staging = AlegraContactStaging.objects.get()
        self.assertEqual(result["total"], 1)
        self.assertEqual(staging.identification, "900.123.456")
        self.assertEqual(staging.city, "Bogotá")
        self.assertEqual(staging.technical_data["internalContacts_count"], 1)
        self.assertNotIn("email", staging.technical_data)

    def test_identification_is_secondary_match_only(self):
        client = Cliente.objects.create(nombre="Acme local", tipo_cliente=Cliente.TIPO_EMPRESA, identificacion="900123456")
        importer = AlegraContactImporter(FakeContactsClient(self.rows))
        importer.sync(limit=30)
        staging = AlegraContactStaging.objects.get()
        self.assertEqual(normalize_identity(client.identificacion), normalize_identity(staging.identification))
        self.assertEqual(staging.classification, AlegraContactStaging.CLASS_PROBABLE)
        self.assertEqual(staging.matched_client_id, client.pk)
        self.assertFalse(staging.classification_locked)

    def test_import_is_idempotent_and_does_not_create_point_of_sale(self):
        importer = AlegraContactImporter(FakeContactsClient(self.rows))
        importer.sync(limit=30)
        staging = AlegraContactStaging.objects.get()
        staging.classification = AlegraContactStaging.CLASS_NEW
        staging.save(update_fields=["classification"])
        client = importer.import_contact(staging.pk)
        self.assertEqual(client.tipo_cliente, Cliente.TIPO_EMPRESA)
        self.assertEqual(client.digito_verificacion, "7")
        self.assertEqual(ClientePuntoVenta.objects.count(), 0)
        with self.assertRaises(ValueError):
            importer.import_contact(staging.pk)
        self.assertEqual(ExternalObjectMap.objects.filter(resource_type="contacts").count(), 1)

    def test_api_failure_keeps_previous_staging(self):
        importer = AlegraContactImporter(FakeContactsClient(self.rows))
        importer.sync(limit=30)
        class ErrorClient:
            def paged_get(self, *args, **kwargs):
                from tienda.services.alegra_client import AlegraError
                raise AlegraError("fallo controlado")
        result = AlegraContactImporter(ErrorClient()).sync(limit=30)
        self.assertEqual(result["errors"], ["fallo controlado"])
        self.assertEqual(AlegraContactStaging.objects.count(), 1)

    def test_manual_link_protects_existing_external_mapping(self):
        importer = AlegraContactImporter(FakeContactsClient(self.rows))
        importer.sync(limit=30)
        staging = AlegraContactStaging.objects.get()
        first = Cliente.objects.create(nombre="Primero")
        second = Cliente.objects.create(nombre="Segundo")
        importer.link_contact(staging.pk, first.pk)
        with self.assertRaises(ValueError):
            importer.link_contact(staging.pk, second.pk)


class ClientPointOfSaleIntegrityTests(TestCase):
    def test_customer_can_exist_without_points_and_has_many_points(self):
        client = Cliente.objects.create(nombre="Cliente sin local")
        first = ClientePuntoVenta.objects.create(cliente=client, nombre="Centro", es_principal=True)
        second = ClientePuntoVenta.objects.create(cliente=client, nombre="Norte")
        self.assertEqual(client.puntos_venta.count(), 2)
        self.assertTrue(first.es_principal)
        self.assertFalse(second.es_principal)

    def test_points_are_scoped_to_one_customer(self):
        first = Cliente.objects.create(nombre="Cliente A")
        second = Cliente.objects.create(nombre="Cliente B")
        point = ClientePuntoVenta.objects.create(cliente=first, nombre="Sucursal")
        point.cliente = second
        point.save()
        self.assertEqual(point.cliente_id, second.pk)


class AlegraContactsPanelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="contact-admin", password="test-pass")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.system = get_alegra_system()
        self.item = AlegraContactStaging.objects.create(system=self.system, external_id="C-9", name="Cliente externo", fetched_at="2026-10-07T12:00:00Z", classification=AlegraContactStaging.CLASS_NEW)

    def test_catalog_requires_staff_and_bulk_preview_has_no_effect(self):
        response = self.client.get(reverse("alegra_contactos_catalogo"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sincronizar clientes de Alegra")
        token = response.cookies["csrftoken"].value
        response = self.client.post(reverse("alegra_contactos_preview"), {"operation": "import", "selected_ids": [self.item.pk]}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Cliente.objects.count(), 0)

    def test_bulk_preview_rejects_missing_csrf(self):
        response = self.client.post(reverse("alegra_contactos_preview"), {"operation": "ignore", "selected_ids": [self.item.pk]})
        self.assertEqual(response.status_code, 403)

    @patch("tienda.views.AlegraContactImporter.sync")
    def test_sync_is_post_only_and_external_client_is_mocked(self, sync):
        sync.return_value = {"total": 3, "created": 2, "updated": 1, "errors": []}
        self.assertEqual(self.client.get(reverse("alegra_sync_contacts")).status_code, 405)
        token = self.client.get(reverse("alegra_contactos_catalogo")).cookies["csrftoken"].value
        response = self.client.post(reverse("alegra_sync_contacts"), {"limit": 1}, HTTP_X_CSRFTOKEN=token, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "3 contactos consultados")
        self.assertContains(response, "2 nuevos en staging")
        sync.assert_called_once()
