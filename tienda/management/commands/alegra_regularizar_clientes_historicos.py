"""Regularización local y controlada de campos tributarios históricos de Alegra."""

from __future__ import annotations

import copy
import os
from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_inbound_sync import InboundClientSyncService
from tienda.services.alegra_normalization import extract_identification_context
from tienda.services.alegra_preimport_clients import fetch_customer_contacts
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


CONFIRMATION = "REGULARIZAR CLIENTES HISTORICOS ALEGRA"
LOCK_NAME = "bettaapp:alegra:historical-tax-regularization"
HISTORICAL_SOURCES = {"alegra_initial_import"}
HISTORICAL_PHASES = {"initial", "7.3"}
TAX_FIELDS = ("tipo_identificacion", "regimen_tributario", "digito_verificacion")


def _is_historical(mapping):
    metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
    return metadata.get("source") in HISTORICAL_SOURCES or metadata.get("phase") in HISTORICAL_PHASES


def _local_kind(client):
    return "LEGAL_ENTITY" if client.tipo_cliente == Cliente.TIPO_EMPRESA else "PERSON_ENTITY"


def _tax_values(client, identity):
    return {
        "tipo_identificacion": str(client.tipo_identificacion or "").strip().casefold(),
        "regimen_tributario": str(client.regimen_tributario or "").strip().upper(),
        "digito_verificacion": str(client.digito_verificacion or "").strip(),
        "kindOfPerson": _local_kind(client),
    }, {
        "tipo_identificacion": identity.get("type") or "",
        "regimen_tributario": identity.get("regime") or "",
        "digito_verificacion": identity.get("dv") or "",
        "kindOfPerson": identity.get("kind") or "",
    }


def plan_regularization(client, mapping, remote, baseline_service=None):
    """Devuelve un plan sin modificar objetos persistidos ni ejecutar HTTP."""
    if not _is_historical(mapping):
        return {"state": "EXCLUDED", "reason": "Sin evidencia de importación histórica."}
    metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
    if metadata.get("last_confirmed") or metadata.get("last_synced_fields"):
        return {"state": "NO_ACTION", "reason": "El vínculo ya tiene baseline confirmado."}
    if not remote:
        return {"state": "REVIEW", "reason": "Contacto remoto no recuperado."}
    if not client:
        return {"state": "REVIEW", "reason": "Cliente local no recuperado."}
    if mapping.created_at and client.fecha_actualizacion and client.fecha_actualizacion > mapping.created_at:
        return {"state": "REVIEW", "reason": "El cliente local fue modificado después de la importación."}

    identity = extract_identification_context(remote)
    required = (identity.get("type"), identity.get("number"), identity.get("kind"), identity.get("regime"))
    if not all(required):
        return {"state": "REVIEW", "reason": "Faltan campos tributarios protegidos confirmables."}

    local, external = _tax_values(client, identity)
    if external["kindOfPerson"] not in {"LEGAL_ENTITY", "PERSON_ENTITY"}:
        return {"state": "REVIEW", "reason": "El tipo de persona remoto no está confirmado."}

    proposed = {}
    conflicts = []
    if local["kindOfPerson"] != external["kindOfPerson"]:
        proposed["tipo_cliente"] = (
            Cliente.TIPO_EMPRESA
            if external["kindOfPerson"] == "LEGAL_ENTITY"
            else Cliente.TIPO_PERSONA
        )
    for field in TAX_FIELDS:
        if not external[field]:
            continue
        if not local[field]:
            proposed[field] = external[field]
        elif local[field] != external[field]:
            conflicts.append(field)
    if conflicts:
        return {"state": "REVIEW", "reason": "Existe un valor tributario local no vacío diferente.", "conflicts": conflicts}

    candidate_client = copy.copy(client)
    for field, value in proposed.items():
        setattr(candidate_client, field, value)
    service = baseline_service or InboundClientSyncService(None)
    candidate = service.baseline_candidate(candidate_client, mapping, remote)
    if candidate.get("state") != "SAFE":
        return {
            "state": "REVIEW",
            "reason": "Persisten diferencias fuera de los campos tributarios regularizables.",
            "differences": candidate.get("differences", []),
        }
    return {
        "state": "SAFE",
        "reason": "Campos tributarios vacíos completables y baseline confirmable.",
        "changes": {
            field: {
                "before": local.get(field, client.tipo_cliente if field == "tipo_cliente" else ""),
                "after": value,
            }
            for field, value in proposed.items()
        },
        "candidate": candidate,
    }


