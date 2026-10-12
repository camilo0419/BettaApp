"""Recupera de forma controlada una actualización marcada erróneamente como no-op.

El comando solo consulta Alegra mediante GET. Nunca ejecuta POST, PUT ni
DELETE; la operación que prepara queda pendiente para el procesador normal.
"""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from tienda.models import AlegraWriteOperation, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.alegra_client import AlegraError
from tienda.services.alegra_inbound_sync import InboundClientSyncService
from tienda.services.alegra_operation_queue import enqueue_update_if_changed
from tienda.services.alegra_write import AlegraWriteClient
from tienda.services.database_lock import advisory_lock


CONFIRMATION = "AUTORIZAR RECUPERACION NO_OP ALEGRA"
PROTECTED_FIELDS = {
    "tipo_cliente", "nombre", "tipo_identificacion", "identificacion",
    "digito_verificacion", "regimen_tributario",
}
ORDINARY_FIELDS = InboundClientSyncService.WRITABLE_FIELDS - PROTECTED_FIELDS


class Command(BaseCommand):
    help = "Diagnostica o recupera una actualización Alegra marcada incorrectamente como no-op."

    def add_arguments(self, parser):
        parser.add_argument("--operation-id", type=int, required=True)
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--timeout", type=float, default=15)

    @staticmethod
    def _remote_presence(remote):
        address = remote.get("address") if isinstance(remote.get("address"), dict) else {}
        return {
            "nombre": "name" in remote or isinstance(remote.get("nameObject"), dict),
            "tipo_cliente": "kindOfPerson" in remote,
            "tipo_identificacion": isinstance(remote.get("identificationObject"), dict),
            "identificacion": isinstance(remote.get("identificationObject"), dict),
            "digito_verificacion": isinstance(remote.get("identificationObject"), dict)
            and "dv" in remote["identificationObject"],
            "regimen_tributario": "regime" in remote,
            "email": "email" in remote,
            "telefono": "phonePrimary" in remote,
            "telefono_secundario": "phoneSecondary" in remote,
            "celular": "mobile" in remote,
            "direccion": "address" in remote and "address" in address,
            "ciudad": "address" in remote and "city" in address,
            "departamento": "address" in remote and "department" in address,
            "pais": "address" in remote and "country" in address,
            "codigo_postal": "address" in remote and "zipCode" in address,
        }

    @staticmethod
    def _remote_values(remote):
        values = BidirectionalClientSync._external_values(remote)
        identity = remote.get("identificationObject")
        if isinstance(identity, dict):
            values.update({
                "tipo_identificacion": str(identity.get("type") or "").strip().casefold(),
                "identificacion": str(identity.get("number") or "").strip(),
                "digito_verificacion": str(identity.get("dv") or "").strip(),
            })
        kind = str(remote.get("kindOfPerson") or "").strip().upper()
        if kind:
            values["tipo_cliente"] = "empresa" if kind == "LEGAL_ENTITY" else "persona" if kind == "PERSON_ENTITY" else kind
        regime = str(remote.get("regime") or "").strip().upper()
        if regime:
            values["regimen_tributario"] = regime
        return values

    @staticmethod
    def _normalized(field, value):
        value = str(value or "").strip()
        if field in {"ciudad", "departamento", "pais"}:
            from tienda.services.geografia import normalize_location
            return normalize_location(value)
        return value.casefold() if field in {"tipo_identificacion"} else value

    def _classify(self, operation, mapping, remote):
        metadata = operation.result_metadata if isinstance(operation.result_metadata, dict) else {}
        snapshot = metadata.get("local_snapshot")
        baseline = metadata.get("baseline")
        if not isinstance(snapshot, dict) or not isinstance(baseline, dict):
            return {"state": "BLOCKED", "reason": "snapshot_o_baseline_incompleto", "fields": []}

        presence = self._remote_presence(remote)
        remote_values = self._remote_values(remote)
        fields = sorted(set(snapshot) | set(baseline))
        missing = [
            field for field in fields
            if (str(snapshot.get(field) or "").strip() or str(baseline.get(field) or "").strip())
            and not presence.get(field, False)
        ]
        if missing:
            return {"state": "BLOCKED", "reason": "campos_remotos_omitidos", "fields": missing}

        protected_missing = [field for field in PROTECTED_FIELDS if not presence.get(field, False)]
        if protected_missing:
            return {"state": "BLOCKED", "reason": "campos_protegidos_omitidos", "fields": sorted(protected_missing)}

        protected_differences = InboundClientSyncService._protected_differences(operation.client, remote)
        remote_name = str(remote_values.get("nombre") or "").strip()
        local_name = str(operation.client.nombre or "").strip()
        if remote_name and local_name and remote_name != local_name:
            protected_differences.append("nombre")
        if protected_differences:
            return {"state": "BLOCKED", "reason": "diferencias_protegidas", "fields": sorted(set(protected_differences))}

        conflicts = []
        remote_only = []
        ordinary_changes = []
        for field in fields:
            local = self._normalized(field, snapshot.get(field, ""))
            previous = self._normalized(field, baseline.get(field, ""))
            current_remote = self._normalized(field, remote_values.get(field, ""))
            remote_changed = current_remote != previous
            local_changed = local != previous
            if remote_changed and local_changed and current_remote != local:
                conflicts.append(field)
            elif remote_changed and not local_changed:
                remote_only.append(field)
            elif local_changed and not remote_changed and field in ORDINARY_FIELDS:
                ordinary_changes.append(field)
        if conflicts or remote_only:
            return {
                "state": "BLOCKED",
                "reason": "conflicto_remoto" if conflicts else "cambio_remoto_no_local",
                "fields": sorted(set(conflicts + remote_only)),
            }
        return {"state": "SAFE", "reason": "cambios_ordinarios_confirmados", "fields": sorted(ordinary_changes), "presence": presence, "remote_values": remote_values}

    def handle(self, *args, **options):
        operation = AlegraWriteOperation.objects.select_related("client", "system").filter(pk=options["operation_id"]).first()
        if operation is None:
            raise CommandError("No existe la operación indicada.")
        metadata = operation.result_metadata if isinstance(operation.result_metadata, dict) else {}
        if operation.operation != AlegraWriteOperation.OP_UPDATE:
            raise CommandError("La recuperación solo aplica a operaciones update.")
        if operation.state != AlegraWriteOperation.STATE_SYNCED or not metadata.get("no_op") or not metadata.get("confirmed_by_get"):
            raise CommandError("La operación no cumple los criterios synced + no_op + confirmed_by_get.")
        if metadata.get("recovery_status") == "recovered_for_followup":
            self.stdout.write(f"operation={operation.pk} ya fue recuperada; no se crea otra operación.")
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
        if str(remote.get("id") or "") != str(mapping.external_id):
            self.stdout.write(f"operation={operation.pk} bloqueada=external_id_no_coincide")
            return

        result = self._classify(operation, mapping, remote)
        self.stdout.write(
            f"operation={operation.pk} estado={result['state'].lower()} "
            f"motivo={result['reason']} campos={len(result['fields'])} dry_run={not options['apply']}"
        )
        if result["state"] != "SAFE" or not options["apply"]:
            return
        if options["confirm"] != CONFIRMATION:
            raise CommandError(f"Para aplicar se requiere --confirm \"{CONFIRMATION}\".")

        with advisory_lock(f"bettaapp:alegra:queue:contact:{operation.client_id}"):
            with transaction.atomic():
                locked = AlegraWriteOperation.objects.select_for_update().get(pk=operation.pk)
                locked_metadata = dict(locked.result_metadata or {})
                if locked.state != AlegraWriteOperation.STATE_SYNCED or not locked_metadata.get("no_op"):
                    raise CommandError("La operación cambió durante la recuperación; vuelve a diagnosticar.")
                if locked_metadata.get("recovery_status") == "recovered_for_followup":
                    self.stdout.write(f"operation={locked.pk} ya fue recuperada; no se crea otra operación.")
                    return

                presence = result["presence"]
                remote_values = result["remote_values"]
                mapping_metadata = dict(mapping.metadata or {})
                previous = mapping_metadata.get("last_confirmed", {})
                if not isinstance(previous, dict):
                    previous = {}
                confirmed = {**previous, **{field: value for field, value in remote_values.items() if presence.get(field, False)}}
                mapping.metadata = {
                    **mapping_metadata,
                    "last_synced_fields": confirmed,
                    "last_confirmed": confirmed,
                    "baseline_source": "alegra_noop_recovery_get",
                }
                mapping.last_synced_at = timezone.now()
                mapping.save(update_fields=["metadata", "last_synced_at", "updated_at"])
                locked_metadata.update({
                    "recovery_status": "recovered_for_followup",
                    "recovery_method": "remote_get_reconciliation",
                    "invalidated_no_op": True,
                    "recovery_fields": result["fields"],
                })
                locked.result_metadata = locked_metadata
                locked.reconciliation_reason = "Clasificación no-op invalidada; baseline reconstruido mediante GET."
                locked.save(update_fields=["result_metadata", "reconciliation_reason", "updated_at"])
                SyncAuditLog.objects.create(
                    system=locked.system, operation="recover_contact_no_op", resource="contacts",
                    external_id=locked.external_id, result=SyncAuditLog.RESULT_SUCCESS,
                    detail="Se invalidó un no-op mediante GET; no se ejecutó PUT.",
                    metadata={"operation_id": locked.pk, "client_id": locked.client_id, "fields": result["fields"]},
                )

        follow_up = enqueue_update_if_changed(operation.client, defer_remote_check=True)
        self.stdout.write(f"operation={operation.pk} recuperada seguimiento={getattr(follow_up, 'pk', 'sin_cambios')}")
