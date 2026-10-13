from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from tienda.forms import ClienteForm
from tienda.models import Cliente, ColombiaMunicipio
from tienda.services.alegra_write import build_contact_payload


class ClienteAlegraFormTests(TestCase):
    def setUp(self):
        self.municipio = ColombiaMunicipio.objects.create(
            codigo="05001", codigo_departamento="05", nombre="Medellín",
            departamento="Antioquia", version="DIVIPOLA-TEST",
        )

    def natural_data(self, **overrides):
        data = {
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "nombre": "Camilo Test",
            "primer_nombre": "Camilo",
            "segundo_nombre": "",
            "primer_apellido": "Test",
            "segundo_apellido": "",
            "tipo_identificacion": Cliente.ID_CC,
            "identificacion": "1019059650",
            "digito_verificacion": "",
            "regimen_tributario": "SIMPLIFIED_REGIME",
            "email": "",
            "telefono": "",
            "telefono_secundario": "",
            "celular": "",
            "whatsapp": "",
            "direccion": "Calle 10 # 20-30",
            "municipio_departamento": "Medellín, Antioquia",
            "divipola_code": "05001",
            "ciudad": "",
            "departamento": "",
            "pais": "",
            "codigo_postal": "",
            "contacto_principal": "",
            "nombre_comercial": "",
            "sector": "",
            "sitio_web": "",
            "preferencia_contacto": "",
            "notas": "",
            "activo": "on",
        }
        data.update(overrides)
        return data

    def test_form_captures_colombia_fields_and_payload(self):
        form = ClienteForm(data=self.natural_data())
        self.assertTrue(form.is_valid(), form.errors)
        client = form.save(commit=False)
        payload = build_contact_payload(client)
        self.assertEqual(payload["nameObject"], {"firstName": "Camilo", "lastName": "Test"})
        self.assertEqual(payload["identificationObject"], {"type": "CC", "number": "1019059650"})
        self.assertEqual(payload["regime"], "SIMPLIFIED_REGIME")
        self.assertNotIn("name", payload)

    def test_form_rechaza_alta_automatica_sin_departamento(self):
        form = ClienteForm(data=self.natural_data(municipio_departamento="", divipola_code=""))

        self.assertFalse(form.is_valid())
        self.assertIn("Selecciona un departamento para continuar", str(form.errors["municipio_departamento"]))

    def test_local_form_save_does_not_call_external_transport(self):
        form = ClienteForm(data=self.natural_data(identificacion="1019059651"))
        self.assertTrue(form.is_valid(), form.errors)
        with patch("tienda.services.alegra_write.AlegraWriteClient") as transport:
            client = form.save()
        self.assertIsNotNone(client.pk)
        transport.assert_not_called()

    def test_sitio_web_sin_protocolo_se_normaliza(self):
        form = ClienteForm(data=self.natural_data(sitio_web="veltrixcol.com.co"))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["sitio_web"], "https://veltrixcol.com.co")

    def test_sitio_web_con_protocolo_se_conserva(self):
        form = ClienteForm(data=self.natural_data(sitio_web="http://ejemplo.com"))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["sitio_web"], "http://ejemplo.com")

    def test_sitio_web_invalido_muestra_error(self):
        form = ClienteForm(data=self.natural_data(sitio_web="no es una url"))

        self.assertFalse(form.is_valid())
        self.assertIn("sitio_web", form.errors)

    def test_payload_blocks_missing_regime_without_changing_client(self):
        client = Cliente(
            nombre="Camilo Test", tipo_cliente=Cliente.TIPO_PERSONA,
            primer_nombre="Camilo", primer_apellido="Test",
            tipo_identificacion=Cliente.ID_CC, identificacion="1019059652",
        )
        with self.assertRaises(ValidationError):
            build_contact_payload(client)
        self.assertIsNone(client.pk)

    def test_form_rechaza_direccion_vacia_o_con_espacios(self):
        form = ClienteForm(data=self.natural_data(direccion="   "))

        self.assertFalse(form.is_valid())
        self.assertIn("direccion", form.errors)
        self.assertIn("La dirección física es obligatoria", str(form.errors["direccion"]))

