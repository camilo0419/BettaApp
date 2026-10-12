from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap, ExternalSystem


class HistoricalClientReconciliationTests(TestCase):
    def setUp(self):
        self.system = ExternalSystem.objects.create(code="alegra", name="Alegra")

    def _client(self, identification, **kwargs):
        return Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_PERSONA,
            nombre=kwargs.pop("nombre", "Cliente histórico"),
            primer_nombre="Cliente",
            primer_apellido="Histórico",
            tipo_identificacion=kwargs.pop("tipo_identificacion", Cliente.ID_CC),
            identificacion=identification,
            regimen_tributario="COMMON_REGIME",
            alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
            **kwargs,
        )

    @staticmethod
    def _remote(external_id, identification, document_type="CC"):
        return {
            "id": external_id,
            "name": "Contacto remoto",
            "identification": identification,
            "identificationObject": {"type": document_type, "number": identification},
            "identificationType": document_type,
            "type": ["client"],
            "status": "active",
        }

    @patch("tienda.management.commands.alegra_conciliar_clientes_historicos.AlegraWriteClient")
    def test_dry_run_classifies_without_maps_or_operations(self, client_class):
        unique = self._client("111")
        self._client("222", nombre="Sin coincidencia")
        self._client("333", nombre="Ambiguo")
        client_class.return_value.find_candidates.side_effect = [
            [self._remote("A-1", "111")],
            [],
            [self._remote("A-2", "333"), self._remote("A-3", "333")],
        ]

        output = StringIO()
        call_command("alegra_conciliar_clientes_historicos", "--all", stdout=output)

        text = output.getvalue()
        self.assertIn("UNIQUE_MATCH=1", text)
        self.assertIn("SAFE_CREATE=1", text)
        self.assertIn("AMBIGUOUS=1", text)
        self.assertEqual(ExternalObjectMap.objects.count(), 0)
        self.assertEqual(AlegraWriteOperation.objects.count(), 0)
        self.assertIn(f"ID local={unique.pk}", text)
        self.assertNotIn("Contacto remoto", text)

    @patch("tienda.management.commands.alegra_conciliar_clientes_historicos.AlegraWriteClient")
    def test_apply_links_unique_and_prepares_safe_create_without_external_write(self, client_class):
        unique = self._client("444")
        new = self._client("555", nombre="Cliente nuevo histórico")
        client_class.return_value.find_candidates.side_effect = [
            [self._remote("A-4", "444")],
            [],
        ]

        call_command(
            "alegra_conciliar_clientes_historicos",
            "--all",
            "--apply",
            "--confirm",
            "AUTORIZAR CONCILIACION HISTORICA",
            stdout=StringIO(),
        )

        unique_map = ExternalObjectMap.objects.get(object_id=unique.pk)
        self.assertEqual(unique_map.external_id, "A-4")
        operation = AlegraWriteOperation.objects.get(client=new)
        self.assertEqual(operation.state, AlegraWriteOperation.STATE_PENDING)
        client_class.return_value.post.assert_not_called()
        client_class.return_value.put.assert_not_called()
