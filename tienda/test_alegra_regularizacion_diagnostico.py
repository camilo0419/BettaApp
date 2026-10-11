from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.contrib.contenttypes.models import ContentType
from io import StringIO

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_import import get_alegra_system
from tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria import (
    diagnose_mapping,
    initial_precedence_plan,
    plan_mapping,
)


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
        fetch.return_value = {"rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [], "duplicate_external_ids": 0}
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
        fetch.return_value = {"rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [], "duplicate_external_ids": 0}
        first = StringIO()
        second = StringIO()
        call_command("alegra_diagnosticar_regularizacion_tributaria", "--all", stdout=first)
        metadata_after_first = ExternalObjectMap.objects.get(pk=mapping.pk).metadata
        call_command("alegra_diagnosticar_regularizacion_tributaria", "--all", stdout=second)
        self.assertEqual(ExternalObjectMap.objects.get(pk=mapping.pk).metadata, metadata_after_first)
        self.assertIn("Modo=DRY-RUN", second.getvalue())

    @patch("tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria.fetch_customer_contacts")
    def test_integer_duplicate_count_does_not_raise_and_blocks_real_duplicate(self, fetch):
        client = self._client()
        mapping = self._map(client)
        row = self._remote()
        fetch.return_value = {
            "rows": [row, dict(row)], "pages": 1, "coverage": "complete", "errors": [],
            "duplicate_external_ids": 1,
        }
        output = StringIO()
        call_command("alegra_diagnosticar_regularizacion_tributaria", "--all", stdout=output)
        text = output.getvalue()
        self.assertIn("ids_duplicados=1", text)
        self.assertIn("UNVERIFIED_PROVENANCE=1", text)
        self.assertNotIn("MISSING_LOCAL_CANDIDATE", text)
        self.assertEqual(ExternalObjectMap.objects.get(pk=mapping.pk).metadata, {"source": "alegra_initial_import"})

    def test_plan_separates_safe_fields_from_default_person_review(self):
        client = self._client()
        plan = plan_mapping(self._map(client), client, self._remote(), coverage_complete=True)
        self.assertEqual(plan["fields"]["regime"]["classification"], "SAFE_FILL_CANDIDATE")
        self.assertEqual(plan["fields"]["tipo_identificacion"]["classification"], "SAFE_FILL_CANDIDATE")
        self.assertEqual(plan["fields"]["digito_verificacion"]["classification"], "SAFE_FILL_CANDIDATE")
        self.assertEqual(plan["fields"]["kindOfPerson"]["classification"], "DEFAULT_VALUE_REVIEW")
        self.assertFalse(plan["fully_safe_for_future_apply"])

    def test_plan_preserves_dv_zero_as_valid_remote_value(self):
        client = self._client()
        plan = plan_mapping(self._map(client), client, self._remote(), coverage_complete=True)
        detail = plan["fields"]["digito_verificacion"]
        self.assertEqual(detail["remote_state"], "present")
        self.assertEqual(detail["classification"], "SAFE_FILL_CANDIDATE")

    def test_plan_marks_remote_missing_only_with_complete_coverage(self):
        client = self._client()
        mapping = self._map(client)
        remote = self._remote(regime="", identificationObject={"type": "NIT", "number": "900123456", "dv": 0})
        complete = plan_mapping(mapping, client, remote, coverage_complete=True)
        incomplete = plan_mapping(mapping, client, None, coverage_complete=False)
        self.assertEqual(complete["fields"]["regime"]["classification"], "REMOTE_MISSING")
        self.assertEqual(incomplete["fields"]["regime"]["classification"], "BLOCKED")
        self.assertEqual(incomplete["fields"]["regime"]["reason"], "cobertura_remota_incompleta")

    def test_plan_blocks_duplicates_and_unverified_provenance(self):
        client = self._client()
        mapping = self._map(client)
        duplicate = plan_mapping(mapping, client, self._remote(), coverage_complete=True, duplicate=True)
        provenance = plan_mapping(mapping, client, self._remote(), coverage_complete=True,
                                  provenance_unverified=True)
        self.assertEqual(duplicate["fields"]["regime"]["classification"], "BLOCKED")
        self.assertEqual(duplicate["fields"]["regime"]["action"], "BLOCK")
        self.assertEqual(provenance["fields"]["regime"]["classification"], "UNVERIFIED_PROVENANCE")

    def test_plan_keeps_real_conflict_out_of_safe_candidates(self):
        client = self._client(tipo_cliente=Cliente.TIPO_EMPRESA, regimen_tributario="SIMPLIFIED_REGIME")
        plan = plan_mapping(self._map(client), client, self._remote(), coverage_complete=True)
        self.assertEqual(plan["fields"]["regime"]["classification"], "CONFLICT_REVIEW")
        self.assertEqual(plan["fields"]["kindOfPerson"]["classification"], "ALREADY_MATCHED")
        self.assertFalse(plan["fully_safe_for_future_apply"])

    def test_initial_precedence_proposes_valid_remote_values_without_writes(self):
        client = self._client()
        mapping = self._map(client)
        before = {"tipo_cliente": client.tipo_cliente, "regimen_tributario": client.regimen_tributario}
        plan = initial_precedence_plan(mapping, client, self._remote(), coverage_complete=True)
        self.assertEqual(plan["fields"]["regimen_tributario"]["classification"], "UPDATE_FROM_ALEGRA")
        self.assertEqual(plan["fields"]["tipo_cliente"]["classification"], "UPDATE_FROM_ALEGRA")
        self.assertEqual(plan["fields"]["digito_verificacion"]["remote_value"], "0")
        self.assertFalse(plan["client_blocked"])
        self.assertEqual((client.tipo_cliente, client.regimen_tributario), (before["tipo_cliente"], before["regimen_tributario"]))
        self.assertFalse(plan["writes"])

    def test_initial_precedence_does_not_infer_person_from_nit(self):
        client = self._client()
        remote = self._remote(kindOfPerson="", regime="COMMON_REGIME")
        plan = initial_precedence_plan(self._map(client), client, remote, coverage_complete=True)
        self.assertEqual(plan["fields"]["tipo_cliente"]["classification"], "SIN_DATO_ALEGRA")
        self.assertEqual(plan["fields"]["tipo_cliente"]["action"], "KEEP_LOCAL")

    def test_initial_precedence_blocks_identity_conflict(self):
        client = self._client(identificacion="900123457")
        plan = initial_precedence_plan(self._map(client), client, self._remote(), coverage_complete=True)
        self.assertTrue(plan["client_blocked"])
        self.assertEqual(plan["fields"]["regimen_tributario"]["classification"], "BLOCKED")
        self.assertEqual(plan["fields"]["regimen_tributario"]["reason"], "identificacion_no_coincide")

    def test_initial_precedence_blocks_only_invalid_remote_field(self):
        client = self._client()
        remote = self._remote(regime="REGIME_NOT_SUPPORTED")
        plan = initial_precedence_plan(self._map(client), client, remote, coverage_complete=True)
        self.assertFalse(plan["client_blocked"])
        self.assertEqual(plan["fields"]["regimen_tributario"]["classification"], "INVALID_REMOTE_VALUE")
        self.assertEqual(plan["fields"]["tipo_identificacion"]["classification"], "UPDATE_FROM_ALEGRA")
        self.assertTrue(plan["requires_manual_review"])

    def test_initial_precedence_keeps_remote_missing_and_reports_provenance(self):
        client = self._client()
        remote = self._remote(regime="", identificationObject={"type": "NIT", "number": "900123456"})
        plan = initial_precedence_plan(self._map(client), client, remote, coverage_complete=True,
                                       provenance_unverified=True)
        self.assertEqual(plan["fields"]["regimen_tributario"]["classification"], "SIN_DATO_ALEGRA")
        self.assertEqual(plan["fields"]["digito_verificacion"]["classification"], "SIN_DATO_ALEGRA")
        self.assertTrue(plan["provenance_exception"])
        self.assertFalse(plan["client_blocked"])

    @patch("tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria.fetch_customer_contacts")
    def test_command_initial_precedence_is_dry_run_and_reports_proposed_changes(self, fetch):
        client = self._client()
        mapping = self._map(client)
        fetch.return_value = {
            "rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [],
            "duplicate_external_ids": 0,
        }
        output = StringIO()
        call_command(
            "alegra_diagnosticar_regularizacion_tributaria", "--all", "--initial-precedence", stdout=output,
        )
        text = output.getvalue()
        self.assertIn("Regularización inicial Alegra (DRY-RUN)", text)
        self.assertIn("regimen_tributario=1", text)
        self.assertIn("escrituras_db=0; escrituras_alegra=0", text)
        mapping.refresh_from_db()
        client.refresh_from_db()
        self.assertEqual(mapping.metadata, {"source": "alegra_initial_import"})
        self.assertEqual(client.regimen_tributario, "")

    @patch("tienda.management.commands.alegra_aplicar_regularizacion_inicial.fetch_customer_contacts")
    def test_initial_application_dry_run_does_not_write(self, fetch):
        client = self._client()
        mapping = self._map(client)
        fetch.return_value = {
            "rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [],
            "duplicate_external_ids": 0,
        }
        output = StringIO()
        call_command("alegra_aplicar_regularizacion_inicial", "--all", stdout=output)
        client.refresh_from_db()
        self.assertIn("Modo=DRY-RUN", output.getvalue())
        self.assertIn("escrituras_alegra=0", output.getvalue())
        self.assertEqual(client.regimen_tributario, "")
        self.assertFalse(SyncAuditLog.objects.filter(operation="regularizacion_inicial_alegra").exists())
        self.assertEqual(mapping.metadata, {"source": "alegra_initial_import"})

    @patch("tienda.management.commands.alegra_aplicar_regularizacion_inicial.fetch_customer_contacts")
    def test_initial_application_requires_confirmation_and_system_authorization(self, fetch):
        with self.assertRaises(CommandError):
            call_command("alegra_aplicar_regularizacion_inicial", "--all", "--apply", stdout=StringIO())
        fetch.assert_not_called()

    @patch("tienda.management.commands.alegra_aplicar_regularizacion_inicial.fetch_customer_contacts")
    def test_initial_application_updates_fields_and_audits_without_baseline(self, fetch):
        client = self._client()
        mapping = self._map(client)
        fetch.return_value = {
            "rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [],
            "duplicate_external_ids": 0,
        }
        with patch.dict("os.environ", {"ALEGRA_HISTORICAL_TAX_REGULARIZATION_ENABLED": "true"}, clear=False):
            call_command(
                "alegra_aplicar_regularizacion_inicial", "--all", "--apply",
                "--confirm", "APLICAR REGULARIZACION INICIAL ALEGRA", "--batch-id", "batch-test",
                stdout=StringIO(),
            )
        client.refresh_from_db()
        mapping.refresh_from_db()
        self.assertEqual(client.regimen_tributario, "COMMON_REGIME")
        self.assertEqual(client.tipo_cliente, Cliente.TIPO_EMPRESA)
        self.assertEqual(client.digito_verificacion, "0")
        self.assertEqual(mapping.metadata, {"source": "alegra_initial_import"})
        audit = SyncAuditLog.objects.get(operation="regularizacion_inicial_alegra", result=SyncAuditLog.RESULT_SUCCESS)
        self.assertEqual(audit.metadata["batch_id"], "batch-test")
        self.assertFalse(audit.metadata["baseline_changed"])

    @patch("tienda.management.commands.alegra_aplicar_regularizacion_inicial.fetch_customer_contacts")
    def test_initial_application_blocks_incomplete_coverage_before_writes(self, fetch):
        client = self._client()
        fetch.return_value = {
            "rows": [], "pages": 0, "coverage": "incomplete", "errors": ["GET error"],
            "duplicate_external_ids": 0,
        }
        with patch.dict("os.environ", {"ALEGRA_HISTORICAL_TAX_REGULARIZATION_ENABLED": "true"}, clear=False):
            with self.assertRaises(CommandError):
                call_command(
                    "alegra_aplicar_regularizacion_inicial", "--all", "--apply",
                    "--confirm", "APLICAR REGULARIZACION INICIAL ALEGRA", stdout=StringIO(),
                )
        client.refresh_from_db()
        self.assertEqual(client.regimen_tributario, "")
        self.assertFalse(SyncAuditLog.objects.filter(operation="regularizacion_inicial_alegra").exists())

    @patch("tienda.management.commands.alegra_aplicar_regularizacion_inicial.fetch_customer_contacts")
    def test_initial_application_rejects_reused_batch_id(self, fetch):
        client = self._client()
        self._map(client)
        fetch.return_value = {
            "rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [],
            "duplicate_external_ids": 0,
        }
        with patch.dict("os.environ", {"ALEGRA_HISTORICAL_TAX_REGULARIZATION_ENABLED": "true"}, clear=False):
            call_command(
                "alegra_aplicar_regularizacion_inicial", "--all", "--apply",
                "--confirm", "APLICAR REGULARIZACION INICIAL ALEGRA", "--batch-id", "batch-repeat",
                stdout=StringIO(),
            )
            with self.assertRaises(CommandError):
                call_command(
                    "alegra_aplicar_regularizacion_inicial", "--all", "--apply",
                    "--confirm", "APLICAR REGULARIZACION INICIAL ALEGRA", "--batch-id", "batch-repeat",
                    stdout=StringIO(),
                )

    @patch("tienda.management.commands.alegra_aplicar_regularizacion_inicial.fetch_customer_contacts")
    def test_initial_application_rolls_back_client_when_save_fails(self, fetch):
        client = self._client()
        self._map(client)
        fetch.return_value = {
            "rows": [self._remote()], "pages": 1, "coverage": "complete", "errors": [],
            "duplicate_external_ids": 0,
        }
        with patch.dict("os.environ", {"ALEGRA_HISTORICAL_TAX_REGULARIZATION_ENABLED": "true"}, clear=False):
            with patch.object(Cliente, "full_clean", side_effect=ValueError("simulated save failure")):
                call_command(
                    "alegra_aplicar_regularizacion_inicial", "--all", "--apply",
                    "--confirm", "APLICAR REGULARIZACION INICIAL ALEGRA", stdout=StringIO(),
                )
        client.refresh_from_db()
        self.assertEqual(client.regimen_tributario, "")
        self.assertTrue(SyncAuditLog.objects.filter(
            operation="regularizacion_inicial_alegra", result=SyncAuditLog.RESULT_ERROR,
        ).exists())

