"""Consulta dirigida GET-only de contactos pendientes; nunca importa."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_client_import import apply_create_plan
from tienda.services.alegra_preimport_clients import ACTION_CREATE_LOCAL, build_preimport_plan, fetch_contacts_by_id


class Command(BaseCommand):
    help = "Recupera contactos Alegra por ID para revisión; no crea clientes ni mapeos."

    def add_arguments(self, parser):
        parser.add_argument("--external-id", nargs="+", required=True, help="IDs externos proporcionados por una fuente local autorizada.")
        parser.add_argument("--apply", action="store_true", help="Crea localmente solo los contactos recuperados y elegibles.")
        parser.add_argument("--confirm", default="", help="Para --apply: RECUPERAR CLIENTES LOCALMENTE")
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--output", default="docs/auditorias/betta_recuperacion_dirigida_clientes_2026_10.md")

    def handle(self, *args, **options):
        self._assert_local_sqlite()
        if options["apply"] and options["confirm"] != "RECUPERAR CLIENTES LOCALMENTE":
            raise CommandError(' --apply requiere --confirm "RECUPERAR CLIENTES LOCALMENTE".')
        try:
            result = fetch_contacts_by_id(AlegraReadOnlyClient(timeout=options["timeout"]), options["external_id"])
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        applied = None
        if options["apply"]:
            system = ExternalSystem.objects.filter(code="alegra").first()
            if not system:
                raise CommandError("No existe el sistema externo Alegra local.")
            active_maps = ExternalObjectMap.objects.filter(system=system, resource_type="contacts", status=ExternalObjectMap.STATUS_ACTIVE)
            all_maps = ExternalObjectMap.objects.filter(system=system, resource_type="contacts")
            plan = build_preimport_plan(result["recovered"], local_clients=Cliente.objects.only("id", "identificacion", "nombre"), active_maps=active_maps, all_maps=all_maps)
            if result["errors"] or result["incomplete"] or any(item["action"] != ACTION_CREATE_LOCAL for item in plan["plans"]):
                raise CommandError("Recuperación bloqueada: algún contacto no es CREATE_LOCAL y requiere revisión.")
            applied = apply_create_plan(result["recovered"], system=system)
        report = "\n".join([
            "# Recuperación dirigida de clientes Alegra",
            "",
            "Consulta GET-only en memoria. No se modificaron clientes, staging ni mapeos.",
            "",
            f"- Referencias únicas solicitadas: {len(result['requested'])}.",
            f"- Contactos recuperados: {len(result['recovered'])}.",
            f"- Respuestas incompletas: {len(result['incomplete'])}.",
            f"- Errores: {len(result['errors'])}.",
            f"- Aplicados localmente: {applied['counts'].get('CREATE_LOCAL', 0) if applied else 0}.",
            f"- Errores de aplicación: {len(applied['errors']) if applied else 0}.",
            "- Los identificadores completos no se escriben en este informe.",
            "",
            "La vinculación o creación local requiere una revisión posterior explícita.",
            "",
        ])
        output = Path(options["output"])
        if not output.is_absolute():
            output = Path.cwd() / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        mode = "aplicada" if applied is not None else "simulada"
        self.stdout.write(self.style.SUCCESS(f"Recuperación {mode}: {len(result['recovered'])} recuperado(s); informe: {output}"))

    @staticmethod
    def _assert_local_sqlite():
        database = settings.DATABASES["default"]
        from pathlib import Path
        if "sqlite3" not in str(database.get("ENGINE", "")) or Path(str(database.get("NAME", ""))).resolve() != (Path(settings.BASE_DIR) / "db.sqlite3").resolve():
            raise CommandError("Ejecución bloqueada: se requiere SQLite local db.sqlite3.")
