from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from tienda.models import Cliente
from tienda.services.alegra_write import build_contact_payload


class ClienteCrearViewTests(TestCase):
    def setUp(self):
        staff = User.objects.create_user(username="staff-create-client", password="Test123!", is_staff=True)
        self.client.force_login(staff)
        self.url = reverse("panel_cliente_crear")

    def test_post_invalido_muestra_errores_y_no_crea_cliente(self):
        response = self.client.post(self.url, {"tipo_cliente": Cliente.TIPO_PERSONA, "nombre": ""})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertContains(response, "No se pudo guardar el cliente")
        self.assertContains(response, "Este campo es obligatorio")

    def test_post_valido_crea_un_cliente_redirige_y_no_llama_alegra(self):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Cliente Formulario QA",
            "primer_nombre": "Cliente",
            "primer_apellido": "Formulario",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1019059653",
            "regimen_tributario": "SIMPLIFIED_REGIME",
            "activo": "on",
        }
        with patch("tienda.views.AlegraWriteClient") as transport:
            response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(Cliente.objects.get().nombre, "Cliente Formulario")
        transport.assert_not_called()

    def test_nombres_separados_construyen_nombre_local(self):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Persona",
            "primer_nombre": "Ana",
            "segundo_nombre": "María",
            "primer_apellido": "Pérez",
            "segundo_apellido": "Gómez",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1019059654",
            "regimen_tributario": "SIMPLIFIED_REGIME",
            "activo": "on",
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 302)
        cliente = Cliente.objects.get()
        self.assertEqual(cliente.nombre, "Ana María Pérez Gómez")
        self.assertEqual(cliente.nombre_mostrado, "Ana María Pérez Gómez")
        self.assertEqual(build_contact_payload(cliente)["nameObject"]["firstName"], "Ana")

    def test_save_directo_no_permite_nombre_generico_con_nombres_completos(self):
        cliente = Cliente(
            tipo_cliente=Cliente.TIPO_PERSONA,
            nombre="Persona",
            primer_nombre="Laura",
            segundo_nombre="Sofía",
            primer_apellido="Gómez",
            segundo_apellido="Rojas",
        )
        cliente.save()
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Laura Sofía Gómez Rojas")

    def test_save_historico_sin_nombres_separados_conserva_nombre(self):
        cliente = Cliente(tipo_cliente=Cliente.TIPO_PERSONA, nombre="Nombre histórico")
        cliente.save()
        cliente.refresh_from_db()
        self.assertEqual(cliente.nombre, "Nombre histórico")
