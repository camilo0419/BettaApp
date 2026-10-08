"""Orquestador seguro de sincronización bidireccional de clientes."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from tienda.models import Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync, CONFLICT
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_client_import import apply_create_plan, apply_remote_updates
from tienda.services.alegra_preimport_clients import ACTION_CREATE_LOCAL, build_preimport_plan, fetch_customer_contacts


CONFIRMATION = "APLICAR CAMBIOS LOCALES"


class ExecutionLock:
    def __init__(self, name="bettaapp_alegra_client_sync.lock"):
        self.path = Path(tempfile.gettempdir()) / name
        self.fd = None

    def __enter__(self):
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode("ascii"))
            return self
        except FileExistsError as exc:
            raise CommandError("Ya existe una sincronización de clientes en ejecución.") from exc

    def __exit__(self, exc_type, exc_value, traceback):
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


class Command(BaseCommand):
    help = "Diagnóstico y simulación segura de sincronización bidireccional de clientes."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Diagnóstico sin escrituras; comportamiento por defecto.")
        mode.add_argument("--apply-local", action="store_true", help="Aplica cambios locales después de validaciones explícitas.")
        parser.add_argument("--confirm", default="", help=f'Para --apply-local: {CONFIRMATION}')
        parser.add_argument("--simulate-outbound", action="store_true", help="Genera planes salientes simulados en memoria.")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--output", default="docs/auditorias/betta_sync_clientes_ultima_ejecucion.md")

    def handle(self, *args, **options):
        self._assert_local_sqlite()
        if options["apply_local"] and options["confirm"] != CONFIRMATION:
            raise CommandError(f'--apply-local requiere --confirm "{CONFIRMATION}".')
        started = time.monotonic()
        with ExecutionLock():
            try:
                fetched = fetch_customer_contacts(
                    AlegraReadOnlyClient(timeout=options["timeout"]),
                    limit=min(max(options["limit"], 1), 1000),
                    max_pages=min(max(options["max_pages"], 1), 100),
                    pause=options["pause"],
                    max_retries=options["max_retries"],
                )
            except AlegraError as exc:
                raise CommandError(str(exc)) from exc
            system = ExternalSystem.objects.filter(code="alegra").first()
            if not system:
                raise CommandError("No existe el sistema externo Alegra local.")
            content_type = ContentType.objects.get_for_model(Cliente)
            all_maps = list(ExternalObjectMap.objects.filter(system=system, resource_type="contacts").select_related("content_type"))
            active_maps = [item for item in all_maps if item.status == ExternalObjectMap.STATUS_ACTIVE and item.content_type_id == content_type.pk and item.object_id]
            local_clients = list(Cliente.objects.only("id", "nombre", "identificacion", "tipo_cliente", "razon_social", "tipo_identificacion", "digito_verificacion", "email", "telefono", "telefono_secundario", "celular", "direccion", "ciudad", "departamento", "pais", "codigo_postal"))
            plan = build_preimport_plan(fetched["rows"], local_clients=local_clients, active_maps=active_maps, all_maps=all_maps)
            mapped_clients = {str(item.external_id): next((client for client in local_clients if client.pk == item.object_id), None) for item in active_maps}
            baselines = {str(item.external_id): (item.metadata or {}).get("last_confirmed") for item in active_maps if isinstance(item.metadata, dict) and item.metadata.get("last_confirmed")}
            inbound_updates = BidirectionalClientSync().prepare_inbound_updates(fetched["rows"], {key: value for key, value in mapped_clients.items() if value}, baselines)
            mapped_local_ids = [item.object_id for item in active_maps]
            outbound_plans = BidirectionalClientSync().pending_local_clients(local_clients, mapped_local_ids)
            outbound_invalid = sum(1 for item in outbound_plans if not item.payload.get("nombre") or not item.payload.get("identificacion"))
            applied = None
            if options["apply_local"]:
                self._assert_apply_allowed(fetched, plan, inbound_updates, system)
                eligible_rows = [row for row, item in zip(fetched["rows"], plan["plans"]) if item["action"] == ACTION_CREATE_LOCAL]
                created = apply_create_plan(eligible_rows, system=system)
                updated = apply_remote_updates(fetched["rows"], inbound_updates, system=system)
                applied = {"created": created, "updated": updated}
            elapsed = time.monotonic() - started
            if options["apply_local"]:
                SyncAuditLog.objects.create(
                    system=system,
                    operation="sync_clientes_bidireccional",
                    resource="contacts",
                    result=SyncAuditLog.RESULT_SUCCESS if not fetched["errors"] else SyncAuditLog.RESULT_PARTIAL,
                    detail=f"Entrantes: {len(fetched['rows'])}; altas: {plan['counts'].get(ACTION_CREATE_LOCAL, 0)}; salientes pendientes: {len(outbound_plans)}.",
                    metadata={"direction": "both", "pages": fetched["pages"], "coverage": fetched["coverage"], "duration_seconds": round(elapsed, 3), "outbound_simulated": True},
                )
            report = self._report(fetched, plan, inbound_updates, outbound_plans, outbound_invalid, applied, options, elapsed)
            output = Path(options["output"])
            if not output.is_absolute():
                output = Path.cwd() / output
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Sincronización {'local aplicada' if options['apply_local'] else 'simulada'}: informe {output}"))

    @staticmethod
    def _assert_local_sqlite():
        database = settings.DATABASES["default"]
        if "sqlite3" not in str(database.get("ENGINE", "")) or Path(str(database.get("NAME", ""))).resolve() != (Path(settings.BASE_DIR) / "db.sqlite3").resolve():
            raise CommandError("Ejecución bloqueada: solo se permite SQLite local db.sqlite3.")

    @staticmethod
    def _assert_apply_allowed(fetched, plan, inbound_updates, system):
        if not system or fetched["coverage"] != "complete" or fetched["errors"] or fetched["stopped_reason"] != "end_of_pagination":
            raise CommandError("Aplicación local bloqueada: cobertura Alegra incompleta o sistema local no verificable.")
        if fetched["duplicate_external_ids"] or plan["counts"].get("REVIEW_CONFLICT", 0):
            raise CommandError("Aplicación local bloqueada: existen conflictos estructurales.")
        if any(item.get("state") == CONFLICT for item in inbound_updates):
            raise CommandError("Aplicación local bloqueada: existen conflictos de actualización.")

    @staticmethod
    def _report(fetched, plan, inbound, outbound, outbound_invalid, applied, options, elapsed):
        inbound_counts = {}
        for item in inbound:
            inbound_counts[item["state"]] = inbound_counts.get(item["state"], 0) + 1
        lines = [
            "# BettaApp — Motor operativo de sincronización de clientes",
            "",
            f"- Modo: `{'APPLY_LOCAL' if options['apply_local'] else 'DRY_RUN'}`.",
            "- Escrituras externas: deshabilitadas; solo GET a Alegra.",
            f"- Contactos consultados: {len(fetched['rows'])}; páginas: {fetched['pages']}; cobertura: `{fetched['coverage']}`.",
            f"- Motivo de finalización: `{fetched['stopped_reason']}`; errores: {len(fetched['errors'])}.",
            f"- Plan entrante: {plan['counts']}.",
            f"- Actualizaciones vinculadas evaluadas: {inbound_counts}.",
            f"- Clientes locales pendientes de salida: {len(outbound)}; payloads inválidos: {outbound_invalid}.",
            f"- Simulación saliente: {'solicitada' if options['simulate_outbound'] else 'no solicitada'}; ningún mapeo se crea por simulación.",
            f"- Duración: {elapsed:.3f}s.",
            "",
            "Los nombres, documentos, correos, teléfonos y payloads completos no se incluyen en el informe.",
            "",
        ]
        if applied is None:
            lines.append("No se aplicaron cambios locales.")
        else:
            lines.extend([
                f"Altas locales: {applied['created']['counts'].get('CREATE_LOCAL', 0)}.",
                f"Actualizaciones locales: {applied['updated']['counts'].get('UPDATED', 0)}.",
                f"Errores de altas: {len(applied['created']['errors'])}; errores de actualizaciones: {len(applied['updated']['errors'])}.",
            ])
        lines.extend([
            "",
            "Estado: las operaciones hacia Alegra permanecen simuladas y requieren una fase posterior de autorización real.",
        ])
        return "\n".join(lines)
