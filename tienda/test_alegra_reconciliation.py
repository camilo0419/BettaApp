from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tienda.models import AlegraItemStaging, Categoria, ExternalObjectMap, Producto
from tienda.services.alegra_import import AlegraItemImporter, AlegraItemReconciler


class AlegraReconciliationTests(TestCase):
    def setUp(self):
        self.category = Categoria.objects.create(nombre="Catálogo externo")
        from tienda.services.alegra_import import get_alegra_system
        self.system = get_alegra_system()

    def staging(self, external_id, name, external_type="product"):
        return AlegraItemStaging.objects.create(
            system=self.system,
            external_id=external_id,
            name=name,
            external_type=external_type,
            fetched_at="2026-10-07T12:00:00Z",
        )

    def test_classifies_new_probable_conflict_and_incomplete(self):
        Producto.objects.create(nombre="Lona local", categoria=self.category)
        Producto.objects.create(nombre="Duplicado", categoria=self.category)
        Producto.objects.create(nombre="Duplicado", categoria=self.category)
        new = self.staging("new", "Producto nuevo")
        probable = self.staging("probable", "LONA LOCAL")
        conflict = self.staging("conflict", "Duplicado")
        incomplete = self.staging("incomplete", "Kit", external_type="kit")
        AlegraItemReconciler().classify()
        self.assertEqual(new.refresh_from_db(), None)
        self.assertEqual(new.classification, AlegraItemStaging.CLASS_NEW)
        self.assertEqual(probable.refresh_from_db(), None)
        self.assertEqual(probable.classification, AlegraItemStaging.CLASS_PROBABLE)
        self.assertEqual(conflict.refresh_from_db(), None)
        self.assertEqual(conflict.classification, AlegraItemStaging.CLASS_CONFLICT)
        self.assertEqual(incomplete.refresh_from_db(), None)
        self.assertEqual(incomplete.classification, AlegraItemStaging.CLASS_INCOMPLETE)

    def test_manual_decision_is_preserved(self):
        item = self.staging("manual", "Producto nuevo")
        item.classification = AlegraItemStaging.CLASS_IGNORED
        item.classification_locked = True
        item.classification_reason = "Decisión administrativa"
        item.save()
        AlegraItemReconciler().classify(staging_ids=[item.pk])
        item.refresh_from_db()
        self.assertEqual(item.classification, AlegraItemStaging.CLASS_IGNORED)
        self.assertEqual(item.classification_reason, "Decisión administrativa")

    def test_existing_external_map_is_linked(self):
        product = Producto.objects.create(nombre="Producto vinculado", categoria=self.category)
        item = self.staging("mapped", "Otro nombre")
        from django.contrib.contenttypes.models import ContentType
        ExternalObjectMap.objects.create(system=self.system, resource_type="items", external_id=item.external_id, content_type=ContentType.objects.get_for_model(product), object_id=product.pk)
        AlegraItemReconciler().classify(staging_ids=[item.pk])
        item.refresh_from_db()
        self.assertEqual(item.classification, AlegraItemStaging.CLASS_LINKED)

    def test_import_is_inactive_and_idempotent(self):
        item = self.staging("importable", "Nuevo importable")
        AlegraItemReconciler().classify(staging_ids=[item.pk])
        importer = AlegraItemImporter()
        product = importer.import_item(item.pk, category_id=self.category.pk, calculation_type=Producto.CALCULO_UNIDAD)
        self.assertFalse(product.activo)
        with self.assertRaises(ValueError):
            importer.import_item(item.pk, category_id=self.category.pk, calculation_type=Producto.CALCULO_UNIDAD)
        self.assertEqual(Producto.objects.filter(nombre="Nuevo importable").count(), 1)


class AlegraBulkPanelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="alegra-admin", password="test-pass")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.user)
        self.category = Categoria.objects.create(nombre="Catálogo")
        from tienda.services.alegra_import import get_alegra_system
        self.system = get_alegra_system()
        self.item = AlegraItemStaging.objects.create(system=self.system, external_id="bulk-1", name="Bulk nuevo", fetched_at="2026-10-07T12:00:00Z", classification=AlegraItemStaging.CLASS_NEW)

    def test_bulk_preview_has_no_side_effects_and_requires_csrf(self):
        url = reverse("alegra_bulk_preview")
        response = self.client.post(url, {"operation": "import", "selected_ids": [self.item.pk]})
        self.assertEqual(response.status_code, 403)
        token = self.client.get(reverse("alegra_catalogo")).cookies["csrftoken"].value
        response = self.client.post(url, {"operation": "import", "selected_ids": [self.item.pk]}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Producto.objects.count(), 0)

    @patch("tienda.views.AlegraItemImporter.import_item")
    def test_bulk_confirm_uses_post_and_permission(self, import_item):
        import_item.return_value = Producto.objects.create(nombre="Creado", categoria=self.category, activo=False)
        token = self.client.get(reverse("alegra_catalogo")).cookies["csrftoken"].value
        response = self.client.post(reverse("alegra_bulk_confirm"), {"operation": "import", "selected_ids": [self.item.pk], "categoria_id": self.category.pk, "tipo_calculo": Producto.CALCULO_UNIDAD}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 302)
        import_item.assert_called_once()
