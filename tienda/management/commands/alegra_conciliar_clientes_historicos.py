"""Diagnóstico y conciliación controlada de clientes históricos sin mapa activo."""

from __future__ import annotations

from collections import Counter

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from tienda.models import (
    AlegraContactStaging,
    AlegraWriteOperation,
    Cliente,
    ExternalObjectMap,
    ExternalSystem,
    SyncAuditLog,
)
from tienda.services.alegra_contact_import import AlegraContactImporter, normalize_identity
from tienda.services.alegra_write import (
    AlegraContactWriteService,
    AlegraError,
    AlegraWriteClient,
    COLOMBIA_ID_TYPES,
    WriteConflict,
    build_contact_payload,
)
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock


CONFIRMATION = "AUTORIZAR CONCILIACION HISTORICA"
LOCK_NAME = "bettaapp:alegra:historical-client-reconciliation"


class Command(BaseCommand):
    help = "Diagnostica y concilia clientes históricos sin vínculo Alegra; dry-run por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Procesa todos los históricos sin mapa activo.")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--after-client-id", type=int, default=0)
        parser.add_argument("--timeout", type=float, default=None)
        parser.add_argument("--apply", action="store_true", help="Aplica solo vínculos y operaciones locales aprobadas.")
        parser.add_argument("--confirm", default="")

    def handle(self, *args, **options):
        if options["apply"] and options["confirm"] != CONFIRMATION:
            raise CommandError(f"--apply requiere --confirm \"{CONFIRMATION}\".")
        if options["apply"]:
            try:
                with advisory_lock(LOCK_NAME):
                    return self._run(options)
            except DatabaseLockUnavailable as exc:
                raise CommandError(str(exc)) from exc
        return self._run(options)

    def _run(self, options):
        system = ExternalSystem.objects.filter(code="alegra", status=ExternalSystem.STATUS_ACTIVE).first()
        if not system:
            raise CommandError("No existe un sistema Alegra activo.")
        content_type = ContentType.objects.get_for_model(Cliente)
        mapped_ids = ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", content_type=content_type,
            status=ExternalObjectMap.STATUS_ACTIVE, object_id__isnull=False,
        ).values_list("object_id", flat=True)
        queryset = Cliente.objects.filter(
            alegra_sync_policy=Cliente.ALEGRA_SYNC_HISTORICAL_PENDING,
        ).exclude(pk__in=mapped_ids).filter(pk__gt=max(options["after_client_id"], 0)).order_by("pk")
        if not options["all"]:
            queryset = queryset[: max(int(options["limit"]), 1)]
        client = AlegraWriteClient(timeout=options["timeout"])
        importer = AlegraContactImporter()
        counts = Counter()
        rows = []
        for local in queryset:
            result = self._classify(local, system, content_type, client)
            counts[result["classification"]] += 1
            rows.append(result)
            if options["apply"] and result["classification"] in {"UNIQUE_MATCH", "SAFE_CREATE"}:
                try:
                    result["applied"] = self._apply_result(result, local, system, content_type, importer)
                except Exception as exc:  # keep one bad local record from aborting the batch
                    result["applied"] = "APPLY_ERROR"
                    counts["APPLY_ERROR"] += 1
                    SyncAuditLog.objects.create(
                        system=system,
                        operation="historical_reconciliation_apply",
                        resource="contacts",
                        result=SyncAuditLog.RESULT_ERROR,
                        detail="No fue posible aplicar el resultado local; requiere revisión.",
                        metadata={"client_id": local.pk, "error_type": exc.__class__.__name__},
                    )
                    continue
                counts[result["applied"]] += 1
        mode = "APLICACIÓN LOCAL AUTORIZADA" if options["apply"] else "DRY-RUN"
        self.stdout.write(
            f"{mode}: evaluados={len(rows)}; "
            + "; ".join(f"{key}={value}" for key, value in sorted(counts.items()))
            + ". Alegra solo fue consultada mediante GET."
        )
        for row in rows:
            self.stdout.write(
                f"ID local={row['client_id']}; clasificacion={row['classification']}; "
                f"candidatos={row['candidate_count']}; motivo={row['reason']}"
                + (f"; resultado={row['applied']}" if "applied" in row else "")
            )

    @staticmethod
    def _classify(local, system, content_type, client):
        expected_type = COLOMBIA_ID_TYPES.get(local.tipo_identificacion, "")
        if not local.identificacion or not local.tipo_identificacion:
            return {"client_id": local.pk, "classification": "INCOMPLETE", "candidate_count": 0, "reason": "identificacion_o_tipo_documental_faltante"}
        try:
            candidates = client.find_candidates(identification=normalize_identity(local.identificacion))
        except Exception as exc:
            return {"client_id": local.pk, "classification": "QUERY_ERROR", "candidate_count": 0, "reason": f"error_{exc.__class__.__name__}"}
        exact_number = []
        exact_document = []
        for row in candidates:
            identity = row.get("identificationObject") if isinstance(row.get("identificationObject"), dict) else {}
            remote_number = normalize_identity(identity.get("number") or row.get("identification"))
            if remote_number != normalize_identity(local.identificacion):
                continue
            exact_number.append(row)
            if expected_type and str(identity.get("type") or "").strip().upper() == expected_type:
                exact_document.append(row)
        if len(exact_document) == 1 and not ExternalObjectMap.objects.filter(
            system=system, resource_type="contacts", external_id=str(exact_document[0].get("id")), status=ExternalObjectMap.STATUS_ACTIVE,
        ).exists():
            return {"client_id": local.pk, "classification": "UNIQUE_MATCH", "candidate_count": len(candidates), "reason": "identificacion_y_documento_inequivocos", "remote_row": exact_document[0]}
        if len(exact_document) > 1:
            return {"client_id": local.pk, "classification": "AMBIGUOUS", "candidate_count": len(candidates), "reason": "multiples_candidatos_con_identificacion_y_documento"}
        if exact_number:
            return {"client_id": local.pk, "classification": "IDENTITY_CONFLICT", "candidate_count": len(candidates), "reason": "diferencia_tipo_documento"}
        try:
            build_contact_payload(local)
        except ValidationError:
            return {"client_id": local.pk, "classification": "INCOMPLETE", "candidate_count": len(candidates), "reason": "payload_local_incompleto"}
        return {"client_id": local.pk, "classification": "SAFE_CREATE", "candidate_count": len(candidates), "reason": "sin_coincidencia_remota_y_payload_valido"}

    @staticmethod
    def _apply_result(result, local, system, content_type, importer):
        if result["classification"] == "UNIQUE_MATCH":
            remote_row = result["remote_row"]
            external_id = str(remote_row.get("id") or "")
            if not external_id:
                return "BLOCKED_INVALID_EXTERNAL_ID"
            with transaction.atomic():
                importer._upsert(system, remote_row)
                staging = AlegraContactStaging.objects.get(system=system, external_id=external_id)
                importer.link_contact(staging.pk, local.pk)
                SyncAuditLog.objects.create(
                    system=system, operation="historical_reconciliation_link", resource="contacts",
                    external_id=external_id, result=SyncAuditLog.RESULT_SUCCESS,
                    detail="Coincidencia documental única vinculada; no se modificó Alegra.",
                    metadata={"client_id": local.pk, "classification": "UNIQUE_MATCH"},
                )
            return "LINKED"
        try:
            operation, _ = AlegraContactWriteService(transport=AlegraWriteClient()).prepare_create(local, system)
        except (AlegraError, ValidationError, WriteConflict) as exc:
            SyncAuditLog.objects.create(
                system=system, operation="historical_reconciliation_create", resource="contacts",
                result=SyncAuditLog.RESULT_ERROR, detail="Alta local bloqueada por validación o consulta.",
                metadata={"client_id": local.pk, "error_type": exc.__class__.__name__},
            )
            return "BLOCKED_CREATE"
        SyncAuditLog.objects.create(
            system=system, operation="historical_reconciliation_create", resource="contacts",
            result=SyncAuditLog.RESULT_PARTIAL, detail="Operación local preparada; escritura externa no ejecutada.",
            metadata={"client_id": local.pk, "operation_id": operation.pk, "classification": "SAFE_CREATE"},
        )
        return "CREATE_PREPARED"
