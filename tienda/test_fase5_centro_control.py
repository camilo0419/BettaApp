from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from tienda.models import Categoria, Cliente, EmpleadoPerfil, Producto, Solicitud, SolicitudTarea, Notificacion
from tienda.services.centro_control import centro_control_snapshot, refresh_control_alerts


User = get_user_model()


class CentroControlTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="control-admin", password="Test123!", is_staff=True)
        self.operario = User.objects.create_user(username="control-operario", password="Test123!")
        self.categoria = Categoria.objects.create(nombre="Control")
        self.producto = Producto.objects.create(nombre="Producto control", categoria=self.categoria)
        self.cliente = Cliente.objects.create(nombre="Cliente control", activo=True)
        self.solicitud = Solicitud.objects.create(
            producto=self.producto,
            cliente=self.cliente,
            cliente_nombre="Cliente control",
            cliente_celular="3000000000",
        )
        self.empleado = EmpleadoPerfil.objects.create(
            user=self.operario,
            activo=True,
        )

    def crear_tarea_atrasada(self):
        return SolicitudTarea.objects.create(
            solicitud=self.solicitud,
            titulo="Revisar entrega control",
            responsable=self.empleado,
            fecha_limite=timezone.localdate() - timedelta(days=1),
        )

    def test_snapshot_consolida_pendientes_y_advierte_sin_facturas(self):
        self.crear_tarea_atrasada()
        snapshot = centro_control_snapshot(self.staff)

        self.assertEqual(snapshot["summary"]["requests"], 1)
        self.assertEqual(snapshot["summary"]["processes_overdue"], 1)
        self.assertTrue(snapshot["financial"]["warning"])

    def test_tareas_de_usuario_no_staff_se_limitan_a_su_responsable(self):
        propia = self.crear_tarea_atrasada()
        otro_usuario = User.objects.create_user(username="control-otro", password="Test123!")
        otro_empleado = EmpleadoPerfil.objects.create(user=otro_usuario)
        otra = SolicitudTarea.objects.create(solicitud=self.solicitud, titulo="Otra tarea", responsable=otro_empleado)

        snapshot = centro_control_snapshot(self.operario)
        ids = [row["object"].id for row in snapshot["tasks"]["mine"]]
        self.assertIn(propia.id, ids)
        self.assertNotIn(otra.id, ids)

    def test_alertas_repetidas_son_idempotentes(self):
        tarea = self.crear_tarea_atrasada()
        first = refresh_control_alerts(self.staff)
        second = refresh_control_alerts(self.staff)

        self.assertEqual(first["created"], 1)
        self.assertEqual(second["created"], 0)
        self.assertEqual(Notificacion.objects.filter(tarea=tarea).count(), 1)

    def test_panel_requiere_staff_y_actualizacion_requiere_post(self):
        self.client.force_login(self.operario)
        self.assertEqual(self.client.get(reverse("panel_centro_control")).status_code, 403)

        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("panel_centro_control_actualizar_alertas")).status_code, 405)

    def test_actualizar_alertas_exige_csrf_y_acepta_post_valido(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        url = reverse("panel_centro_control_actualizar_alertas")
        self.assertEqual(csrf_client.post(url).status_code, 403)

        self.client.force_login(self.staff)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)

    def test_panel_staff_muestra_centro_y_formulario_de_alertas(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("panel_centro_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Centro de control")
        self.assertContains(response, "Actualizar alertas")
        self.assertContains(response, "No hay facturas Alegra sincronizadas")
