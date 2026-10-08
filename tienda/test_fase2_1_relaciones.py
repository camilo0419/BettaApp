from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from tienda.forms import CotizacionForm, ProyectoForm, SolicitudClienteForm
from tienda.models import Categoria, Cliente, ClientePuntoVenta, Cotizacion, Producto, Proyecto, Solicitud


class CommercialRelationshipTests(TestCase):
    def setUp(self):
        self.category = Categoria.objects.create(nombre="Relaciones")
        self.product = Producto.objects.create(nombre="Producto relación", categoria=self.category)
        self.client_a = Cliente.objects.create(nombre="Cliente A")
        self.client_b = Cliente.objects.create(nombre="Cliente B")
        self.point_a = ClientePuntoVenta.objects.create(cliente=self.client_a, nombre="Sede A")
        self.point_a_two = ClientePuntoVenta.objects.create(cliente=self.client_a, nombre="Sede A 2")
        self.point_b = ClientePuntoVenta.objects.create(cliente=self.client_b, nombre="Sede B")

    def test_project_can_have_multiple_points_or_none(self):
        project = Proyecto.objects.create(cliente=self.client_a, nombre="Proyecto multi-sede")
        project.puntos_venta.add(self.point_a, self.point_a_two)
        self.assertEqual(project.puntos_venta.count(), 2)
        empty_project = Proyecto.objects.create(cliente=self.client_a, nombre="Proyecto sin sede")
        self.assertEqual(empty_project.puntos_venta.count(), 0)

    def test_project_form_rejects_points_from_another_client(self):
        form = ProyectoForm(data={"cliente": self.client_a.pk, "puntos_venta": [self.point_b.pk], "nombre": "Invalido", "estado": Proyecto.ESTADO_BORRADOR, "prioridad": Proyecto.PRIORIDAD_NORMAL, "activo": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("puntos_venta", form.errors)

    def test_project_model_validation_rejects_cross_client_point(self):
        project = Proyecto.objects.create(cliente=self.client_a, nombre="Proyecto")
        project.puntos_venta.add(self.point_b)
        with self.assertRaises(ValidationError):
            project.full_clean()

    def test_request_accepts_and_rejects_optional_point(self):
        request = Solicitud.objects.create(producto=self.product, cliente=self.client_a, cliente_nombre="Cliente A", cliente_celular="3000000000", punto_venta=self.point_a)
        request.full_clean()
        request.punto_venta = self.point_b
        with self.assertRaises(ValidationError):
            request.full_clean()
        request.punto_venta = None
        request.full_clean()

    def test_quote_accepts_and_rejects_optional_point(self):
        quote = Cotizacion(cliente=self.client_a, titulo="Cotización", punto_venta=self.point_a)
        quote.full_clean()
        quote.punto_venta = self.point_b
        with self.assertRaises(ValidationError):
            quote.full_clean()

    def test_forms_filter_points_by_client_and_keep_selected_value(self):
        request_form = SolicitudClienteForm(instance=Solicitud.objects.create(producto=self.product, cliente=self.client_a, cliente_nombre="A", cliente_celular="1", punto_venta=self.point_a))
        self.assertIn(self.point_a, request_form.fields["punto_venta"].queryset)
        self.assertNotIn(self.point_b, request_form.fields["punto_venta"].queryset)
        quote_form = CotizacionForm(instance=Cotizacion(cliente=self.client_a, titulo="Cotización", punto_venta=self.point_a))
        self.assertIn(self.point_a, quote_form.fields["punto_venta"].queryset)
        self.assertNotIn(self.point_b, quote_form.fields["punto_venta"].queryset)

    def test_only_one_active_principal_point(self):
        self.point_a.es_principal = True
        self.point_a.save()
        duplicate = ClientePuntoVenta(cliente=self.client_a, nombre="Sede A 3", es_principal=True)
        with self.assertRaises(ValidationError):
            duplicate.save()


class CommercialRelationshipSecurityTests(TestCase):
    def test_project_edit_requires_staff_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        user = get_user_model().objects.create_user(username="regular", password="test-pass")
        client.force_login(user)
        category = Categoria.objects.create(nombre="Seguridad")
        owner = Cliente.objects.create(nombre="Owner")
        project = Proyecto.objects.create(cliente=owner, nombre="Proyecto protegido")
        response = client.post(reverse("panel_proyecto_editar", kwargs={"proyecto_id": project.pk}), {"cliente": owner.pk, "nombre": project.nombre})
        self.assertEqual(response.status_code, 403)
