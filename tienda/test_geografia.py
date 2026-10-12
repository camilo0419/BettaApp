from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tienda.forms import ClienteForm
from tienda.models import Cliente, ColombiaMunicipio
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.geografia import search_municipios


class GeografiaColombianaTests(TestCase):
    def setUp(self):
        self.municipio = ColombiaMunicipio.objects.create(
            codigo="05001", codigo_departamento="05", nombre="Medellín",
            departamento="Antioquia", version="DIVIPOLA-2025",
        )
        ColombiaMunicipio.objects.create(
            codigo="25317", codigo_departamento="25", nombre="Guachetá",
            departamento="Cundinamarca", version="DIVIPOLA-2025",
        )

    def test_search_without_accents_and_by_department(self):
        self.assertEqual(list(search_municipios("medellin")), [self.municipio])
        self.assertEqual(list(search_municipios("antioquia")), [self.municipio])

    def test_form_persists_code_and_legacy_display_fields(self):
        form = ClienteForm(data={
            "tipo_cliente": "persona", "primer_nombre": "Diego", "primer_apellido": "Garzon",
            "tipo_identificacion": "cc", "identificacion": "1017175766",
            "regimen_tributario": "COMMON_REGIME", "municipio_departamento": "Medellín, Antioquia",
            "divipola_code": "05001",
        })
        self.assertTrue(form.is_valid(), form.errors)
        cliente = form.save()
        self.assertEqual(cliente.divipola_municipio_id, self.municipio.pk)
        self.assertEqual(cliente.ciudad, "Medellín")
        self.assertEqual(cliente.departamento, "Antioquia")
        self.assertEqual(cliente.pais, "Colombia")

    def test_form_exposes_only_divipola_location_selector(self):
        form = ClienteForm()
        self.assertIn("municipio_departamento", form.fields)
        self.assertIn("divipola_code", form.fields)
        self.assertNotIn("ciudad", form.fields)
        self.assertNotIn("departamento", form.fields)

    def test_form_rejects_free_text_or_contradictory_divipola_selection(self):
        data = {
            "tipo_cliente": "persona", "primer_nombre": "Diego", "primer_apellido": "Garzon",
            "tipo_identificacion": "cc", "identificacion": "1017175766",
            "regimen_tributario": "COMMON_REGIME", "municipio_departamento": "Bogotá, Cundinamarca",
            "divipola_code": "05001",
        }
        form = ClienteForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("municipio_departamento", form.errors)

        data["divipola_code"] = ""
        form = ClienteForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn("municipio_departamento", form.errors)

    def test_edit_without_location_selection_preserves_legacy_location(self):
        cliente = Cliente.objects.create(
            nombre="Cliente histórico", ciudad="Cali", departamento="Valle del Cauca", pais="Colombia",
        )
        form = ClienteForm(data={
            "tipo_cliente": "persona", "nombre": "Cliente histórico", "primer_nombre": "Cliente",
            "primer_apellido": "histórico", "tipo_identificacion": "cc", "identificacion": "100",
            "regimen_tributario": "COMMON_REGIME", "municipio_departamento": "", "divipola_code": "",
        }, instance=cliente)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.ciudad, "Cali")
        self.assertEqual(saved.departamento, "Valle del Cauca")

    def test_baseline_comparison_treats_geographic_accents_as_equal(self):
        cliente = Cliente.objects.create(
            nombre="Diego Garzon", primer_nombre="Diego", primer_apellido="Garzon",
            tipo_identificacion="cc", identificacion="1017175766",
            ciudad="Medellin", departamento="Antioquia", pais="Colombia",
        )
        remote = {
            "id": "479", "name": "Diego Garzon", "identification": "1017175766",
            "identificationObject": {"type": "CC", "number": "1017175766"},
            "address": {"city": "Medellín", "department": "Antioquia", "country": "Colombia"},
        }
        comparison = BidirectionalClientSync().compare_linked_client(
            cliente, remote,
            {"nombre": "Diego Garzon", "identificacion": "1017175766", "ciudad": "Medellín", "departamento": "Antioquia", "pais": "Colombia"},
        )
        self.assertEqual(comparison["local_fields"], [])

    def test_endpoint_requires_panel_access_and_returns_catalog_results(self):
        user = get_user_model().objects.create_user(username="staff", password="password", is_staff=True)
        client = Client()
        client.force_login(user)
        response = client.get(reverse("panel_ajax_divipola"), {"q": "medellin"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], "05001")
