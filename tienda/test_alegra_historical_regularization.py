from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.utils import timezone

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_import import get_alegra_system
from tienda.management.commands.alegra_regularizar_clientes_historicos import (
    apply_regularization,
    plan_regularization,
)


class HistoricalRegularizationTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()
        self.client = Cliente.objects.create(
            tipo_cliente=Cliente.TIPO_EMPRESA,
            nombre="Empresa histórica",
            razon_social="Empresa histórica",
            identificacion="900123456",
            email="historica@example.test",
        )
        self.mapping = ExternalObjectMap.objects.create(
            system=self.system,
            resource_type="contacts",
            external_id="A-HIST-1",
            content_type=ContentType.objects.get_for_model(Cliente),
            object_id=self.client.pk,
            metadata={"source": "alegra_initial_import", "phase": "initial"},
        )
        self.remote = {
            "id": "A-HIST-1",
            "name": "Empresa histórica",
            "identification": "900123456",
            "identificationObject": {
                "type": "NIT",
                "number": "900123456",
                "dv": "0",
            },
            "kindOfPerson": "LEGAL_ENTITY",
            "regime": "COMMON_REGIME",
            "type": ["client"],
            "status": "active",
            "email": "historica@example.test",
        }

    def test_blank_tax_fields_are_safe_and_preserve_zero_dv(self):
        plan = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(plan["state"], "SAFE")
        self.assertEqual(plan["changes"]["tipo_identificacion"]["after"], "nit")
        self.assertEqual(plan["changes"]["regimen_tributario"]["after"], "COMMON_REGIME")
        self.assertEqual(plan["changes"]["digito_verificacion"]["after"], "0")
        self.client.refresh_from_db()
        self.assertEqual(self.client.tipo_identificacion, "")
        self.assertEqual(self.client.digito_verificacion, "")

    def test_nonempty_tax_difference_is_manual_review(self):
        self.client.tipo_identificacion = Cliente.ID_CC
        self.client.save(update_fields=["tipo_identificacion", "fecha_actualizacion"])
        self.mapping.created_at = timezone.now()
        self.mapping.save(update_fields=["created_at", "updated_at"])

        plan = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(plan["state"], "REVIEW")
        self.assertIn("tipo_identificacion", plan["conflicts"])

    def test_kind_mismatch_is_not_inferred_from_nit(self):
        self.client.tipo_cliente = Cliente.TIPO_PERSONA
        self.client.save(update_fields=["tipo_cliente", "fecha_actualizacion"])
        self.mapping.created_at = timezone.now()
        self.mapping.save(update_fields=["created_at", "updated_at"])

        plan = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(plan["state"], "SAFE")
        self.assertEqual(plan["changes"]["tipo_cliente"]["before"], Cliente.TIPO_PERSONA)
        self.assertEqual(plan["changes"]["tipo_cliente"]["after"], Cliente.TIPO_EMPRESA)

    def test_person_entity_is_regularized_from_alegra(self):
        self.client.tipo_cliente = Cliente.TIPO_EMPRESA
        self.client.save(update_fields=["tipo_cliente", "fecha_actualizacion"])
        self.mapping.created_at = timezone.now()
        self.mapping.save(update_fields=["created_at", "updated_at"])
        remote = {
            **self.remote,
            "kindOfPerson": "PERSON_ENTITY",
            "identificationObject": {"type": "CC", "number": "10101010"},
            "regime": "SIMPLIFIED_REGIME",
        }

        plan = plan_regularization(self.client, self.mapping, remote)

        self.assertEqual(plan["state"], "SAFE")
        self.assertEqual(plan["changes"]["tipo_cliente"]["after"], Cliente.TIPO_PERSONA)

    def test_kind_change_after_import_is_reviewed(self):
        from datetime import timedelta

        self.mapping.created_at = timezone.now() - timedelta(minutes=1)
        self.mapping.save(update_fields=["created_at", "updated_at"])
        self.client.tipo_cliente = Cliente.TIPO_PERSONA
        self.client.save(update_fields=["tipo_cliente", "fecha_actualizacion"])

        plan = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(plan["state"], "REVIEW")
        self.assertIn("modificado después", plan["reason"])

    def test_post_import_local_change_is_review(self):
        self.client.nombre = "Cambio posterior"
        self.client.save(update_fields=["nombre", "fecha_actualizacion"])

        plan = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(plan["state"], "REVIEW")
        self.assertNotEqual(plan["state"], "SAFE")
        self.assertEqual(self.client.tipo_identificacion, "")

    def test_apply_updates_only_tax_fields_and_sets_baseline(self):
        plan = plan_regularization(self.client, self.mapping, self.remote)
        result = apply_regularization(
            [{"mapping": self.mapping, "remote": self.remote, "plan": plan}],
            system=self.system,
        )

        self.assertEqual(result["counts"]["UPDATED"], 1)
        self.client.refresh_from_db()
        self.mapping.refresh_from_db()
        self.assertEqual(self.client.tipo_identificacion, Cliente.ID_NIT)
        self.assertEqual(self.client.regimen_tributario, "COMMON_REGIME")
        self.assertEqual(self.client.digito_verificacion, "0")
        self.assertEqual(self.client.tipo_cliente, Cliente.TIPO_EMPRESA)
        self.assertEqual(self.client.nombre, "Empresa histórica")
        self.assertEqual(self.mapping.external_id, "A-HIST-1")
        self.assertTrue(self.mapping.metadata.get("last_confirmed"))
        self.assertEqual(
            SyncAuditLog.objects.filter(operation="regularize_historical_alegra_client").count(),
            1,
        )

    def test_second_plan_after_application_requires_no_action(self):
        plan = plan_regularization(self.client, self.mapping, self.remote)
        apply_regularization(
            [{"mapping": self.mapping, "remote": self.remote, "plan": plan}],
            system=self.system,
        )
        self.mapping.refresh_from_db()
        self.client.refresh_from_db()

        repeat = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(repeat["state"], "NO_ACTION")

    def test_kind_regularization_is_idempotent(self):
        self.client.tipo_cliente = Cliente.TIPO_PERSONA
        self.client.save(update_fields=["tipo_cliente", "fecha_actualizacion"])
        self.mapping.created_at = timezone.now()
        self.mapping.save(update_fields=["created_at", "updated_at"])
        plan = plan_regularization(self.client, self.mapping, self.remote)
        result = apply_regularization(
            [{"mapping": self.mapping, "remote": self.remote, "plan": plan}],
            system=self.system,
        )

        self.assertEqual(result["counts"]["UPDATED"], 1)
        self.mapping.refresh_from_db()
        self.client.refresh_from_db()
        self.assertEqual(self.client.tipo_cliente, Cliente.TIPO_EMPRESA)
        self.assertEqual(plan_regularization(self.client, self.mapping, self.remote)["state"], "NO_ACTION")

    def test_non_historical_mapping_is_excluded(self):
        self.mapping.metadata = {"source": "manual"}
        self.mapping.save(update_fields=["metadata", "updated_at"])

        plan = plan_regularization(self.client, self.mapping, self.remote)

        self.assertEqual(plan["state"], "EXCLUDED")
