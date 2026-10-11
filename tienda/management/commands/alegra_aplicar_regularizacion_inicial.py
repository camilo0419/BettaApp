"""Plan y aplicación controlada de la regularización inicial desde Alegra."""

from __future__ import annotations

import os
import uuid
from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tienda.management.commands.alegra_diagnosticar_regularizacion_tributaria import (
    HISTORICAL_SOURCE,
    INITIAL_IMPORT_FIELDS,
    _duplicate_count,
    _duplicate_ids_from_rows,
    initial_precedence_plan,
)
from tienda.models import Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_preimport_clients import fetch_customer_contacts
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


CONFIRMATION = "APLICAR REGULARIZACION INICIAL ALEGRA"
LOCK_NAME = "bettaapp:alegra:initial-client-regularization"
OPERATION = "regularizacion_inicial_alegra"


def _is_historical(mapping):
    metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
    return metadata.get("source") == HISTORICAL_SOURCE


def _provenance_unverified(mapping, client):
    return bool(
        not _is_historical(mapping)
        or (
            client
            and mapping.created_at
            and client.fecha_actualizacion
            and client.fecha_actualizacion > mapping.created_at
        )
    )


def _mapping_is_unique(system, content_type, mapping):
    external_count = ExternalObjectMap.objects.filter(
        system=system, resource_type="contacts", content_type=content_type,
        external_id=mapping.external_id, status=ExternalObjectMap.STATUS_ACTIVE,
    ).count()
    local_count = ExternalObjectMap.objects.filter(
        system=system, resource_type="contacts", content_type=content_type,
        object_id=mapping.object_id, status=ExternalObjectMap.STATUS_ACTIVE,
    ).count()
    return external_count == 1 and local_count == 1


def _audit_error(system, mapping, batch_id, error):
    SyncAuditLog.objects.create(
        system=system,
        operation=OPERATION,
        resource="contacts",
        external_id=str(mapping.external_id),
        result=SyncAuditLog.RESULT_ERROR,
        detail="No se pudo regularizar el cliente; la transacción fue revertida.",
        metadata={
            "batch_id": batch_id,
            "local_id": mapping.object_id,
            "error_type": type(error).__name__,
        },
    )


