"""Consulta contactos y aplica únicamente cambios remotos inequívocos."""

import os

from django.core.management.base import BaseCommand, CommandError
from django.contrib.contenttypes.models import ContentType

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_client_import import apply_initial_import_plan
from tienda.services.alegra_inbound_sync import InboundClientSyncService, InboundSyncConflict
from tienda.services.alegra_preimport_clients import ACTION_CREATE_LOCAL, ACTION_LINK_EXISTING, build_preimport_plan, fetch_customer_contacts
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


class Command(BaseCommand):
    help = "Sincroniza cambios inequívocos de contactos Alegra a BettaApp usando únicamente GET."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Recorre todos los contactos hasta el final verificable.")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--apply-new", action="store_true", help="Incorpora candidatos nuevos con confirmación explícita.")
        parser.add_argument("--confirm", default="", help='Para --apply-new: APLICAR NUEVOS CLIENTES ALEGRA')

    def handle(self, *args, **options):
        try:
            with advisory_lock("bettaapp:alegra:inbound:contacts"):
                self._sync(options)
        except DatabaseLockUnavailable as exc:
            raise CommandError(str(exc)) from exc

    def _sync(self, options):
        if options.get("apply_new"):
            if options.get("confirm") != "APLICAR NUEVOS CLIENTES ALEGRA":
                raise CommandError('El modo --apply-new requiere --confirm "APLICAR NUEVOS CLIENTES ALEGRA".')
            if os.environ.get("ALEGRA_INBOUND_NEW_CLIENTS_APPLY_ENABLED", "").strip().casefold() != "true":
                raise CommandError("La incorporación de nuevos clientes está deshabilitada por configuración.")
        try:
            client = AlegraReadOnlyClient(timeout=options["timeout"])
            fetched = fetch_customer_contacts(
                client, limit=None if options["all"] else min(max(options["limit"], 1), 1000),
                max_pages=None if options["all"] else min(max(options["max_pages"], 1), 100),
                pause=options["pause"], max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        if fetched["errors"] or fetched["coverage"] != "complete":
            raise CommandError(
                "Sincronización no aplicada: consulta Alegra incompleta o con errores "
                f"(cobertura={fetched['coverage']}, errores={len(fetched['errors'])})."
            )
        system = ExternalSystem.objects.filter(code="alegra").first()
        if not system:
            raise CommandError("No existe el sistema externo Alegra local.")
        ct = ContentType.objects.get_for_model(Cliente)
        mappings = list(ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=ct,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).select_related("content_type"))
        by_external = {str(item.external_id): item for item in mappings}
        local_clients = list(Cliente.objects.only(
            "id", "nombre", "identificacion", "tipo_cliente", "razon_social", "tipo_identificacion",
            "digito_verificacion", "email", "telefono", "telefono_secundario", "celular", "direccion",
            "ciudad", "departamento", "pais", "codigo_postal",
        ))
        all_maps = list(ExternalObjectMap.objects.filter(system=system, resource_type="contacts").only(
            "external_id", "object_id", "status", "content_type",
        ))
        active_maps = [item for item in all_maps if item.status == ExternalObjectMap.STATUS_ACTIVE and item.object_id and item.content_type_id == ct.pk]
        new_plan = build_preimport_plan(fetched["rows"], local_clients=local_clients, active_maps=active_maps, all_maps=all_maps)
        new_applied = None
        if options.get("apply_new"):
            if fetched["duplicate_external_ids"] or new_plan["counts"].get("REVIEW_CONFLICT", 0):
                raise CommandError("La incorporación de nuevos clientes queda bloqueada por conflictos estructurales.")
            eligible_rows = [row for row, item in zip(fetched["rows"], new_plan["plans"]) if item["action"] in {ACTION_CREATE_LOCAL, ACTION_LINK_EXISTING}]
            eligible_plans = [item for item in new_plan["plans"] if item["action"] in {ACTION_CREATE_LOCAL, ACTION_LINK_EXISTING}]
            new_applied = apply_initial_import_plan(eligible_rows, eligible_plans, system=system)
        applied = conflicts = blocked = 0
        service = InboundClientSyncService(client)
        for row in fetched["rows"]:
            external_id = str(row.get("id") or "")
            mapping = by_external.get(external_id)
            if not mapping:
                continue
            local = mapping.local_object
            if not local:
                blocked += 1
                continue
            try:
                review = service.review(local, mapping, row)
                if review["state"] == "PENDING":
                    result = service.apply(local.pk, mapping.pk, review, review["remote_fields"])
                    applied += result.get("status") == "updated"
                elif review["state"] == "CONFLICT":
                    conflicts += 1
                else:
                    blocked += 1
            except (InboundSyncConflict, AlegraError):
                conflicts += 1
        SyncAuditLog.objects.create(
            system=system, operation="sync_inbound_contacts", resource="contacts",
            result=SyncAuditLog.RESULT_SUCCESS,
            detail=f"Consultados: {len(fetched['rows'])}; aplicados: {applied}; conflictos: {conflicts}; bloqueados: {blocked}.",
            metadata={"pages": fetched["pages"], "coverage": fetched["coverage"], "errors": 0},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Contactos entrantes: consultados={len(fetched['rows'])}, aplicados={applied}, "
            f"conflictos={conflicts}, bloqueados={blocked}, paginas={fetched['pages']}, "
            f"nuevos_propuestos={new_plan['counts'].get(ACTION_CREATE_LOCAL, 0)}, "
            f"nuevos_aplicados={new_applied['counts'].get('CREATE_LOCAL', 0) if new_applied else 0}, "
            f"duplicados={fetched['duplicate_external_ids']}, cobertura={fetched['coverage']}. Solo GET."
        ))
