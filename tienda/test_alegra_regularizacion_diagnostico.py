from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.contrib.contenttypes.models import ContentType
from io import StringIO

from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_import import get_alegra_system
from tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria import diagnose_mapping


class HistoricalTaxDiagnosticTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()

    def _client(self, **kwargs):
        values = {
            "nombre": "Cliente reservado",
            "tipo_cliente": Cliente.TIPO_PERSONA,
            "identificacion": "900123456",
        }
        values.update(kwargs)
        return Cliente.objects.create(**values)

    def _map(self, client, external_id="A-1", metadata=None):
        return ExternalObjectMap.objects.create(
            system=self.system, resource_type="contacts", external_id=external_id,
            content_type=ContentType.objects.get_for_model(Cliente), object_id=client.pk,
            status=ExternalObjectMap.STATUS_ACTIVE,
            metadata=metadata or {"source": "alegra_initial_import"},
        )

    def _remote(self, **kwargs):
        row = {
            "id": "A-1", "regime": "COMMON_REGIME", "kindOfPerson": "LEGAL_ENTITY",
            "identificationObject": {"type": "NIT", "number": "900123456", "dv": 0},
            "type": ["client"], "status": "active",
        }
        row.update(kwargs)
        return row

    def test_missing_tax_fields_are_candidates_and_zero_dv_is_preserved(self):
        client = self._client()
        result = diagnose_mapping(self._map(client), client, self._remote())
        self.assertEqual(result["fields"]["regime"], "MISSING_LOCAL_CANDIDATE")
        self.assertEqual(result["fields"]["tipo_identificacion"], "MISSING_LOCAL_CANDIDATE")
        self.assertEqual(result["fields"]["digito_verificacion"], "MISSING_LOCAL_CANDIDATE")
        self.assertEqual(result["fields"]["kindOfPerson"], "DEFAULT_VALUE_REVIEW")

    def test_conflicts_and_invalid_remote_values_are_distinguished(self):
        client = self._client(tipo_cliente=Cliente.TIPO_EMPRESA, tipo_identificacion=Cliente.ID_CC,
                              regimen_tributario="SIMPLIFIED_REGIME", digito_verificacion="4")
        result = diagnose_mapping(self._map(client), client, self._remote(regime="UNKNOWN_REGIME", kindOfPerson="UNKNOWN"))
        self.assertIn("INVALID_REMOTE_VALUE", result["record_categories"])
        self.assertEqual(result["fields"]["regime"], "INVALID_REMOTE_VALUE")
        self.assertEqual(result["fields"]["tipo_identificacion"], "REAL_CONFLICT")

    def test_duplicate_active_map_is_blocked_without_field_classification(self):
        client = self._client()
        mapping = self._map(client, "A-1")
        result = diagnose_mapping(mapping, client, self._remote(), duplicate=True)
        self.assertEqual(result["record_categories"], ["UNVERIFIED_PROVENANCE"])
        self.assertEqual(result["fields"], {})

    @patch("tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria.fetch_customer_contacts")
    def test_command_is_aggregate_only_and_does_not_change_metadata(self, fetch):
        client = self._client()
        mapping = self._map(client)
        fetch.return_value = {"rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [], "duplicate_external_ids": []}
        output = StringIO()
        call_command("alegra_diagnosticar_regularizacion_tributaria", "--all", stdout=output)
        mapping.refresh_from_db()
        text = output.getvalue()
        self.assertIn("Modo=DRY-RUN", text)
        self.assertIn("MISSING_LOCAL_CANDIDATE", text)
        self.assertNotIn("Cliente reservado", text)
        self.assertEqual(mapping.metadata, {"source": "alegra_initial_import"})

    @patch("tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria.fetch_customer_contacts")
    def test_repeating_command_is_idempotent(self, fetch):
        client = self._client()
        mapping = self._map(client)
        fetch.return_value = {"rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [], "duplicate_external_ids": []}
        first = StringIO()
        second = StringIO()
        call_command("alegra_diagnosticar_regularizacion_tributaria", "--all", stdout=first)
        metadata_after_first = ExternalObjectMap.objects.get(pk=mapping.pk).metadata
        call_command("alegra_diagnosticar_regularizacion_tributaria", "--all", stdout=second)
        self.assertEqual(ExternalObjectMap.objects.get(pk=mapping.pk).metadata, metadata_after_first)
        self.assertIn("Modo=DRY-RUN", second.getvalue())

