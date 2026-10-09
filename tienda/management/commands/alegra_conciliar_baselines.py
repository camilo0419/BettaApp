"""Reconcilia e inicializa baselines de contactos Alegra sin sobrescribir clientes."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient, extract_rows
from tienda.services.alegra_inbound_sync import InboundClientSyncService
from tienda.services.alegra_preimport_clients import fetch_customer_contacts
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


CONFIRMATION = "INICIALIZAR BASELINES ALEGRA"
LOCK_NAME = "bettaapp:alegra:baseline-reconciliation"


class Command(BaseCommand):
    help = "Diagnostica o inicializa baselines confirmados de contactos Alegra usando solo GET."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true", help="Solo concilia en memoria; es el comportamiento por defecto.")
        mode.add_argument("--apply", action="store_true", help="Persiste únicamente baselines clasificados como seguros.")
        parser.add_argument("--confirm", default="", help=f'Para --apply: {CONFIRMATION}')
        parser.add_argument("--all", action="store_true", help="Recorre todos los contactos hasta el final verificable.")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10)
        parser.add_argument("--pause", type=float, default=0)
        parser.add_argument("--max-retries", type=int, default=2)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--output", default="docs/auditorias/betta_conciliacion_baselines_alegra.md")

    def handle(self, *args, **options):
        apply_mode = bool(options.get("apply"))
        if apply_mode:
            if options.get("confirm") != CONFIRMATION:
                raise CommandError(f'--apply requiere --confirm "{CONFIRMATION}".')
            if os.environ.get("ALEGRA_INBOUND_BASELINE_APPLY_ENABLED", "").strip().casefold() != "true":
                raise CommandError("Aplicación bloqueada: requiere ALEGRA_INBOUND_BASELINE_APPLY_ENABLED=true.")
        try:
            with advisory_lock(LOCK_NAME):
                return self._run(options, apply_mode)
        except DatabaseLockUnavailable as exc:
            raise CommandError(str(exc)) from exc

    def _run(self, options, apply_mode):
        try:
            fetched = fetch_customer_contacts(
                AlegraReadOnlyClient(timeout=options["timeout"]),
                limit=None if options["all"] else min(max(options["limit"], 1), 1000),
                max_pages=None if options["all"] else min(max(options["max_pages"], 1), 100),
                pause=options["pause"], max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        if apply_mode and (
            fetched["coverage"] != "complete"
            or fetched["errors"]
            or fetched["stopped_reason"] != "end_of_pagination"
            or fetched["duplicate_external_ids"]
        ):
            raise CommandError("Aplicación bloqueada: cobertura incompleta, errores o IDs externos duplicados.")

        system = ExternalSystem.objects.filter(code="alegra").first()
        if not system:
            raise CommandError("No existe el sistema externo Alegra.")
        content_type = ContentType.objects.get_for_model(Cliente)
        mappings = list(ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=content_type,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).only("id", "external_id", "object_id", "metadata", "status", "system_id"))
        local_clients = Cliente.objects.in_bulk([item.object_id for item in mappings if item.object_id])
        rows = {str(row.get("id")): row for row in fetched["rows"] if row.get("id")}
        service = InboundClientSyncService(AlegraReadOnlyClient(timeout=options["timeout"]))
        candidates = []
        counts = {"ALREADY_BASELINE": 0, "SAFE": 0, "REVIEW": 0, "MISSING_REMOTE": 0}
        for mapping in mappings:
            metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
            if metadata.get("last_synced_fields") or metadata.get("last_confirmed"):
                counts["ALREADY_BASELINE"] += 1
                continue
            remote = rows.get(str(mapping.external_id))
            client = local_clients.get(mapping.object_id)
            if not remote or not client:
                counts["MISSING_REMOTE"] += 1
                candidates.append((mapping, client, remote, {"state": "REVIEW", "reason": "No se pudo confirmar el contacto vinculado.", "differences": ["remote_or_local"]}))
                continue
            candidate = service.baseline_candidate(client, mapping, remote)
            counts[candidate["state"]] += 1
            candidates.append((mapping, client, remote, candidate))

        diagnostics = self._aggregate_review_causes(candidates)

        applied = 0
        if apply_mode:
            for mapping, client, remote, candidate in candidates:
                if candidate.get("state") != "SAFE":
                    continue
                try:
                    response = service.transport.get(f"/contacts/{mapping.external_id}", {"mode": "advanced"})
                    latest_remote = response.data if isinstance(response.data, dict) and response.data.get("id") else None
                    if latest_remote is None:
                        rows = extract_rows(response.data)
                        latest_remote = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None
                    if latest_remote is None:
                        continue
                except AlegraError:
                    continue
                with transaction.atomic():
                    locked_mapping = ExternalObjectMap.objects.select_for_update().get(pk=mapping.pk)
                    locked_client = Cliente.objects.select_for_update().get(pk=client.pk)
                    metadata = locked_mapping.metadata if isinstance(locked_mapping.metadata, dict) else {}
                    if metadata.get("last_synced_fields") or metadata.get("last_confirmed"):
                        continue
                    current = service.baseline_candidate(locked_client, locked_mapping, latest_remote)
                    if current.get("state") != "SAFE":
                        continue
                    service.set_baseline(locked_mapping, current)
                    applied += 1
            SyncAuditLog.objects.create(
                system=system,
                operation="reconcile_alegra_client_baselines",
                resource="contacts",
                result=SyncAuditLog.RESULT_SUCCESS if counts["REVIEW"] == 0 and counts["MISSING_REMOTE"] == 0 else SyncAuditLog.RESULT_PARTIAL,
                detail=f"Baselines inicializados: {applied}; revisión: {counts['REVIEW'] + counts['MISSING_REMOTE']}.",
                metadata={"counts": counts, "applied": applied, "coverage": fetched["coverage"], "direction": "alegra_to_betta"},
            )
        report = self._report(fetched, counts, diagnostics, applied, apply_mode)
        output = Path(options["output"])
        if not output.is_absolute():
            output = Path.cwd() / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        mode = "APLICACIÓN" if apply_mode else "DRY-RUN"
        self.stdout.write(self.style.SUCCESS(f"{mode}: baselines seguros={counts['SAFE']}; revisión={counts['REVIEW'] + counts['MISSING_REMOTE']}; informe={output}"))

    @staticmethod
    @staticmethod
    def _aggregate_review_causes(candidates):
        """Agrega motivos sin conservar identificadores ni valores de contactos."""
        reasons = Counter()
        missing_fields = Counter()
        different_fields = Counter()
        categories = Counter()
        affected_clients = set()
        affected_by_category = {}
        for mapping, _client, _remote, candidate in candidates:
            if candidate.get("state") != "REVIEW":
                continue
            key = str(mapping.pk)
            affected_clients.add(key)
            missing = sorted({str(field) for field in candidate.get("missing", []) if field})
            unknown = sorted({str(field) for field in candidate.get("unknown", []) if field})
            differences = sorted({str(field) for field in candidate.get("differences", []) if field})
            reason = str(candidate.get("reason") or "Motivo no especificado")
            reasons[reason] += 1
            for field in missing:
                missing_fields[field] += 1
            for field in differences:
                different_fields[field] += 1
            if missing:
                category = "MISSING_PROTECTED_FIELDS"
            elif unknown:
                category = "UNKNOWN_PROTECTED_FIELDS"
            elif "remote_or_local" in differences:
                category = "MISSING_LINKED_RECORD"
            elif differences:
                category = "FIELD_DIFFERENCES"
            else:
                category = "OTHER_REVIEW"
            categories[category] += 1
            affected_by_category.setdefault(category, set()).add(key)
        return {
            "reason_counts": dict(sorted(reasons.items())),
            "missing_field_counts": dict(sorted(missing_fields.items())),
            "different_field_counts": dict(sorted(different_fields.items())),
            "exclusive_category_counts": dict(sorted(categories.items())),
            "affected_clients": len(affected_clients),
            "affected_by_category": {
                category: len(ids) for category, ids in sorted(affected_by_category.items())
            },
        }

    @staticmethod
    def _report(fetched, counts, diagnostics, applied, apply_mode):
        return "\n".join([
            "# Conciliación de baselines Alegra → BettaApp",
            "",
            f"- Modo: `{'APPLY' if apply_mode else 'DRY-RUN'}`.",
            f"- Contactos consultados: {len(fetched['rows'])}; páginas: {fetched['pages']}; cobertura: `{fetched['coverage']}`.",
            f"- Baselines existentes: {counts['ALREADY_BASELINE']}.",
            f"- Baselines confirmables: {counts['SAFE']}.",
            f"- Revisión manual: {counts['REVIEW']}.",
            f"- Contactos vinculados no recuperados: {counts['MISSING_REMOTE']}.",
            f"- Baselines inicializados: {applied}.",
            f"- Errores de consulta: {len(fetched['errors'])}; IDs duplicados observados: {fetched['duplicate_external_ids']}.",
            "",
            "## Diagnóstico agregado de REVIEW",
            "",
            f"- Clientes afectados por revisión: {diagnostics['affected_clients']}.",
            f"- Categorías excluyentes: {diagnostics['exclusive_category_counts']}.",
            f"- Clientes por categoría: {diagnostics['affected_by_category']}.",
            f"- Campos faltantes: {diagnostics['missing_field_counts']}.",
            f"- Campos diferentes: {diagnostics['different_field_counts']}.",
            f"- Motivos: {diagnostics['reason_counts']}.",
            "",
            "No se modifican clientes ni campos tributarios. Los casos con diferencias, datos protegidos incompletos o contacto remoto ausente quedan bloqueados.",
            "Las solicitudes externas del comando son exclusivamente GET.",
            "",
        ])
