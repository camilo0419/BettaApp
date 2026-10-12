from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.management import CommandError, call_command
from django.test import TestCase

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_operation_queue import alegra_system


class ControlledContactUpdateTests(TestCase):
    def setUp(self):
        Cliente.objects.bulk_create([
            Cliente(
                tipo_cliente=Cliente.TIPO_PERSONA,
                nombre=f"Fixture {index}",
                primer_nombre="Fixture",
                primer_apellido=str(index),
                identificacion=f"900000{index:04d}",
                tipo_identificacion=Cliente.ID_CC,
                regimen_tributario="COMMON_REGIME",
            )
            for index in range(1, 165)
        ])
        self.client_local = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA,
            nombre="Persona de Prueba Test",
            primer_nombre="Persona de Prueba",
            primer_apellido="Test",
            identificacion="1212121212",
            tipo_identificacion=Cliente.ID_CC,
            regimen_tributario="COMMON_REGIME",
            direccion="Calle 10 # 20-30",
            email="test@gmail.com",
            telefono="5555555",
            telefono_secundario="3334455",
            ciudad="MEDELLÍN",
            departamento="ANTIOQUIA",
            pais="Colombia",
        )
        self.mapping = ExternalObjectMap.objects.create(
            system=alegra_system(), resource_type="contacts", external_id="678",
            content_type=ContentType.objects.get_for_model(Cliente), object_id=self.client_local.pk,
            status=ExternalObjectMap.STATUS_ACTIVE, metadata={},
        )
        self.remote = {
            "id": "678", "name": "Persona de Prueba Test",
            "nameObject": {
                "firstName": "Persona de Prueba", "secondName": None,
                "lastName": "Test", "secondLastName": None,
            },
            "identificationObject": {"type": "CC", "number": "1212121212"},
            "kindOfPerson": "PERSON_ENTITY", "regime": "COMMON_REGIME",
            "type": ["client"], "status": "active",
            "email": None, "phonePrimary": None, "phoneSecondary": None,
            "address": {"city": None, "department": None, "country": None},
        }

    def _confirmed(self):
        return {
            **self.remote,
            "email": "test@gmail.com", "phonePrimary": "5555555", "phoneSecondary": "3334455",
            "address": {"city": "MEDELLÍN", "department": "ANTIOQUIA", "country": "Colombia"},
        }

    @patch("tienda.management.commands.alegra_actualizar_contacto_controlado.AlegraWriteClient")
    def test_dry_run_solo_muestra_y_no_hace_put(self, client_class):
        client_class.return_value.get_contact.return_value = self.remote

        call_command("alegra_actualizar_contacto_controlado")

        client_class.return_value.update_contact.assert_not_called()
        self.assertEqual(SyncAuditLog.objects.count(), 0)

    @patch("tienda.management.commands.alegra_actualizar_contacto_controlado.AlegraWriteClient")
    def test_execute_hace_un_put_y_confirma_con_get(self, client_class):
        client_class.return_value.get_contact.side_effect = [self.remote, self._confirmed()]
        client_class.return_value.update_contact.return_value = {"id": "678"}

        call_command(
            "alegra_actualizar_contacto_controlado", "--execute",
            "--confirm", "AUTORIZAR ACTUALIZACION CONTACTO 678",
        )

        client_class.return_value.update_contact.assert_called_once()
        payload = client_class.return_value.update_contact.call_args.args[1]
        self.assertIn("nameObject", payload)
        self.assertEqual(payload["address"]["address"], "Calle 10 # 20-30")
        self.assertEqual(SyncAuditLog.objects.get().result, SyncAuditLog.RESULT_SUCCESS)

    @patch("tienda.management.commands.alegra_actualizar_contacto_controlado.AlegraWriteClient")
    def test_get_posterior_incompleto_no_repite_put(self, client_class):
        client_class.return_value.get_contact.side_effect = [self.remote, self.remote]
        client_class.return_value.update_contact.return_value = {"id": "678"}

        with self.assertRaises(CommandError):
            call_command(
                "alegra_actualizar_contacto_controlado", "--execute",
                "--confirm", "AUTORIZAR ACTUALIZACION CONTACTO 678",
            )

        client_class.return_value.update_contact.assert_called_once()
        self.assertEqual(SyncAuditLog.objects.get().result, SyncAuditLog.RESULT_PARTIAL)

    @patch("tienda.management.commands.alegra_actualizar_contacto_controlado.AlegraWriteClient")
    def test_name_object_realmente_diferente_mantiene_bloqueo(self, client_class):
        different = {**self.remote, "nameObject": {
            "firstName": "Otro nombre", "secondName": None,
            "lastName": "Test", "secondLastName": None,
        }}
        client_class.return_value.get_contact.return_value = different

        with self.assertRaises(CommandError) as raised:
            call_command("alegra_actualizar_contacto_controlado")

        self.assertIn("nameObject", str(raised.exception))
        client_class.return_value.update_contact.assert_not_called()

    @patch("tienda.management.commands.alegra_actualizar_contacto_controlado.AlegraWriteClient")
    def test_vinculo_incorrecto_bloquea_antes_del_get(self, client_class):
        self.mapping.external_id = "999"
        self.mapping.save(update_fields=["external_id"])

        with self.assertRaises(CommandError):
            call_command("alegra_actualizar_contacto_controlado")

        client_class.return_value.get_contact.assert_not_called()

