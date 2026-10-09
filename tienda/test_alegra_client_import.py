from types import SimpleNamespace
import os
from unittest.mock import patch

from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase
from django.conf import settings

from tienda.management.commands.alegra_import_clientes import Command, CONFIRMATION
from tienda.models import Cliente, ExternalObjectMap
from tienda.services.alegra_client_import import apply_create_plan, apply_initial_import_plan
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

    def test_inequivocal_match_is_updated_and_linked_without_second_client(self):
        client = Cliente.objects.create(nombre="Nombre local", identificacion="900123456")
        from tienda.models import ClientePuntoVenta
        ClientePuntoVenta.objects.create(cliente=client, nombre="Principal")
        row = customer()
        plan = build_preimport_plan([row], local_clients=[client], active_maps=[], all_maps=[])
        self.assertEqual(plan["plans"][0]["action"], "LINK_EXISTING")
        result = apply_initial_import_plan([row], plan["plans"], system=self.system)
        client.refresh_from_db()
        self.assertEqual(result["counts"]["UPDATED"], 1)
        self.assertEqual(Cliente.objects.count(), 1)
        self.assertEqual(client.nombre, "Cliente de prueba")
        self.assertEqual(client.puntos_venta.count(), 1)
        self.assertEqual(ExternalObjectMap.objects.filter(resource_type="contacts").count(), 1)

    def test_initial_import_is_idempotent_for_linked_match(self):
        client = Cliente.objects.create(nombre="Nombre local", identificacion="900123456")
        row = customer()
        plan = build_preimport_plan([row], local_clients=[client], active_maps=[], all_maps=[])
        apply_initial_import_plan([row], plan["plans"], system=self.system)
        second = apply_initial_import_plan([row], plan["plans"], system=self.system)
        self.assertEqual(second["counts"]["NO_ACTION"], 1)
        self.assertEqual(Cliente.objects.count(), 1)

    def test_ambiguous_match_is_blocked_without_update(self):
        first = Cliente.objects.create(nombre="Primero", identificacion="900-123-456")
        second = Cliente.objects.create(nombre="Segundo", identificacion="900123456")
        row = customer()
        plan = build_preimport_plan([row], local_clients=[first, second], active_maps=[], all_maps=[])
        self.assertEqual(plan["plans"][0]["action"], "REVIEW_CONFLICT")
        result = apply_initial_import_plan([row], plan["plans"], system=self.system)
        self.assertEqual(result["counts"]["OMITTED"], 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.nombre, "Primero")
        self.assertEqual(second.nombre, "Segundo")


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

    def test_mariadb_apply_requires_explicit_system_enablement(self):
        database = {
            "ENGINE": "django.db.backends.mysql",
            "NAME": "bettaapp",
            "USER": "bettaapp",
            "HOST": "db.example.test",
        }
        with patch.object(settings, "DATABASES", {"default": database}), patch.dict(
            os.environ,
            {"DJANGO_ENV": "production", "DB_NAME": "bettaapp", "DB_USER": "bettaapp", "DB_HOST": "db.example.test", "ALEGRA_EMAIL": "configured", "ALEGRA_API_TOKEN": "configured"},
            clear=False,
        ):
            with self.assertRaises(CommandError):
                Command._assert_apply_database(True)

    def test_mariadb_apply_can_be_validated_without_connecting_or_applying(self):
        database = {
            "ENGINE": "django.db.backends.mysql",
            "NAME": "bettaapp",
            "USER": "bettaapp",
            "HOST": "db.example.test",
        }
        with patch.object(settings, "DATABASES", {"default": database}), patch.dict(
            os.environ,
            {
                "DJANGO_ENV": "production",
                "DB_NAME": "bettaapp",
                "DB_USER": "bettaapp",
                "DB_HOST": "db.example.test",
                "ALEGRA_EMAIL": "configured",
                "ALEGRA_API_TOKEN": "configured",
                "ALEGRA_INITIAL_CLIENT_IMPORT_MARIADB_ENABLED": "true",
            },
            clear=False,
        ):
            Command._assert_apply_database(True)

    def test_contradictory_active_mappings_block_apply(self):
        system = get_alegra_system()
        fake_qs = SimpleNamespace(only=lambda *args: [
            SimpleNamespace(external_id="one", object_id=10),
            SimpleNamespace(external_id="two", object_id=10),
        ])
        with patch.object(ExternalObjectMap.objects, "filter", return_value=fake_qs):
            with self.assertRaises(CommandError):
                Command._assert_no_contradictory_active_mappings(system)

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