class Command(BaseCommand):
    help = "Planifica o aplica la regularización inicial de clientes desde Alegra."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Solo calcula el plan; es el modo predeterminado.")
        mode.add_argument("--apply", action="store_true", help="Aplica cambios locales con autorización explícita.")
        parser.add_argument("--confirm", default="", help=f'Requiere exactamente: {CONFIRMATION}')
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--after-map-id", type=int, default=0)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--batch-id", default="", help="Identificador opcional; no se puede reutilizar.")

    def handle(self, *args, **options):
        if options["apply"]:
            if options["confirm"] != CONFIRMATION:
                raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
            if os.environ.get("ALEGRA_HISTORICAL_TAX_REGULARIZATION_ENABLED", "").casefold() != "true":
                raise CommandError("La aplicación requiere autorización explícita del sistema.")
        try:
            with advisory_lock(LOCK_NAME):
                return self._run(options)
        except DatabaseLockUnavailable as exc:
            raise CommandError(str(exc)) from exc

    def _run(self, options):
        try:
            fetched = fetch_customer_contacts(
                AlegraReadOnlyClient(timeout=options["timeout"]),
                limit=None if options["all"] else min(max(options["limit"], 1), 1000),
                max_pages=None if options["all"] else min(max(options["max_pages"], 1), 100),
                pause=options["pause"], max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc

        declared_duplicates = _duplicate_count(fetched.get("duplicate_external_ids", 0))
        duplicate_ids = _duplicate_ids_from_rows(fetched["rows"])
        duplicate_ids_unknown = declared_duplicates > 0 and not duplicate_ids
        coverage_complete = fetched["coverage"] == "complete" and not fetched["errors"]
        if options["apply"] and (not coverage_complete or declared_duplicates or duplicate_ids_unknown):
            raise CommandError("Aplicación bloqueada: cobertura incompleta, errores GET o IDs externos duplicados.")

        system = ExternalSystem.objects.filter(code="alegra").first()
        if not system:
            raise CommandError("No existe el sistema externo Alegra.")
        content_type = ContentType.objects.get_for_model(Cliente)
        mappings_query = ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=content_type,
            status=ExternalObjectMap.STATUS_ACTIVE, pk__gt=max(options["after_map_id"], 0),
        ).order_by("pk")
        if not options["all"]:
            mappings_query = mappings_query[:max(options["limit"], 1)]
        mappings = list(mappings_query)
        clients = Cliente.objects.in_bulk([mapping.object_id for mapping in mappings if mapping.object_id])
        rows = {str(row.get("id")): row for row in fetched["rows"] if row.get("id")}
        plans = []
        counts = Counter()
        field_changes = Counter()
        for mapping in mappings:
            client = clients.get(mapping.object_id)
            remote = rows.get(str(mapping.external_id))
            duplicate = (
                not _mapping_is_unique(system, content_type, mapping)
                or str(mapping.external_id) in duplicate_ids
                or duplicate_ids_unknown
            )
            plan = initial_precedence_plan(
                mapping, client, remote, duplicate=duplicate,
                coverage_complete=coverage_complete,
                duplicate_ids_unknown=duplicate_ids_unknown,
                provenance_unverified=_provenance_unverified(mapping, client),
            )
            plans.append((mapping, client, remote, plan))
            counts["BLOQUEADO" if plan["client_blocked"] else "REVISION" if plan["requires_manual_review"] else "EVALUADO"] += 1
            field_changes.update(plan["fields_to_update"])

        batch_id = options["batch_id"] or uuid.uuid4().hex
        if options["apply"] and self._batch_used(batch_id):
            raise CommandError("El batch-id ya fue utilizado; no se permite reutilizarlo.")

        applied = Counter()
        errors = 0
        if options["apply"]:
            for mapping, client, remote, plan in plans:
                if plan["client_blocked"] or not plan["fields_to_update"]:
                    continue
                try:
                    changed = self._apply_one(
                        system, content_type, mapping, remote, batch_id,
                    )
                    if changed:
                        applied.update(changed)
                except Exception as exc:
                    errors += 1
                    _audit_error(system, mapping, batch_id, exc)

        self.stdout.write(
            f"Modo={'APPLY' if options['apply'] else 'DRY-RUN'}; batch_id={batch_id}; "
            f"mapas={len(mappings)}; paginas={fetched['pages']}; cobertura={fetched['coverage']}; "
            f"errores_get={len(fetched['errors'])}; ids_duplicados={declared_duplicates}; "
            f"evaluados={counts['EVALUADO']}; revision={counts['REVISION']}; bloqueados={counts['BLOQUEADO']}; "
            f"aplicados={sum(applied.values())}; errores_aplicacion={errors}; "
            "escrituras_alegra=0."
        )
        self.stdout.write("Cambios por campo: " + ", ".join(
            f"{field}={count}" for field, count in sorted((applied if options["apply"] else field_changes).items())
        ) or "ninguno")

    def _batch_used(self, batch_id):
        for audit in SyncAuditLog.objects.filter(operation=OPERATION, resource="contacts").only("metadata"):
            metadata = audit.metadata if isinstance(audit.metadata, dict) else {}
            if metadata.get("batch_id") == batch_id:
                return True
        return False

    def _apply_one(self, system, content_type, mapping, remote, batch_id):
        with transaction.atomic():
            locked_mapping = ExternalObjectMap.objects.select_for_update().get(pk=mapping.pk)
            locked_client = Cliente.objects.select_for_update().get(pk=mapping.object_id)
            if locked_mapping.status != ExternalObjectMap.STATUS_ACTIVE:
                return []
            if not _mapping_is_unique(system, content_type, locked_mapping):
                return []
            current = initial_precedence_plan(
                locked_mapping, locked_client, remote,
                coverage_complete=True,
                provenance_unverified=_provenance_unverified(locked_mapping, locked_client),
            )
            if current["client_blocked"] or not current["fields_to_update"]:
                return []
            changes = {}
            for field in current["fields_to_update"]:
                detail = current["fields"][field]
                changes[field] = {"before": detail["local_value"], "after": detail["remote_value"]}
                setattr(locked_client, field, detail["remote_value"])
            locked_client.full_clean()
            locked_client.save(update_fields=[*changes.keys(), "fecha_actualizacion"])
            SyncAuditLog.objects.create(
                system=system,
                operation=OPERATION,
                resource="contacts",
                external_id=str(locked_mapping.external_id),
                result=SyncAuditLog.RESULT_SUCCESS,
                detail="Regularización inicial aplicada desde Alegra.",
                metadata={
                    "batch_id": batch_id,
                    "local_id": locked_client.pk,
                    "fields": changes,
                    "direction": "alegra_to_betta",
                    "baseline_changed": False,
                },
            )
            return list(changes.keys())
