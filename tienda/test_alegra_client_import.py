from types import SimpleNamespace
from unittest.mock import patch

from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase

from tienda.management.commands.alegra_import_clientes import Command, CONFIRMATION
from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_client_import import apply_create_plan
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_preimport_clients import build_preimport_plan


def customer(external_id="C-1", identification="900123456"):
    return {
        "id": external_id,
        "name": "Cliente de prueba",
        "identification": identification,
        "identificationType": "nit",
        "type": ["client", "company"],
        "email": f"{external_id.lower()}@example.test",
        "status": "active",
    }


class ClientImportApplyTests(TestCase):
    def setUp(self):
        self.system = get_alegra_system()

    def test_apply_is_idempotent(self):
        row = customer()
        first = apply_create_plan([row], system=self.system)
        second = apply_create_plan([row], system=self.system)
        self.assertEqual(first["counts"]["CREATE_LOCAL"], 1)
        self.assertEqual(second["counts"]["NO_ACTION"], 1)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(ExternalObjectMap.objects.filter(resource_type="contacts").count(), 1)
        self.assertEqual(Cliente.objects.first().puntos_venta.count(), 0)

    def test_mapping_failure_rolls_back_client(self):
        row = customer("C-ROLLBACK", "900555666")
        with patch("tienda.services.alegra_client_import.ExternalObjectMap.objects.create", side_effect=IntegrityError("mapping failed")):
            result = apply_create_plan([row], system=self.system)
        self.assertEqual(result["counts"]["FAILED"], 1)
        self.assertEqual(Cliente.objects.count(), 0)
        self.assertEqual(ExternalObjectMap.objects.filter(resource_type="contacts").count(), 0)

    def test_existing_normalized_identity_is_not_overwritten(self):
        Cliente.objects.create(nombre="Existente", identificacion="900-123-456")
        result = apply_create_plan([customer()], system=self.system)
        self.assertEqual(result["counts"]["REVIEW_CONFLICT"], 1)
        self.assertEqual(Cliente.objects.count(), 1)

    def test_invalid_rows_are_not_created(self):
        result = apply_create_plan([{"id": "C-BAD", "name": "Sin ID", "type": ["client"]}], system=self.system)
        self.assertEqual(result["counts"]["SKIP_INVALID"], 1)
        self.assertEqual(Cliente.objects.count(), 0)

    def test_existing_point_of_sale_is_preserved(self):
        client = Cliente.objects.create(nombre="Local")
        from tienda.models import ClientePuntoVenta
        ClientePuntoVenta.objects.create(cliente=client, nombre="Principal")
        result = apply_create_plan([customer("C-OTHER", "700333444")], system=self.system)
        self.assertEqual(result["counts"]["CREATE_LOCAL"], 1)
        self.assertEqual(client.puntos_venta.count(), 1)


class ClientImportSafetyTests(TestCase):
    def test_apply_requires_exact_confirmation(self):
        options = {
            "apply": True, "confirm": "incorrecto", "limit": 1, "max_pages": 1,
            "pause": 0, "max_retries": 0, "timeout": 1, "output": "ignored.md", "dry_run": False,
        }
        with patch.object(Command, "_assert_local_sqlite"):
            with self.assertRaises(CommandError):
                Command().handle(**options)
        self.assertEqual(CONFIRMATION, "IMPORTAR CLIENTES LOCALMENTE")

    def test_incomplete_coverage_is_blocked(self):
        with self.assertRaises(Exception):
            Command._assert_apply_allowed(
                {"coverage": "limited", "errors": [], "stopped_reason": "max_pages", "duplicate_external_ids": 0},
                {"counts": {}},
                SimpleNamespace(),
            )

    def test_plan_marks_conflicting_active_mapping(self):
        wrong_mapping = SimpleNamespace(external_id="C-1", object_id=99, status="active", content_type_id=999)
        plan = build_preimport_plan([customer()], all_maps=[wrong_mapping])
        self.assertEqual(plan["counts"]["REVIEW_CONFLICT"], 1)
