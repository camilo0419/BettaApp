"""Sincronización segura de cambios de contactos Alegra hacia BettaApp."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from .alegra_bidirectional_clients import BidirectionalClientSync, CONFLICT, PENDING, SYNCED
from .alegra_normalization import extract_identification_context

LOCAL_ONLY = "local_only"


class InboundSyncConflict(Exception):
    """La revisión ya no representa el estado actual."""


class InboundClientSyncService:
    WRITABLE_FIELDS = {
        "nombre", "email", "telefono", "telefono_secundario", "celular",
        "direccion", "ciudad", "departamento", "pais", "codigo_postal",
    }
    PROTECTED_FIELDS = {
        "tipo_cliente", "tipo_identificacion", "identificacion",
        "digito_verificacion", "regimen_tributario",
    }

    def __init__(self, transport, *, sync=None):
        self.transport = transport
        self.sync = sync or BidirectionalClientSync()

    def baseline_candidate(self, client: Cliente, mapping: ExternalObjectMap, remote_row: Mapping[str, Any]) -> dict[str, Any]:
        """Evalúa si el estado remoto confirma inequívocamente un baseline.

        No modifica el cliente ni el mapeo. Un campo remoto ausente no confirma
        que un valor local sea correcto, por lo que queda para revisión manual.
        """
        if mapping.status != ExternalObjectMap.STATUS_ACTIVE or mapping.object_id != client.pk:
            return {"state": "REVIEW", "reason": "El mapeo no está activo para el cliente.", "differences": ["mapping"]}
        if str(remote_row.get("id") or "").strip() != str(mapping.external_id):
            return {"state": "REVIEW", "reason": "El GET no corresponde al ID externo vinculado.", "differences": ["external_id"]}

        identity = extract_identification_context(remote_row)
        required_remote = {
            "tipo_identificacion": identity["type"] or "",
            "identificacion": identity["number"],
            "digito_verificacion": identity["dv"],
            "kindOfPerson": identity["kind"] or "",
            "regime": identity["regime"] or "",
        }
        unknown = []
        for raw_key, canonical_key in (("raw_type", "tipo_identificacion"), ("raw_kind", "kindOfPerson"), ("raw_regime", "regime")):
            if identity[raw_key] and not required_remote[canonical_key]:
                unknown.append(canonical_key)
        missing = [field for field, value in required_remote.items() if not value and field not in {"digito_verificacion"}]
        if unknown:
            return {"state": "REVIEW", "reason": "Existen códigos tributarios desconocidos.", "differences": unknown, "unknown": unknown}
        if missing:
            return {"state": "REVIEW", "reason": "Faltan campos protegidos confirmables.", "differences": missing, "missing": missing}
        local_kind = "LEGAL_ENTITY" if client.tipo_cliente == Cliente.TIPO_EMPRESA else "PERSON_ENTITY"
        protected = {
            "tipo_identificacion": str(client.tipo_identificacion or "").strip().casefold(),
            "identificacion": str(client.identificacion or "").strip(),
            "digito_verificacion": str(client.digito_verificacion or "").strip(),
            "kindOfPerson": local_kind,
            "regime": str(client.regimen_tributario or "").strip().upper(),
        }
        differences = []
        for field in ("tipo_identificacion", "identificacion", "digito_verificacion", "kindOfPerson", "regime"):
            remote_value = required_remote[field]
            local_value = protected[field]
            if field == "identificacion":
                remote_value = remote_value.replace(".", "")
                local_value = local_value.replace(".", "")
            if field == "digito_verificacion" and not remote_value and not local_value:
                continue
            if remote_value != local_value:
                differences.append(field)

        local_values = self.sync._local_values(client)
        remote_values = self.sync._external_values(remote_row)
        for field, remote_value in remote_values.items():
            local_value = local_values.get(field, "")
            if not remote_value and not local_value:
                continue
            if remote_value != local_value:
                differences.append(field)
        if differences:
            return {
                "state": "REVIEW",
                "reason": "Existen diferencias entre Alegra y BettaApp; no se fuerza el baseline.",
                "differences": sorted(set(differences)),
            }
        return {
            "state": "SAFE",
            "reason": "Campos compartidos y protegidos confirmados por GET.",
            "baseline": remote_values,
            "protected": required_remote,
        }

    @staticmethod
    def set_baseline(mapping: ExternalObjectMap, candidate: Mapping[str, Any], *, now=None):
        """Persiste únicamente un baseline previamente clasificado como SAFE."""
        if candidate.get("state") != "SAFE" or not isinstance(candidate.get("baseline"), Mapping):
            raise ValidationError("Solo puede persistirse un baseline confirmado.")
        metadata = dict(mapping.metadata or {})
        baseline = dict(candidate["baseline"])
        metadata["last_synced_fields"] = baseline
        metadata["last_confirmed"] = baseline
        metadata["baseline_protected"] = dict(candidate.get("protected") or {})
        metadata["baseline_source"] = "alegra_get_reconciliation"
        mapping.metadata = metadata
        mapping.last_synced_at = now or timezone.now()
        mapping.save(update_fields=["metadata", "last_synced_at", "updated_at"])

    @staticmethod
    def _stable_hash(value: Any) -> str:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _baseline(mapping: ExternalObjectMap) -> dict[str, Any]:
        metadata = mapping.metadata or {}
        baseline = metadata.get("last_synced_fields") or metadata.get("last_confirmed")
        return dict(baseline) if isinstance(baseline, Mapping) else {}

    @staticmethod
    def _local_version(client: Cliente) -> str:
        return client.fecha_actualizacion.isoformat() if client.fecha_actualizacion else ""

    @staticmethod
    def _protected_differences(client: Cliente, row: Mapping[str, Any]) -> list[str]:
        identity = extract_identification_context(row)
        remote_type = identity["type"] or ("__UNKNOWN__" if identity["raw_type"] else "")
        local_type = str(client.tipo_identificacion or "").strip().casefold()
        remote_number = identity["number"]
        remote_dv = identity["dv"]
        remote_kind = identity["kind"] or ("__UNKNOWN__" if identity["raw_kind"] else "")
        local_kind = "LEGAL_ENTITY" if client.tipo_cliente == Cliente.TIPO_EMPRESA else "PERSON_ENTITY"
        differences = []
        if remote_type and local_type and remote_type != local_type:
            differences.append("tipo_identificacion")
        if remote_number and client.identificacion and remote_number.replace(".", "") != str(client.identificacion).replace(".", ""):
            differences.append("identificacion")
        if remote_dv and remote_dv != str(client.digito_verificacion or "").strip():
            differences.append("digito_verificacion")
        if remote_kind and remote_kind != local_kind:
            differences.append("tipo_cliente")
        remote_regime = identity["regime"] or ("__UNKNOWN__" if identity["raw_regime"] else "")
        local_regime = str(client.regimen_tributario or "").strip().upper()
        if remote_regime and local_regime and remote_regime != local_regime:
            differences.append("regimen_tributario")
        return differences

    def review(self, client: Cliente, mapping: ExternalObjectMap, remote_row: Mapping[str, Any]) -> dict[str, Any]:
        if mapping.status != ExternalObjectMap.STATUS_ACTIVE or mapping.object_id != client.pk:
            raise InboundSyncConflict("El mapeo externo no está activo para este cliente.")
        external_id = str(remote_row.get("id") or "").strip()
        if external_id != str(mapping.external_id):
            raise InboundSyncConflict("El GET no corresponde al ID externo vinculado.")
        baseline = self._baseline(mapping)
        if not baseline:
            raise InboundSyncConflict("No existe baseline confirmado para revisar cambios.")
        comparison = self.sync.compare_linked_client(client, remote_row, baseline)
        protected = self._protected_differences(client, remote_row)
        identity = extract_identification_context(remote_row)
        if not identity["type"] or not identity["number"] or identity["raw_type"] and not identity["type"]:
            protected.append("identificacion")
        if not identity["kind"]:
            protected.append("tipo_cliente")
        if not identity["regime"]:
            protected.append("regimen_tributario")
        protected = sorted(set(protected))
        remote_fields = [field for field in comparison.get("remote_fields", []) if field in self.WRITABLE_FIELDS]
        conflicts = sorted(set(comparison.get("conflict_fields", [])) | set(protected))
        state = CONFLICT if conflicts else (PENDING if remote_fields else (LOCAL_ONLY if comparison.get("local_fields") else SYNCED))
        external_values = self.sync._external_values(remote_row)
        local_values = self.sync._local_values(client)
        changes = []
        for field in sorted(set(comparison.get("changed_fields", [])) | set(protected)):
            changes.append({
                "field": field,
                "previous": baseline.get(field, ""),
                "external": external_values.get(field, ""),
                "local": local_values.get(field, ""),
                "protected": field in self.PROTECTED_FIELDS,
            })
        return {
            "mapping_id": mapping.pk,
            "external_id": str(mapping.external_id),
            "local_id": client.pk,
            "state": state,
            "reason": (
                "Cambios tributarios requieren conciliación manual." if protected
                else "Hay cambios locales pendientes; no se sobrescriben." if comparison.get("local_fields") and not remote_fields
                else comparison.get("reason", "")
            ),
            "remote_fields": remote_fields,
            "local_fields": comparison.get("local_fields", []),
            "conflict_fields": conflicts,
            "protected_fields": protected,
            "changes": changes,
            "local_version": self._local_version(client),
            # La marca auto_now no es suficiente como detector de concurrencia:
            # SQLite puede conservar la misma precisión temporal en dos guardados
            # consecutivos. El hash captura el estado revisado de los campos
            # sincronizables sin añadir una migración ni cambiar el modelo.
            "local_hash": self._stable_hash(local_values),
            "baseline_hash": self._stable_hash(baseline),
            "remote_hash": self._stable_hash(remote_row),
        }

    def apply(self, client_id: int, mapping_id: int, review: Mapping[str, Any], approved_fields: Sequence[str], *, actor=None) -> dict[str, Any]:
        mapping = ExternalObjectMap.objects.select_related("system").get(pk=mapping_id)
        client = Cliente.objects.get(pk=client_id)
        remote = self.transport.get_contact(mapping.external_id)
        current_review = self.review(client, mapping, remote)
        if current_review["state"] == SYNCED:
            return {"status": "noop", "updated_fields": []}
        if current_review["state"] == CONFLICT:
            raise InboundSyncConflict(current_review["reason"])
        for key in ("external_id", "local_id", "local_version", "local_hash", "baseline_hash", "remote_hash"):
            if str(current_review.get(key)) != str(review.get(key)):
                raise InboundSyncConflict("La revisión quedó obsoleta; vuelva a consultar Alegra.")
        approved = set(approved_fields)
        if not approved or not approved.issubset(set(current_review["remote_fields"])):
            raise ValidationError("Solo se pueden aplicar campos externos revisados y no protegidos.")
        with transaction.atomic():
            mapping = ExternalObjectMap.objects.select_for_update().get(pk=mapping_id)
            client = Cliente.objects.select_for_update().get(pk=client_id)
            if self._local_version(client) != review.get("local_version"):
                raise InboundSyncConflict("El cliente local cambió después de la revisión.")
            values = self.sync._external_values(remote)
            update_fields = []
            if "nombre" in approved and client.tipo_cliente == Cliente.TIPO_PERSONA:
                name_object = remote.get("nameObject") if isinstance(remote.get("nameObject"), Mapping) else {}
                for source, target in (("firstName", "primer_nombre"), ("secondName", "segundo_nombre"), ("lastName", "primer_apellido"), ("secondLastName", "segundo_apellido")):
                    if source in name_object:
                        setattr(client, target, str(name_object.get(source) or "").strip())
                        update_fields.append(target)
            for field in approved:
                if field == "nombre":
                    client.nombre = values.get("nombre", "")
                else:
                    setattr(client, field, values.get(field, ""))
                update_fields.append(field)
            client.full_clean()
            client.save(update_fields=sorted(set(update_fields + ["fecha_actualizacion"])))
            metadata = dict(mapping.metadata or {})
            synced = self.sync.build_betta_payload(client)
            metadata["last_synced_fields"] = synced
            metadata["last_confirmed"] = synced
            metadata["inbound_last_remote_hash"] = current_review["remote_hash"]
            mapping.metadata = metadata
            mapping.last_synced_at = timezone.now()
            mapping.save(update_fields=["metadata", "last_synced_at", "updated_at"])
            SyncAuditLog.objects.create(
                system=mapping.system,
                operation="apply_alegra_contact_changes",
                resource="contacts",
                external_id=str(mapping.external_id),
                actor=actor,
                result=SyncAuditLog.RESULT_SUCCESS,
                detail=f"Cambios externos aplicados al cliente {client.pk}: {', '.join(sorted(approved))}.",
                metadata={"fields": sorted(approved), "direction": "alegra_to_betta"},
            )
        return {"status": "updated", "updated_fields": sorted(approved)}
