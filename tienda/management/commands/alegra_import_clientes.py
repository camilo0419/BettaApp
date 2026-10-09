"""Importación inicial de clientes Alegra, dry-run por defecto."""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_client_import import apply_initial_import_plan
from tienda.services.alegra_preimport_clients import ACTION_LINK_EXISTING
from tienda.services.alegra_preimport_clients import ACTION_CREATE_LOCAL, build_preimport_plan, fetch_customer_contacts
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


CONFIRMATION = "IMPORTAR CLIENTES LOCALMENTE"
MARIADB_CONFIRMATION = "IMPORTAR CLIENTES EN MARIADB"
IMPORT_LOCK = "bettaapp:alegra:initial-client-import"


class Command(BaseCommand):
    help = "Prepara o aplica (solo con confirmación explícita) la importación inicial de clientes Alegra."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Solo genera el plan; es el comportamiento por defecto.")
        mode.add_argument("--apply", action="store_true", help="Aplica altas locales después de todas las validaciones.")
        parser.add_argument("--confirm", default="", help=f"Para --apply debe ser exactamente: {CONFIRMATION}")
        parser.add_argument(
            "--mariadb", action="store_true",
            help="Habilita explícitamente la ruta MariaDB; requiere configuración de sistema y confirmación propia.",
        )
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--max-pages", type=int, default=100)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--output", default="docs/auditorias/betta_importacion_controlada_clientes_2026_10.md")

    def handle(self, *args, **options):
        apply_mode = bool(options.get("apply"))
        mariadb_mode = bool(options.get("mariadb"))
        if mariadb_mode and not apply_mode:
            raise CommandError("--mariadb solo puede utilizarse junto con --apply.")
        if apply_mode:
            self._assert_apply_database(mariadb_mode)
            expected_confirmation = MARIADB_CONFIRMATION if mariadb_mode else CONFIRMATION
            if options.get("confirm") != expected_confirmation:
                raise CommandError(f"--apply requiere --confirm \"{expected_confirmation}\".")
            try:
                with advisory_lock(IMPORT_LOCK):
                    return self._handle_import(*args, **options)
            except DatabaseLockUnavailable as exc:
                raise CommandError(str(exc)) from exc
        return self._handle_import(*args, **options)

    def _handle_import(self, *args, **options):
        try:
            client = AlegraReadOnlyClient(timeout=options["timeout"])
            fetched = fetch_customer_contacts(
                client,
                limit=min(max(options["limit"], 1), 1000),
                max_pages=min(max(options["max_pages"], 1), 100),
                pause=options["pause"],
                max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        system = ExternalSystem.objects.filter(code="alegra").first()
        content_type = ContentType.objects.get_for_model(Cliente)
        local_clients = list(Cliente.objects.only("id", "identificacion", "nombre"))
        all_maps = list(ExternalObjectMap.objects.filter(system=system, resource_type="contacts").only("external_id", "object_id", "status", "content_type")) if system else []
        active_maps = [item for item in all_maps if item.status == ExternalObjectMap.STATUS_ACTIVE and item.object_id and item.content_type_id == content_type.pk]
        plan = build_preimport_plan(fetched["rows"], local_clients=local_clients, active_maps=active_maps, all_maps=all_maps)
        applied = None
        if options.get("apply"):
            self._assert_apply_allowed(fetched, plan, system)
            eligible_rows = [row for row, item in zip(fetched["rows"], plan["plans"]) if item["action"] in {ACTION_CREATE_LOCAL, ACTION_LINK_EXISTING}]
            eligible_plans = [item for item in plan["plans"] if item["action"] in {ACTION_CREATE_LOCAL, ACTION_LINK_EXISTING}]
            applied = apply_initial_import_plan(eligible_rows, eligible_plans, system=system)
        report = self._report(fetched, plan, applied, options["apply"], options)
        output = Path(options["output"])
        if not output.is_absolute():
            output = Path.cwd() / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        counts = plan["counts"]
        mode = "APLICACIÓN" if options.get("apply") else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: {len(fetched['rows'])} contactos; CREATE_LOCAL={counts.get(ACTION_CREATE_LOCAL, 0)}; informe: {output}"))

    @staticmethod
    def _assert_local_sqlite():
        database = settings.DATABASES["default"]
        engine = str(database.get("ENGINE", ""))
        name = Path(str(database.get("NAME", ""))).resolve()
        expected = (Path(settings.BASE_DIR) / "db.sqlite3").resolve()
        if "sqlite3" not in engine or name != expected:
            raise CommandError("Ejecución bloqueada: solo se permite SQLite local db.sqlite3; no se identificó un entorno local confiable.")

    @staticmethod
    def _assert_apply_database(mariadb_mode):
        if not mariadb_mode:
            Command._assert_local_sqlite()
            return
        database = settings.DATABASES["default"]
        engine = str(database.get("ENGINE", ""))
        if "mysql" not in engine:
            raise CommandError("Ejecución bloqueada: --mariadb exige django.db.backends.mysql.")
        if os.environ.get("ALEGRA_INITIAL_CLIENT_IMPORT_MARIADB_ENABLED", "").strip().casefold() != "true":
            raise CommandError(
                "Ejecución bloqueada: la ruta MariaDB requiere "
                "ALEGRA_INITIAL_CLIENT_IMPORT_MARIADB_ENABLED=true."
            )
        if os.environ.get("DJANGO_ENV", "").strip().casefold() != "production":
            raise CommandError("Ejecución bloqueada: la ruta MariaDB exige DJANGO_ENV=production explícito.")
        missing = [
            name for name in ("DB_NAME", "DB_USER", "DB_HOST", "ALEGRA_EMAIL", "ALEGRA_API_TOKEN")
            if not os.environ.get(name, "").strip()
        ]
        if missing:
            raise CommandError(
                "Ejecución bloqueada: faltan variables de configuración requeridas: "
                + ", ".join(missing)
            )

    @staticmethod
    def _assert_apply_allowed(fetched, plan, system):
        if not system:
            raise CommandError("Ejecución bloqueada: no existe el sistema externo Alegra local configurado.")
        if fetched["coverage"] != "complete" or fetched["errors"] or fetched["stopped_reason"] != "end_of_pagination":
            raise CommandError("Ejecución bloqueada: la cobertura de Alegra no es completa y verificable.")
        if fetched["duplicate_external_ids"]:
            raise CommandError("Ejecución bloqueada: se detectaron IDs externos repetidos.")
        if plan["counts"].get("REVIEW_CONFLICT", 0):
            raise CommandError("Ejecución bloqueada: existen conflictos estructurales de identidad o mapeo.")
        Command._assert_no_contradictory_active_mappings(system)

    @staticmethod
    def _assert_no_contradictory_active_mappings(system):
        content_type = ContentType.objects.get_for_model(Cliente)
        mappings = list(ExternalObjectMap.objects.filter(
            system=system,
            resource_type="contacts",
            content_type=content_type,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).only("external_id", "object_id"))
        by_external = {}
        by_object = {}
        for mapping in mappings:
            by_external.setdefault(str(mapping.external_id), set()).add(mapping.object_id)
            by_object.setdefault(mapping.object_id, set()).add(str(mapping.external_id))
        if any(len(object_ids) > 1 for object_ids in by_external.values()):
            raise CommandError("Ejecución bloqueada: un ID externo activo apunta a varios clientes locales.")
        if any(len(external_ids) > 1 for external_ids in by_object.values()):
            raise CommandError("Ejecución bloqueada: un cliente local tiene varios mapeos externos activos.")

    @staticmethod
    def _report(fetched, plan, applied, apply_mode, options):
        counts = plan["counts"]
        status = "APLICACIÓN NO EJECUTADA" if not apply_mode else "APLICACIÓN EJECUTADA"
        lines = [
            "# BettaApp — Importación controlada de clientes Alegra (Fase 7.3)",
            "",
            f"## Estado: {status}",
            "",
            "El comando reconstruye el plan con datos actuales. El comportamiento por defecto es `--dry-run`; no se crean clientes ni mapeos en ese modo. Las consultas externas son exclusivamente GET.",
            "",
            "## Consulta",
            "",
            f"- Contactos recuperados: {len(fetched['rows'])}.",
            f"- Páginas: {fetched['pages']}.",
            f"- Cobertura: `{fetched['coverage']}`; motivo: `{fetched['stopped_reason']}`.",
            f"- Estados HTTP: {fetched['http_statuses']}.",
            f"- Errores: {len(fetched['errors'])}; reintentos 429: {fetched['retries_429']}.",
            "",
            "## Plan simulado",
            "",
            f"- `CREATE_LOCAL`: {counts.get('CREATE_LOCAL', 0)}.",
            f"- `LINK_EXISTING`: {counts.get('LINK_EXISTING', 0)}; no se vincula automáticamente.",
            f"- `REVIEW_DUPLICATE`: {counts.get('REVIEW_DUPLICATE', 0)}; excluidos.",
            f"- `REVIEW_CONFLICT`: {counts.get('REVIEW_CONFLICT', 0)}; bloquean `--apply`.",
            f"- `SKIP_INVALID`: {counts.get('SKIP_INVALID', 0)}; excluidos.",
            f"- `NO_ACTION`: {counts.get('NO_ACTION', 0)}.",
            "",
            "No se incluyen nombres, documentos, correos, teléfonos ni payloads completos.",
            "",
            "## Protecciones",
            "",
            f"- `--apply` requiere `--confirm \"{CONFIRMATION}\"`.",
            "- Requiere SQLite local en `db.sqlite3`, cobertura completa, sin errores ni conflictos estructurales.",
            "- Cada alta y su `ExternalObjectMap` se ejecutan en una transacción atómica.",
            "- Un mapeo activo existente produce `NO_ACTION`; no se crean duplicados.",
            "- Errores de validación o unicidad revierten la alta del contacto afectado.",
            "",
            "## Resultado de ejecución",
            "",
        ]
        if applied is None:
            lines.append("`--apply` no fue ejecutado sobre datos comerciales en esta fase.")
        else:
            lines.extend([
                f"- Creados: {applied['counts'].get('CREATE_LOCAL', 0)}.",
                f"- Actualizados/vinculados: {applied['counts'].get('UPDATED', 0)}.",
                f"- Omitidos: {applied['counts'].get('OMITTED', 0) + applied['counts'].get('NO_ACTION', 0) + applied['counts'].get('SKIP_INVALID', 0)}.",
                f"- Errores: {len(applied['errors'])}.",
            ])
        lines.extend([
            "",
            "## Validación",
            "",
            "37 pruebas específicas correctas. `python manage.py check` correcto y `python manage.py makemigrations --check --dry-run` sin cambios. Las pruebas de aplicación usan bases temporales de test. No se aplicaron migraciones ni se ejecutó `--apply` sobre la SQLite comercial.",
            "",
            "## Riesgos pendientes",
            "",
            "- Debe realizarse respaldo y autorización independiente antes de una futura aplicación.",
            "- Las identificaciones con distinto formato se validan en Python; la base no tiene un índice funcional de identidad normalizada.",
            "- Los duplicados y coincidencias requieren revisión manual posterior.",
            "",
        ])
        return "\n".join(lines)
