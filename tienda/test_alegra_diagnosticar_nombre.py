from unittest.mock import patch

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_import import get_alegra_system


class DiagnosticarNombreCommandTests(TestCase):
    def test_reports_three_way_fingerprints_without_personal_names(self):
        client = Cliente.objects.create(nombre="Nombre local", identificacion="900123")
        mapping = ExternalObjectMap.objects.create(
            system=get_alegra_system(), resource_type="contacts",
            content_type=ContentType.objects.get_for_model(Cliente),
            object_id=client.pk, external_id="678", status=ExternalObjectMap.STATUS_ACTIVE,
            metadata={"last_confirmed": {"nombre": "Nombre original"}, "baseline_source": "test"},
        )
        with patch("tienda.management.commands.alegra_diagnosticar_nombre.AlegraReadOnlyClient.get", return_value=type("Response", (), {
            "data": {"id": "678", "name": "Nombre remoto", "nameObject": {"firstName": "Nombre", "lastName": "remoto"}},
        })()):
            from io import StringIO
            output = StringIO()
            call_command("alegra_diagnosticar_nombre", client_id=client.pk, stdout=output)
        rendered = output.getvalue()
        self.assertIn('"local_equals_baseline": false', rendered)
        self.assertIn('"remote_equals_baseline": false', rendered)
        self.assertIn('"local_equals_remote": false', rendered)
        self.assertNotIn("Nombre local", rendered)
        self.assertNotIn("Nombre original", rendered)
        self.assertNotIn("Nombre remoto", rendered)
        self.assertTrue(ExternalObjectMap.objects.filter(pk=mapping.pk).exists())
