from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from tienda.forms import ProductoForm
from tienda.models import Categoria, Producto, UNSPSCCode
from tienda.services.unspsc import recommend_unspsc


class UNSPSCTests(TestCase):
    def setUp(self):
        self.segment = UNSPSCCode.objects.create(code="01000000", description="Agricultura", level="segment", catalog_version="2026", source="official")
        self.item = UNSPSCCode.objects.create(code="01010101", description="Semillas de prueba", level="product", parent=self.segment, catalog_version="2026", source="official")
        staff = User.objects.create_user(username="unspsc-staff", password="Test123!", is_staff=True)
        self.client.force_login(staff)

    def test_code_preserves_leading_zero_and_hierarchy(self):
        self.assertEqual(self.item.code, "01010101")
        self.assertEqual(self.item.parent.code, "01000000")

    def test_recommendations_by_description_and_code(self):
        self.assertEqual(recommend_unspsc("semillas")[0].pk, self.item.pk)
        self.assertEqual(recommend_unspsc("01010101")[0].code, "01010101")
        self.assertLessEqual(len(recommend_unspsc("semillas")), 5)

    def test_product_can_be_saved_without_unspsc_and_form_preserves_existing(self):
        category = Categoria.objects.create(nombre="UNSPSC QA")
        product = Producto.objects.create(nombre="Producto QA", categoria=category, tipo_calculo=Producto.CALCULO_UNIDAD, unspsc=self.item)
        form = ProductoForm({"nombre": "Producto QA editado", "categoria": category.pk, "tipo_calculo": Producto.CALCULO_UNIDAD, "orden": 0, "precio_base_m2": "0", "precio_base_unidad": "0", "asignar_unspsc": ""}, instance=product)
        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save()
        self.assertEqual(updated.unspsc_id, self.item.pk)

    def test_search_endpoint_is_staff_only_and_limited(self):
        response = self.client.get(reverse("panel_producto_unspsc_buscar"), {"q": "semillas"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["code"], "01010101")
        self.client.logout()
        response = self.client.get(reverse("panel_producto_unspsc_buscar"), {"q": "semillas"})
        self.assertEqual(response.status_code, 302)

    def test_catalog_panel_is_staff_only_and_exposes_upload_workflow(self):
        response = self.client.get(reverse("panel_unspsc_catalogo"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catálogo UNSPSC")
        self.assertContains(response, "actualizaciones se procesan fuera")
        self.client.logout()
        response = self.client.get(reverse("panel_unspsc_catalogo"))
        self.assertEqual(response.status_code, 302)

    def test_explicit_remove_does_not_touch_other_product_data(self):
        category = Categoria.objects.create(nombre="UNSPSC QA 2")
        product = Producto.objects.create(nombre="Producto QA 2", categoria=category, tipo_calculo=Producto.CALCULO_UNIDAD, unspsc=self.item)
        response = self.client.post(reverse("panel_producto_unspsc_quitar", args=[product.pk]))
        self.assertEqual(response.status_code, 302)
        product.refresh_from_db()
        self.assertIsNone(product.unspsc_id)
        self.assertEqual(product.nombre, "Producto QA 2")

    def test_import_command_validates_catalog_and_keeps_codes_as_text(self):
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as handle:
            handle.write("Código,Descripción,Estado\n01000000,Agricultura,Activo\n01010101,Semillas,Activo\n01010101,Duplicado,Activo\n")
            filename = handle.name
        try:
            call_command("unspsc_importar", file=filename, catalog_version="2026-test", source_url="https://official.test/catalog.csv", apply=True, confirm="IMPORTAR CATÁLOGO UNSPSC")
            self.assertEqual(UNSPSCCode.objects.filter(catalog_version="2026-test").count(), 2)
            self.assertIsNotNone(UNSPSCCode.objects.get(catalog_version="2026-test", code="01010101"))
        finally:
            import os
            os.unlink(filename)
