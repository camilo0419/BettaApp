from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_client import AlegraError, AlegraResponse
from tienda.services.alegra_import import get_alegra_system


class AlegraBaselineReconciliationCommandTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Cliente local",
            razon_social="Cliente local",
            identificacion="900123456",
            tipo_identificacion=Cliente.ID_NIT,
            digito_verificacion="1",
            regimen_tributario="COMMON_REGIME",
            email="local@example.test",
            telefono="3000000000",
        )
        self.remote = {
            "id": "A-1",
            "name": "Cliente local",
            "identification": "900123456",
            "identificationObject": {"type": "NIT", "number": "900123456", "dv": "1"},
            "kindOfPerson": "LEGAL_ENTITY",
            "regime": "COMMON_REGIME",
            "type": ["client"],
            "status": "active",
            "email": "local@example.test",
            "phonePrimary": "3000000000",
        }
        self.mapping = ExternalObjectMap.objects.create(
            system=self.system,
            resource_type="contacts",
            external_id="A-1",
            content_type=ContentType.objects.get_for_model(Cliente),
            object_id=self.client.pk,
            metadata={},
        )

    def fetched(self):
        return {
            "rows": [self.remote],
            "pages": 1,
            "coverage": "complete",
            "errors": [],
            "duplicate_external_ids": 0,
            "stopped_reason": "end_of_pagination",
        }

    def run_apply(self, transport):
        output = Path.cwd() / f".test_baseline_report_{self._testMethodName}.md"
        with patch.dict(
            "os.environ", {"ALEGRA_INBOUND_BASELINE_APPLY_ENABLED": "true"}, clear=False
        ), patch(
            "tienda.management.commands.alegra_conciliar_baselines.fetch_customer_contacts",
            return_value=self.fetched(),
        ), patch(
            "tienda.management.commands.alegra_conciliar_baselines.AlegraReadOnlyClient",
            return_value=transport,
        ):
            call_command(
                "alegra_conciliar_baselines",
                "--apply",
                "--confirm",
                "INICIALIZAR BASELINES ALEGRA",
                "--output",
                str(output),
            )
            report = output.read_text(encoding="utf-8")
            output.unlink(missing_ok=True)
            return report

    def test_invalid_second_response_is_counted_and_not_initialized(self):
        transport = Mock()
        transport.get.return_value = AlegraResponse(200, {"unexpected": "shape"}, "https://example.test")

        report = self.run_apply(transport)

        self.mapping.refresh_from_db()
        self.assertNotIn("last_confirmed", self.mapping.metadata)
        self.assertIn("'invalid_response': 1", report)
        self.assertNotIn("'query_error': 1", report)

    def test_query_error_is_counted_without_modifying_mapping(self):
        transport = Mock()
        transport.get.side_effect = AlegraError("error no publicable")

        report = self.run_apply(transport)

        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.metadata, {})
        self.assertIn("'query_error': 1", report)

    def test_second_execution_preserves_existing_baseline_and_skips_get(self):
        transport = Mock()
        transport.get.return_value = AlegraResponse(200, self.remote, "https://example.test")

        self.run_apply(transport)
        self.mapping.refresh_from_db()
        baseline = dict(self.mapping.metadata["last_confirmed"])
        transport.get.reset_mock()

        report = self.run_apply(transport)

        self.mapping.refresh_from_db()
        self.assertEqual(self.mapping.metadata["last_confirmed"], baseline)
        transport.get.assert_not_called()
        self.assertIn("Baselines existentes: 1", report)
        self.assertIn("Baselines inicializados: 0", report)

    def test_reclassification_after_second_get_is_counted(self):
        transport = Mock()
        changed = {**self.remote, "email": "different@example.test"}
        transport.get.return_value = AlegraResponse(200, changed, "https://example.test")

        report = self.run_apply(transport)

        self.mapping.refresh_from_db()
        self.assertNotIn("last_confirmed", self.mapping.metadata)
        self.assertIn("'classification_changed': 1", report)