def apply_regularization(plans, *, system, actor=None):
    """Aplica únicamente planes SAFE, uno por transacción y de forma idempotente."""
    result = Counter()
    errors = []
    service = InboundClientSyncService(None)
    for item in plans:
        mapping_id = item["mapping"].pk
        try:
            with transaction.atomic():
                mapping = ExternalObjectMap.objects.select_for_update().get(pk=mapping_id)
                client = Cliente.objects.select_for_update().get(pk=mapping.object_id)
                metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
                if metadata.get("last_confirmed") or metadata.get("last_synced_fields"):
                    result["NO_ACTION"] += 1
                    continue
                plan = plan_regularization(client, mapping, item["remote"], service)
                if plan.get("state") != "SAFE":
                    result["REVIEW"] += 1
                    continue
                changed = []
                for field, values in plan.get("changes", {}).items():
                    setattr(client, field, values["after"])
                    changed.append(field)
                if changed:
                    client.full_clean()
                    client.save(update_fields=sorted(set(changed + ["fecha_actualizacion"])))
                refreshed = service.baseline_candidate(client, mapping, item["remote"])
                if refreshed.get("state") != "SAFE":
                    raise ValueError("La comparación final dejó de ser SAFE.")
                service.set_baseline(mapping, refreshed)
                SyncAuditLog.objects.create(
                    system=system, operation="regularize_historical_alegra_client", resource="contacts",
                    external_id=str(mapping.external_id), actor=actor,
                    result=SyncAuditLog.RESULT_SUCCESS,
                    detail="Campos tributarios históricos regularizados y baseline confirmado.",
                    metadata={"fields": plan.get("changes", {}), "direction": "alegra_to_betta"},
                )
                result["UPDATED"] += 1
        except Exception as exc:
            result["FAILED"] += 1
            errors.append(str(exc)[:160])
    return {"counts": dict(result), "errors": errors}


class Command(BaseCommand):
    help = "Diagnostica o regulariza localmente campos tributarios de clientes históricos de Alegra."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")
        parser.add_argument("--all", action="store_true")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        if options["apply"]:
            if options["confirm"] != CONFIRMATION:
                raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
            if os.environ.get("ALEGRA_HISTORICAL_TAX_REGULARIZATION_ENABLED", "").casefold() != "true":
                raise CommandError("La regularización requiere autorización explícita del sistema.")
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
        if options["apply"] and (fetched["coverage"] != "complete" or fetched["errors"] or fetched["duplicate_external_ids"]):
            raise CommandError("Aplicación bloqueada: cobertura incompleta, errores o IDs duplicados.")
        system = ExternalSystem.objects.filter(code="alegra").first()
        if not system:
            raise CommandError("No existe el sistema externo Alegra.")
        ct = ContentType.objects.get_for_model(Cliente)
        mappings = list(ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=ct,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).select_related())
        local = Cliente.objects.in_bulk([mapping.object_id for mapping in mappings])
        rows = {str(row.get("id")): row for row in fetched["rows"] if row.get("id")}
        service = InboundClientSyncService(None)
        plans = []
        counts = Counter()
        reasons = Counter()
        proposed_fields = Counter()
        for mapping in mappings:
            plan = plan_regularization(local.get(mapping.object_id), mapping, rows.get(str(mapping.external_id)), service)
            counts[plan["state"]] += 1
            if plan.get("reason"):
                reasons[plan["reason"]] += 1
            if plan["state"] == "SAFE":
                proposed_fields.update(plan.get("changes", {}).keys())
                plans.append({"mapping": mapping, "remote": rows[str(mapping.external_id)], "plan": plan})
        applied = {"counts": {}, "errors": []}
        if options["apply"]:
            applied = apply_regularization(plans, system=system)
        self.stdout.write(
            f"Modo={'APPLY' if options['apply'] else 'DRY-RUN'}; SAFE={counts['SAFE']}; "
            f"REVIEW={counts['REVIEW']}; EXCLUDED={counts['EXCLUDED']}; NO_ACTION={counts['NO_ACTION']}; "
            f"aplicados={applied['counts'].get('UPDATED', 0)}; solo GET externo."
        )
        if proposed_fields:
            self.stdout.write("Campos propuestos=" + ", ".join(
                f"{field}:{count}" for field, count in sorted(proposed_fields.items())
            ))
        if reasons:
            self.stdout.write("Motivos agregados=" + " | ".join(
                f"{reason}:{count}" for reason, count in sorted(reasons.items())
            ))
