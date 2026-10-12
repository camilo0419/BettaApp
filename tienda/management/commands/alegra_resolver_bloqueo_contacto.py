"""Resuelve de forma controlada un bloqueo histórico de actualización.

Solo consulta Alegra mediante GET. La aplicación únicamente rebasea el
baseline local cuando identidad y tributación coinciden; nunca ejecuta PUT.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from tienda.models import AlegraWriteOperation, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.alegra_client import AlegraError
from tienda.services.alegra_inbound_sync import InboundClientSyncService
from tienda.services.alegra_normalization import extract_identification_context
from tienda.services.alegra_operation_queue import enqueue_update_if_changed
from tienda.services.alegra_write import AlegraWriteClient
from tienda.services.database_lock import advisory_lock


CONFIRMATION = "AUTORIZAR REBASE ALEGRA"


class Command(BaseCommand):
    help = "Diagnostica o resuelve un bloqueo histórico de actualización de contacto sin ejecutar PUT."

    def add_arguments(self, parser):
        parser.add_argument("--operation-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true", help="Persiste el rebase local y prepara el seguimiento.")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--timeout", type=float, default=15)

    @staticmethod
    def _baseline_from_remote(client, remote):
        identity = extract_identification_context(remote)
        remote_values = BidirectionalClientSync._external_values(remote)
        kind = identity.get("kind") or ""
        baseline = {
            **remote_values,
            "tipo_cliente": (
                "empresa" if kind == "LEGAL_ENTITY"
                else "persona" if kind == "PERSON_ENTITY" else client.tipo_cliente
            ),
            "tipo_identificacion": identity.get("type") or "",
            "identificacion": identity.get("number") or "",
            "digito_verificacion": identity.get("dv") or "",
            "regimen_tributario": identity.get("regime") or "",
        }
        return baseline, identity

    def handle(self, *args, **options):
        operation_id = options["operation_id"]
        operation = AlegraWriteOperation.objects.select_related("client", "system").filter(pk=operation_id).first()
        if operation is None:
            raise CommandError("No existe la operación indicada.")
        if operation.operation != AlegraWriteOperation.OP_UPDATE:
            raise CommandError("La resolución solo aplica a operaciones update de contactos.")
        if operation.state != AlegraWriteOperation.STATE_NEEDS_RECONCILIATION:
            self.stdout.write(f"operation={operation.pk} estado={operation.state}; no requiere resolución.")
            return
        if (operation.result_metadata or {}).get("resolution_status") == "resolved_for_followup":
            self.stdout.write(f"operation={operation.pk} ya fue resuelta para seguimiento.")
            return

        content_type = ContentType.objects.get_for_model(operation.client)
        mappings = list(ExternalObjectMap.objects.filter(
            system=operation.system, resource_type="contacts", content_type=content_type,
            object_id=operation.client_id, status=ExternalObjectMap.STATUS_ACTIVE,
        ).order_by("id"))
        if len(mappings) != 1:
            self.stdout.write(f"operation={operation.pk} bloqueada=mapping_ambiguo cantidad={len(mappings)}")
            return
        mapping = mappings[0]

        try:
            remote = AlegraWriteClient(timeout=options["timeout"]).get_contact(mapping.external_id)
        except (AlegraError, TimeoutError, OSError) as exc:
            self.stdout.write(f"operation={operation.pk} bloqueada=error_get tipo={exc.__class__.__name__}")
            return

        baseline, identity = self._baseline_from_remote(operation.client, remote)
        required = ("type", "number", "kind", "regime")
        missing = [key for key in required if not identity.get(key)]
        protected_differences = InboundClientSyncService._protected_differences(operation.client, remote)
        local_name = str(operation.client.nombre or "").strip()
        remote_name = str(remote.get("name") or "").strip()
        if not remote_name:
            missing.append("name")
        if remote_name and local_name and remote_name != local_name:
            protected_differences.append("nombre")
        if missing or protected_differences:
            reason = "campos_protegidos" if protected_differences else "campos_remotos_incompletos"
            self.stdout.write(
                f"operation={operation.pk} bloqueada={reason} "
                f"cantidad={len(set(protected_differences)) + len(missing)}"
            )
            return

        comparison = BidirectionalClientSync().compare_linked_client(
            operation.client, remote, (operation.result_metadata or {}).get("baseline") or
            (mapping.metadata or {}).get("last_confirmed") or
            (mapping.metadata or {}).get("last_synced_fields") or {},
        )
        if comparison["conflict_fields"]:
            self.stdout.write(
                f"operation={operation.pk} bloqueada=conflicto_remoto "
                f"cantidad={len(comparison['conflict_fields'])}"
            )
            return
        ordinary_changes = [
            field for field in comparison["local_fields"]
            if field not in {"tipo_cliente", "nombre", "tipo_identificacion", "identificacion", "digito_verificacion", "regimen_tributario"}
        ]
        self.stdout.write(
            f"operation={operation.pk} estado=apta_para_rebase "
            f"cambios_ordinarios={len(ordinary_changes)} dry_run={not options['apply']}"
        )
        if not options["apply"]:
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f"Para aplicar se requiere --confirm \"{CONFIRMATION}\".")

        lock_name = f"bettaapp:alegra:queue:contact:{operation.client_id}"
        with advisory_lock(lock_name):
            with transaction.atomic():
                locked = AlegraWriteOperation.objects.select_for_update().get(pk=operation.pk)
                if locked.state != AlegraWriteOperation.STATE_NEEDS_RECONCILIATION:
                    raise CommandError("La operación cambió durante la resolución; vuelve a diagnosticar.")
                metadata = dict(locked.result_metadata or {})
                metadata.update({
                    "resolution_status": "resolved_for_followup",
                    "resolution_method": "remote_get_baseline_rebase",
                    "resolved_external_id": str(mapping.external_id),
                })
                locked.result_metadata = metadata
                locked.error_code = "resolved_rebase"
                locked.reconciliation_reason = "Bloqueo histórico resuelto mediante GET y rebase seguro; se conserva para auditoría."
                locked.save(update_fields=["result_metadata", "error_code", "reconciliation_reason", "updated_at"])

                mapping_metadata = dict(mapping.metadata or {})
                previous_confirmed = mapping_metadata.get("last_confirmed", {})
                if not isinstance(previous_confirmed, dict):
                    previous_confirmed = {}
                mapping.metadata = {
                    **mapping_metadata,
                    "last_synced_fields": baseline,
                    "last_confirmed": {**previous_confirmed, **baseline},
                    "baseline_source": "alegra_manual_reconciliation",
                }
                mapping.last_synced_at = timezone.now()
                mapping.save(update_fields=["metadata", "last_synced_at", "updated_at"])
                SyncAuditLog.objects.create(
                    system=locked.system, operation="resolve_contact_reconciliation", resource="contacts",
                    external_id=locked.external_id, result=SyncAuditLog.RESULT_SUCCESS,
                    detail="Bloqueo histórico resuelto mediante GET; no se ejecutó PUT.",
                    metadata={"operation_id": locked.pk, "client_id": locked.client_id, "method": "remote_get_baseline_rebase"},
                )

        follow_up = enqueue_update_if_changed(operation.client, defer_remote_check=True)
        self.stdout.write(f"operation={operation.pk} resuelta seguimiento={getattr(follow_up, 'pk', 'sin_cambios')}")
