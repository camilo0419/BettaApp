from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from tienda.forms import ClienteForm
from tienda.models import Cliente
from tienda.services.alegra_write import build_contact_payload


class ClienteAlegraFormTests(TestCase):
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
            "direccion": "",
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

    def test_local_form_save_does_not_call_external_transport(self):
        form = ClienteForm(data=self.natural_data(identificacion="1019059651"))
        self.assertTrue(form.is_valid(), form.errors)
        with patch("tienda.services.alegra_write.AlegraWriteClient") as transport:
            client = form.save()
        self.assertIsNotNone(client.pk)
        transport.assert_not_called()

    def test_payload_blocks_missing_regime_without_changing_client(self):
        client = Cliente(
            nombre="Camilo Test", tipo_cliente=Cliente.TIPO_PERSONA,
            primer_nombre="Camilo", primer_apellido="Test",
            tipo_identificacion=Cliente.ID_CC, identificacion="1019059652",
        )
        with self.assertRaises(ValidationError):
            build_contact_payload(client)
        self.assertIsNone(client.pk)

