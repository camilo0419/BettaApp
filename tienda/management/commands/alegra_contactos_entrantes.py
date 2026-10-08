"""Consulta contactos y aplica únicamente cambios remotos inequívocos."""

import time

from django.core.management.base import BaseCommand, CommandError
from django.contrib.contenttypes.models import ContentType

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_inbound_sync import InboundClientSyncService, InboundSyncConflict
from tienda.services.alegra_preimport_clients import fetch_customer_contacts


class Command(BaseCommand):
    help = "Sincroniza cambios inequívocos de contactos Alegra a BettaApp usando únicamente GET."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Recorre todos los contactos hasta el final verificable.")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--max-retries", type=int, default=2)

    def handle(self, *args, **options):
        try:
            client = AlegraReadOnlyClient(timeout=options["timeout"])
            fetched = fetch_customer_contacts(
                client, limit=None if options["all"] else min(max(options["limit"], 1), 1000),
                max_pages=None if options["all"] else min(max(options["max_pages"], 1), 100),
                pause=options["pause"], max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        system = ExternalSystem.objects.filter(code="alegra").first()
        if not system:
            raise CommandError("No existe el sistema externo Alegra local.")
        ct = ContentType.objects.get_for_model(Cliente)
        mappings = list(ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=ct,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).select_related("content_type"))
        by_external = {str(item.external_id): item for item in mappings}
        applied = 0
        conflicts = 0
        blocked = 0
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
            result=SyncAuditLog.RESULT_SUCCESS if not fetched["errors"] else SyncAuditLog.RESULT_PARTIAL,
            detail=f"Consultados: {len(fetched['rows'])}; aplicados: {applied}; conflictos: {conflicts}; bloqueados: {blocked}.",
            metadata={"pages": fetched["pages"], "coverage": fetched["coverage"], "errors": len(fetched["errors"])},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Contactos entrantes: consultados={len(fetched['rows'])}, aplicados={applied}, "
            f"conflictos={conflicts}, bloqueados={blocked}, paginas={fetched['pages']}, "
            f"duplicados={fetched['duplicate_external_ids']}, cobertura={fetched['coverage']}. Solo GET."
        ))
