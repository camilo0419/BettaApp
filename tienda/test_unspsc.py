from django.contrib.auth.models import User
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from unittest.mock import patch
import os
import shutil
import tempfile
import zipfile

from tienda.forms import ProductoForm
from tienda.models import Categoria, Producto, UNSPSCCode, UNSPSCImportJob
from tienda.storage import PrivateMediaStorage
from tienda.services.unspsc import recommend_unspsc


class UNSPSCTests(TestCase):
    def setUp(self):
        test_tmp_root = os.path.join(settings.BASE_DIR, "tmp")
        os.makedirs(test_tmp_root, exist_ok=True)
        self._private_media_tmp = tempfile.mkdtemp(
            prefix="betta-unspsc-tests-", dir=test_tmp_root
        )
        self._private_media_override = override_settings(PRIVATE_MEDIA_ROOT=self._private_media_tmp)
        self._private_media_override.enable()
        self.segment = UNSPSCCode.objects.create(code="01000000", description="Agricultura", level="segment", catalog_version="2026", source="official")
        self.item = UNSPSCCode.objects.create(code="01010101", description="Semillas de prueba", level="product", parent=self.segment, catalog_version="2026", source="official")
        staff = User.objects.create_user(username="unspsc-staff", password="Test123!", is_staff=True)
        self.client.force_login(staff)

    def tearDown(self):
        self._private_media_override.disable()
        shutil.rmtree(self._private_media_tmp, ignore_errors=True)
        super().tearDown()

    def test_code_preserves_leading_zero_and_hierarchy(self):
        self.assertEqual(self.item.code, "01010101")
        self.assertEqual(self.item.parent.code, "01000000")

    def test_code_validation_rejects_non_eight_digit_values(self):
        invalid = UNSPSCCode(code="123", description="Inválido", level="product", catalog_version="2026")
        with self.assertRaises(ValidationError):
            invalid.full_clean()

    def test_empty_product_unspsc_remains_allowed(self):
        product = Producto.objects.create(nombre="Sin clasificación", activo=False)
        self.assertIsNone(product.unspsc_id)

    def test_recommendations_by_description_and_code(self):
        self.assertEqual(recommend_unspsc("semillas")[0].pk, self.item.pk)
        self.assertEqual(recommend_unspsc("01010101")[0].code, "01010101")
        self.assertLessEqual(len(recommend_unspsc("semillas")), 5)

    def test_recommendations_prioritize_product_and_ignore_generic_query(self):
        generic = UNSPSCCode.objects.create(
            code="01010100", description="Semillas agrícolas", level="class",
            catalog_version="2026", source="official",
        )
        results = recommend_unspsc("producto semillas")
        self.assertEqual(results[0].pk, self.item.pk)
        self.assertIn(generic, results)
        self.assertEqual(recommend_unspsc("producto"), [])

    def test_recommendations_match_accented_description(self):
        item = UNSPSCCode.objects.create(
            code="01010102", description="Impresión de prueba", level="product",
            catalog_version="2026", source="official",
        )
        self.assertEqual(recommend_unspsc("impresion")[0].pk, item.pk)

    def test_unchecked_new_product_cannot_assign_hidden_unspsc(self):
        category = Categoria.objects.create(nombre="UNSPSC QA 3")
        form = ProductoForm({
            "nombre": "Producto nuevo", "categoria": category.pk,
            "tipo_calculo": Producto.CALCULO_UNIDAD, "orden": 0,
            "precio_base_m2": "0", "precio_base_unidad": "0",
            "unspsc": self.item.pk, "asignar_unspsc": "",
        })
        self.assertTrue(form.is_valid(), form.errors)
        product = form.save()
        self.assertIsNone(product.unspsc_id)

    def test_checked_product_form_persists_only_confirmed_code(self):
        category = Categoria.objects.create(nombre="UNSPSC QA 4")
        form = ProductoForm({
            "nombre": "Producto clasificado", "categoria": category.pk,
            "tipo_calculo": Producto.CALCULO_UNIDAD, "orden": 0,
            "precio_base_m2": "0", "precio_base_unidad": "0",
            "unspsc": self.item.pk, "asignar_unspsc": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().unspsc_id, self.item.pk)

    def test_editing_only_unspsc_reuses_external_operation_without_integrity_error(self):
        second_item = UNSPSCCode.objects.create(
            code="01010103", description="Otra semilla de prueba", level="product",
            catalog_version="2026", source="official",
        )
        category = Categoria.objects.create(nombre="UNSPSC QA 5")
        product = Producto.objects.create(
            nombre="Producto UNSPSC editable", categoria=category,
            tipo_calculo=Producto.CALCULO_UNIDAD, precio_base_unidad="25.00",
            unspsc=self.item,
        )

        def edit(code_id):
            return self.client.post(
                reverse("panel_producto_editar", args=[product.pk]),
                {
                    "nombre": product.nombre,
                    "slug": product.slug,
                    "categoria": category.pk,
                    "descripcion_corta": "",
                    "descripcion_larga": "",
                    "imagen_estatica": "",
                    "activo": "on",
                    "destacado": "on",
                    "orden": product.orden,
                    "tipo_calculo": Producto.CALCULO_UNIDAD,
                    "precio_base_m2": "0",
                    "precio_base_unidad": "25.00",
                    "requiere_revision": "",
                    "unspsc": code_id,
                    "asignar_unspsc": "on",
                },
            )

        first = edit(self.item.pk)
        self.assertEqual(first.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.unspsc_id, self.item.pk)

        second = edit(second_item.pk)
        self.assertEqual(second.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.unspsc_id, second_item.pk)

        third = edit(self.item.pk)
        self.assertEqual(third.status_code, 302)
        product.refresh_from_db()
        self.assertEqual(product.unspsc_id, self.item.pk)

        self.assertEqual(product.alegra_write_operations.count(), 1)

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

    def test_unspsc_switch_is_off_and_content_collapsed_for_new_product(self):
        response = self.client.get(reverse("panel_producto_crear"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="panel-card unspsc-panel"')
        self.assertContains(response, 'class="unspsc-switch-track"')
        self.assertContains(response, 'class="unspsc-content is-collapsed"')
        self.assertContains(response, 'aria-hidden="true"')

    def test_unspsc_switch_is_on_for_classified_product(self):
        category = Categoria.objects.create(nombre="UNSPSC QA visual")
        product = Producto.objects.create(
            nombre="Producto clasificado visual", categoria=category,
            tipo_calculo=Producto.CALCULO_UNIDAD, unspsc=self.item,
        )
        response = self.client.get(reverse("panel_producto_editar", args=[product.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="unspsc-content"')
        self.assertContains(response, 'aria-hidden="false"')
        self.assertContains(response, self.item.code)

    def test_catalog_panel_is_staff_only_and_exposes_upload_workflow(self):
        response = self.client.get(reverse("panel_unspsc_catalogo"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Catálogo UNSPSC")
        self.assertContains(response, "actualizaciones se procesan fuera")
        self.client.logout()
        response = self.client.get(reverse("panel_unspsc_catalogo"))
        self.assertEqual(response.status_code, 302)

    def test_catalog_job_confirmation_records_user_and_state_endpoint(self):
        job = UNSPSCImportJob.objects.create(
            file=SimpleUploadedFile("catalogo.csv", b"Codigo,Descripcion\n01000000,Agricultura\n"),
            catalog_version="ui-test",
            status=UNSPSCImportJob.STATUS_READY,
            preview={"fingerprint": "a" * 64},
        )
        response = self.client.post(reverse("panel_unspsc_importacion_confirmar", args=[job.pk]))
        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.status, UNSPSCImportJob.STATUS_APPLY_REQUESTED)
        self.assertEqual(job.preview["confirmed_by"], "unspsc-staff")
        state = self.client.get(reverse("panel_unspsc_importacion_estado"))
        self.assertEqual(state.status_code, 200)
        self.assertEqual(state.json()["jobs"][0]["status_code"], UNSPSCImportJob.STATUS_APPLY_REQUESTED)

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
            os.unlink(filename)

    def test_import_accepts_shifted_csv_headers_and_reports_changes(self):
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as handle:
            handle.write("Título del archivo\nOtra fila informativa\nCódigo;Descripción;Estado\n01000000;Agricultura;Activo\n")
            filename = handle.name
        try:
            call_command("unspsc_importar", file=filename, catalog_version="shifted-test")
            self.assertEqual(UNSPSCCode.objects.filter(catalog_version="shifted-test").count(), 0)
        finally:
            os.unlink(filename)

    def test_xlsx_uses_compatible_sheet_after_intro_sheet(self):
        from openpyxl import Workbook
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
            filename = handle.name
        try:
            workbook = Workbook()
            workbook.active.title = "Resumen"
            workbook.active.append(["Hoja informativa"])
            sheet = workbook.create_sheet("Catalogo")
            sheet.append(["Título oficial"])
            sheet.append(["Código", "Descripción"])
            sheet.append(["01000000", "Agricultura"])
            workbook.save(filename)
            call_command("unspsc_importar", file=filename, catalog_version="xlsx-test", apply=True, confirm="IMPORTAR CATÁLOGO UNSPSC")
            self.assertTrue(UNSPSCCode.objects.filter(catalog_version="xlsx-test", code="01000000").exists())
        finally:
            os.unlink(filename)

    def test_parent_is_resolved_when_parent_appears_after_child(self):
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as handle:
            handle.write("Codigo,Descripcion\n01010101,Semillas\n01010100,Clase agrícola\n01010000,Agricultura familiar\n01000000,Agricultura\n")
            filename = handle.name
        try:
            call_command("unspsc_importar", file=filename, catalog_version="parent-order-test", apply=True, confirm="IMPORTAR CATÁLOGO UNSPSC")
            child = UNSPSCCode.objects.get(catalog_version="parent-order-test", code="01010101")
            self.assertEqual(child.parent.code, "01010100")
            self.assertEqual(child.parent.parent.code, "01010000")
            self.assertEqual(child.parent.parent.parent.code, "01000000")
        finally:
            os.unlink(filename)

    def test_preview_detects_existing_changes_without_writing(self):
        from tempfile import NamedTemporaryFile
        UNSPSCCode.objects.create(code="01000000", description="Anterior", level="segment", catalog_version="preview-test")
        with NamedTemporaryFile("w", encoding="utf-8", suffix=".csv", delete=False) as handle:
            handle.write("Codigo,Descripcion\n01000000,Nueva descripción\n")
            filename = handle.name
        try:
            call_command("unspsc_importar", file=filename, catalog_version="preview-test")
            self.assertEqual(UNSPSCCode.objects.get(catalog_version="preview-test", code="01000000").description, "Anterior")
        finally:
            os.unlink(filename)

    def test_import_job_claim_is_idempotent(self):
        from tienda.management.commands.unspsc_procesar_importacion import claim_job

        test_tmp_root = os.path.join(settings.BASE_DIR, "tmp")
        os.makedirs(test_tmp_root, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_tmp_root) as private_root, override_settings(PRIVATE_MEDIA_ROOT=private_root):
            job = UNSPSCImportJob.objects.create(
                file=SimpleUploadedFile("catalogo.csv", b"Codigo,Descripcion\n01000000,Agricultura\n"),
                catalog_version="claim-test",
            )
        first = claim_job(UNSPSCImportJob.STATUS_PENDING, job.pk)
        second = claim_job(UNSPSCImportJob.STATUS_PENDING, job.pk)
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        job.refresh_from_db()
        self.assertEqual(job.status, UNSPSCImportJob.STATUS_PROCESSING)

    def test_zip_catalog_is_extracted_streaming(self):
        from tienda.management.commands.unspsc_importar import _source_file

        test_tmp_root = os.path.join(settings.BASE_DIR, "tmp")
        os.makedirs(test_tmp_root, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_tmp_root) as directory:
            filename = os.path.join(directory, "catalogo.zip")
            with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("catalogo.csv", "Codigo,Descripcion\n01000000,Agricultura\n")
            with patch("zipfile.ZipFile.read", side_effect=AssertionError("no debe leer el ZIP completo")):
                extracted = _source_file(filename)
            try:
                with open(extracted, encoding="utf-8") as handle:
                    self.assertIn("01000000", handle.read())
            finally:
                os.unlink(extracted)

    def test_private_unspsc_storage_has_no_public_url(self):
        storage = PrivateMediaStorage()
        with self.assertRaises(ValueError):
            storage.url("private/unspsc/catalogo.csv")
