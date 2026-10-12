"""Cola durable y segura para operaciones salientes de contactos.

La cola no hace llamadas HTTP al guardar un registro. Un comando controlado
la inspecciona y, solo con una autorización de sistema explícita, puede
entregar una operación al adaptador de escritura existente.
"""

from __future__ import annotations

from typing import Any
import os
from datetime import datetime, timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_write import (
    AlegraError,
    AlegraContactWriteService,
    AlegraWriteClient,
    RemoteCandidateConflict,
    WriteConflict,
    authorize_external_write,
    build_contact_payload,
)
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.alegra_contact_import import normalize_identity
from tienda.services.alegra_inbound_sync import InboundClientSyncService
from tienda.services.database_lock import DatabaseLockUnavailable, advisory_lock

OUTBOUND_PROCESS_LOCK = "bettaapp:alegra:outbound:contacts"

def _assert_remote_create_is_safe(client: Cliente, *, timeout=None, transport=None) -> None:
    """Bloquea altas si Alegra no puede confirmar que no existe un candidato.

    La consulta usa únicamente la identificación normalizada por Alegra. No se
    utiliza el nombre como criterio suficiente para autorizar una creación.
    """
    transport = transport or AlegraWriteClient(timeout=timeout)
    candidates = transport.find_candidates(identification=normalize_identity(client.identificacion))
    if candidates:
        raise RemoteCandidateConflict(
            f"Alegra devolvió {len(candidates)} candidato(s) por identificación; requiere revisión manual."
        )


def alegra_system() -> ExternalSystem:
    system, _ = ExternalSystem.objects.get_or_create(
        code="alegra",
        defaults={
            "name": "Alegra",
            "environment": ExternalSystem.ENVIRONMENT_PRODUCTION,
            "status": ExternalSystem.STATUS_ACTIVE,
            "config": {"api_version": "v1", "read_only": True},
        },
    )
    return system


def _persist_create_validation_block(client: Cliente, exc: ValidationError, *, actor=None) -> AlegraWriteOperation:
    """Registra de forma durable una alta que aún no tiene payload válido.

    El formulario debe conservar la intención de sincronización aunque falten
    datos fiscales. La operación queda bloqueada para que el procesador nunca
    intente enviar un payload incompleto; una edición que corrija los datos
    producirá una nueva clave idempotente y podrá preparar una alta pendiente.
    """
    system = alegra_system()
    snapshot = {
        "tipo_cliente": client.tipo_cliente,
        "tipo_identificacion": client.tipo_identificacion,
        "identificacion": client.identificacion,
        "digito_verificacion": client.digito_verificacion,
        "regimen_tributario": client.regimen_tributario,
        "primer_nombre": client.primer_nombre,
        "primer_apellido": client.primer_apellido,
        "razon_social": client.razon_social,
    }
    key = BidirectionalClientSync.idempotency_key(
        "betta_to_alegra_create_validation", str(client.pk), snapshot,
    )
    operation, created = AlegraWriteOperation.objects.get_or_create(
        system=system,
        client=client,
        operation=AlegraWriteOperation.OP_CREATE,
        idempotency_key=key,
        defaults={
            "state": AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            "error_code": "payload_validation",
            "error_message": str(exc)[:500],
            "reconciliation_reason": "El cliente no tiene datos obligatorios para construir el payload de Alegra.",
            "result_metadata": {"blocked_reason": "payload_validation"},
        },
    )
    if created:
        SyncAuditLog.objects.create(
            system=system,
            operation="enqueue_contact_create",
            resource="contacts",
            actor=actor,
            result=SyncAuditLog.RESULT_ERROR,
            detail="Alta bloqueada por datos locales incompletos; requiere corrección.",
            metadata={
                "client_id": client.pk,
                "queued": False,
                "state": "needs_reconciliation",
                "error_code": "payload_validation",
            },
        )
    return operation


def enqueue_create(client: Cliente, *, actor=None, timeout=None, defer_remote_check=False) -> AlegraWriteOperation | None:
    """Persiste la intención antes del GET preventivo.

    La comprobación remota sigue siendo obligatoria para autorizar el POST,
    pero un fallo transitorio no puede perder la intención de sincronización.
    """
    if client.alegra_sync_policy != Cliente.ALEGRA_SYNC_AUTO:
        SyncAuditLog.objects.create(
            system=alegra_system(), operation="enqueue_contact_create", resource="contacts",
            actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
            detail="Alta automática bloqueada: cliente histórico pendiente de conciliación.",
            metadata={"client_id": client.pk, "queued": False, "state": "historical_pending"},
        )
        return None
    content_type = ContentType.objects.get_for_model(Cliente)
    if ExternalObjectMap.objects.filter(
        system=alegra_system(), resource_type="contacts", content_type=content_type,
        object_id=client.pk, status=ExternalObjectMap.STATUS_ACTIVE,
    ).exists():
        return None
    try:
        payload = build_contact_payload(client)
    except ValidationError as exc:
        return _persist_create_validation_block(client, exc, actor=actor)

    key = BidirectionalClientSync.idempotency_key("betta_to_alegra_create", str(client.pk), payload)
    existing = AlegraWriteOperation.objects.filter(
        system=alegra_system(), client=client, operation=AlegraWriteOperation.OP_CREATE,
        state__in=[AlegraWriteOperation.STATE_PENDING, AlegraWriteOperation.STATE_SENT,
                   AlegraWriteOperation.STATE_NEEDS_RECONCILIATION],
    ).order_by("-id").first()
    if existing:
        # Una sola alta externa puede existir por cliente. Si aún no salió de
        # BettaApp, se actualiza la intención con la última fotografía; si ya
        # fue enviada o requiere conciliación, se conserva sin repetir POST.
        if existing.state == AlegraWriteOperation.STATE_PENDING and existing.idempotency_key != key:
            existing.idempotency_key = key
            existing.payload = payload
            existing.result_metadata = {
                **(existing.result_metadata or {}),
                "superseded_payload": True,
            }
            existing.save(update_fields=["idempotency_key", "payload", "result_metadata", "updated_at"])
        # Un bloqueo exclusivamente local por payload incompleto sí puede
        # resolverse con una edición posterior. Los bloqueos por candidato
        # remoto, POST incierto u otra conciliación siguen siendo terminales
        # para evitar duplicados.
        if not (
            existing.state == AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
            and existing.error_code == "payload_validation"
        ):
            return existing
    operation, _ = AlegraWriteOperation.objects.get_or_create(
        system=alegra_system(), client=client, operation=AlegraWriteOperation.OP_CREATE,
        idempotency_key=key, defaults={"state": AlegraWriteOperation.STATE_PENDING},
    )
    if operation.state != AlegraWriteOperation.STATE_PENDING:
        return operation
    if defer_remote_check:
        return operation
    try:
        _assert_remote_create_is_safe(client, timeout=timeout)
    except WriteConflict as exc:
        SyncAuditLog.objects.create(
            system=alegra_system(), operation="enqueue_contact_create", resource="contacts",
            actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
            detail="Alta bloqueada: candidato externo requiere revisión manual.",
            metadata={"client_id": client.pk, "queued": False, "state": "blocked_remote_candidate"},
        )
        operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
        operation.reconciliation_reason = str(exc)[:500]
        operation.error_code = "remote_candidate"
        operation.error_message = str(exc)[:500]
        operation.save(update_fields=["state", "reconciliation_reason", "error_code", "error_message", "updated_at"])
        return operation
    except (AlegraError, TimeoutError, OSError) as exc:
        retry_count = int((operation.result_metadata or {}).get("retry_count", 0)) + 1
        delay = min(3600, 60 * (2 ** min(retry_count - 1, 5)))
        operation.result_metadata = {
            **(operation.result_metadata or {}),
            "retry_count": retry_count,
            "retry_at": (timezone.now() + timedelta(seconds=delay)).isoformat(),
            "last_transient_error": exc.__class__.__name__,
        }
        operation.error_code = "remote_check"
        operation.error_message = "No fue posible verificar candidatos externos; se reintentará de forma controlada."
        operation.save(update_fields=["result_metadata", "error_code", "error_message", "updated_at"])
        SyncAuditLog.objects.create(
            system=alegra_system(), operation="enqueue_contact_create", resource="contacts",
            actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
            detail="Alta bloqueada: no fue posible verificar candidatos externos.",
            metadata={"client_id": client.pk, "queued": False, "state": "blocked_remote_check"},
        )
        return operation
    return operation


def enqueue_update_if_changed(client: Cliente, *, actor=None, timeout=None, defer_remote_check=False) -> AlegraWriteOperation | None:
    """Prepara una actualización tras un guardado local, sin ejecutar PUT.

    La consulta remota se hace fuera de una transacción de negocio y cualquier
    fallo queda auditado sin deshacer el guardado local. ``prepare_update``
    conserva las validaciones de baseline, conflictos e idempotencia.
    """
    content_type = ContentType.objects.get_for_model(Cliente)
    mapping = ExternalObjectMap.objects.filter(
        system=alegra_system(), resource_type="contacts", content_type=content_type,
        object_id=client.pk, status=ExternalObjectMap.STATUS_ACTIVE,
    ).select_related("system").first()
    if not mapping:
        return None
    metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
    baseline_state, baseline = InboundClientSyncService.baseline_info(metadata)
    if baseline_state != "confirmed":
        SyncAuditLog.objects.create(
            system=mapping.system, operation="enqueue_contact_update", resource="contacts",
            external_id=mapping.external_id, actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
            detail="Actualización local conservada; baseline ausente o incompleto para preparar PUT.",
            metadata={"client_id": client.pk, "state": f"blocked_{baseline_state}_baseline"},
        )
        return None
    if defer_remote_check:
        local_snapshot = BidirectionalClientSync().build_betta_payload(client)
        changed = [
            field for field, value in local_snapshot.items()
            if str(value or "") != str(baseline.get(field, "") or "")
        ]
        if not changed:
            SyncAuditLog.objects.create(
                system=mapping.system, operation="enqueue_contact_update", resource="contacts",
                external_id=mapping.external_id, actor=actor, result=SyncAuditLog.RESULT_SUCCESS,
                detail="No se detectaron cambios locales frente al baseline; no se creó operación.",
                metadata={"client_id": client.pk, "state": "no_changes"},
            )
            return None
        key = BidirectionalClientSync.idempotency_key(
            "betta_to_alegra_update_intent", str(mapping.external_id), local_snapshot,
        )
        metadata_value = {
            **metadata,
            "deferred_prepare": True,
            "local_snapshot": local_snapshot,
            "baseline": baseline,
        }
        try:
            with advisory_lock(f"bettaapp:alegra:queue:contact:{client.pk}"):
                with transaction.atomic():
                    existing = AlegraWriteOperation.objects.select_for_update().filter(
                        system=mapping.system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
                        state__in=[AlegraWriteOperation.STATE_PENDING, AlegraWriteOperation.STATE_SENT,
                                   AlegraWriteOperation.STATE_NEEDS_RECONCILIATION],
                    ).order_by("-id").first()
                    if existing and (existing.result_metadata or {}).get("resolution_status") == "resolved_for_followup":
                        existing = None
                    if existing:
                        existing_metadata = existing.result_metadata if isinstance(existing.result_metadata, dict) else {}
                        if existing.state == AlegraWriteOperation.STATE_PENDING and existing_metadata.get("deferred_prepare"):
                            existing.idempotency_key = key
                            existing.external_id = str(mapping.external_id)
                            existing.result_metadata = metadata_value
                            existing.save(update_fields=["idempotency_key", "external_id", "result_metadata", "updated_at"])
                        return existing
                    operation, _ = AlegraWriteOperation.objects.get_or_create(
                        system=mapping.system, client=client, operation=AlegraWriteOperation.OP_UPDATE,
                        idempotency_key=key,
                        defaults={
                            "external_id": str(mapping.external_id),
                            "state": AlegraWriteOperation.STATE_PENDING,
                            "result_metadata": metadata_value,
                        },
                    )
                    return operation
        except DatabaseLockUnavailable as exc:
            SyncAuditLog.objects.create(
                system=mapping.system, operation="enqueue_contact_update", resource="contacts",
                external_id=mapping.external_id, actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
                detail="Actualización local conservada; otra operación está preparando la sincronización.",
                metadata={"client_id": client.pk, "state": "queue_busy"},
            )
            return None
    try:
        transport = AlegraWriteClient(timeout=timeout)
        remote = transport.get_contact(mapping.external_id)
        operation, _ = AlegraContactWriteService(transport=transport).prepare_update(
            client, mapping.system, external_id=mapping.external_id,
            remote_row=remote, baseline=baseline,
        )
        SyncAuditLog.objects.create(
            system=mapping.system, operation="enqueue_contact_update", resource="contacts",
            external_id=mapping.external_id, actor=actor, result=SyncAuditLog.RESULT_SUCCESS,
            detail="Actualización preparada; no se ejecutó PUT.",
            metadata={"client_id": client.pk, "operation_id": operation.pk},
        )
        return operation
    except (AlegraError, ValidationError, WriteConflict) as exc:
        SyncAuditLog.objects.create(
            system=mapping.system, operation="enqueue_contact_update", resource="contacts",
            external_id=mapping.external_id, actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
            detail=f"Preparación bloqueada: {exc.__class__.__name__}.",
            metadata={"client_id": client.pk, "state": "blocked"},
        )
        return None


def _materialize_deferred_update(operation, service: AlegraContactWriteService):
    """Completa una intención de actualización usando GET solo en el procesador."""
    metadata = operation.result_metadata if isinstance(operation.result_metadata, dict) else {}
    if not metadata.get("deferred_prepare"):
        return operation
    mapping = ExternalObjectMap.objects.filter(
        system=operation.system, resource_type="contacts",
        external_id=operation.external_id,
        content_type=ContentType.objects.get_for_model(Cliente), object_id=operation.client_id,
        status=ExternalObjectMap.STATUS_ACTIVE,
    ).first()
    if not mapping:
        operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
        operation.error_code = "invalid_mapping"
        operation.reconciliation_reason = "La actualización requiere un mapeo activo válido."
        operation.save(update_fields=["state", "error_code", "reconciliation_reason", "updated_at"])
        return operation
    remote = service.transport.get_contact(operation.external_id)
    baseline = metadata.get("baseline")
    if not isinstance(baseline, dict):
        raise ValidationError("La intención de actualización no contiene un baseline válido.")
    comparison = BidirectionalClientSync().compare_linked_client(
        operation.client, remote, baseline,
    )
    # Alegra puede omitir campos opcionales en un GET. En ese caso
    # compare_linked_client no los clasifica como cambios locales para evitar
    # sobrescribir datos con vacío. Para una intención diferida sí conocemos
    # los valores que el usuario guardó: si el campo no vino en la respuesta,
    # debe seguir siendo una actualización pendiente, nunca un no-op.
    local_snapshot = metadata.get("local_snapshot")
    if isinstance(local_snapshot, dict):
        remote_field_keys = {
            "nombre": ("name", "nameObject"),
            "tipo_cliente": ("kindOfPerson",),
            "tipo_identificacion": ("identificationObject",),
            "identificacion": ("identificationObject",),
            "digito_verificacion": ("identificationObject",),
            "email": ("email",),
            "telefono": ("phonePrimary",),
            "telefono_secundario": ("phoneSecondary",),
            "celular": ("mobile",),
            "direccion": ("address.address",),
            "ciudad": ("address.city",),
            "departamento": ("address.department",),
            "pais": ("address.country",),
            "codigo_postal": ("address.zipCode",),
        }

        def remote_has_field(field):
            keys = remote_field_keys.get(field, ())
            for key in keys:
                if key.startswith("address."):
                    address = remote.get("address")
                    if isinstance(address, dict) and key.split(".", 1)[1] in address:
                        return True
                elif key == "nameObject":
                    if isinstance(remote.get(key), dict):
                        return True
                elif key == "identificationObject":
                    if isinstance(remote.get(key), dict):
                        return True
                elif key in remote:
                    return True
            return False

        for field, value in local_snapshot.items():
            if str(value or "").strip() == str(baseline.get(field, "") or "").strip():
                continue
            if remote_has_field(field):
                continue
            if field not in comparison["local_fields"]:
                comparison["local_fields"].append(field)
            if field not in comparison["changed_fields"]:
                comparison["changed_fields"].append(field)
    if comparison["conflict_fields"]:
        operation.state = AlegraWriteOperation.STATE_CONFLICT
        operation.error_code = "conflict"
        operation.reconciliation_reason = "Existen cambios simultáneos; se requiere revisión manual."
        operation.save(update_fields=["state", "error_code", "reconciliation_reason", "updated_at"])
        return operation
    all_local_fields = list(comparison["local_fields"])
    local_fields = [
        field for field in all_local_fields
        if field not in AlegraContactWriteService.PROTECTED_UPDATE_FIELDS
    ]
    if not local_fields and comparison["remote_fields"]:
        operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
        operation.error_code = "remote_change"
        operation.reconciliation_reason = "Alegra tiene cambios no reflejados localmente; requiere revisión manual."
        operation.save(update_fields=["state", "error_code", "reconciliation_reason", "updated_at"])
        return operation
    if not local_fields and not comparison["changed_fields"]:
        # La intención se creó por un baseline parcial o por una edición que
        # finalmente ya coincide con Alegra. No hay PUT que ejecutar ni motivo
        # para clasificarla como cambio protegido.
        operation.state = AlegraWriteOperation.STATE_SYNCED
        operation.result_metadata = {
            **metadata,
            "deferred_prepare": False,
            "fields": [],
            "no_op": True,
            "confirmed_by_get": True,
        }
        operation.save(update_fields=["state", "result_metadata", "updated_at"])
        synced = BidirectionalClientSync().build_betta_payload(operation.client)
        # Solo confirmar en el baseline los campos que realmente llegaron en
        # la respuesta GET. Los campos omitidos siguen sin estar confirmados.
        address = remote.get("address") if isinstance(remote.get("address"), dict) else {}
        present_fields = {
            "nombre": "name" in remote or isinstance(remote.get("nameObject"), dict),
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
        synced = {field: value for field, value in synced.items() if present_fields.get(field, True)}
        mapping_metadata = dict(mapping.metadata or {})
        previous_confirmed = mapping_metadata.get("last_confirmed", {})
        if not isinstance(previous_confirmed, dict):
            previous_confirmed = {}
        mapping.metadata = {
            **mapping_metadata,
            "last_synced_fields": synced,
            "last_confirmed": {**previous_confirmed, **synced},
        }
        mapping.last_synced_at = timezone.now()
        mapping.save(update_fields=["metadata", "last_synced_at", "updated_at"])
        return operation
    if not local_fields:
        operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
        operation.error_code = "protected_change"
        operation.reconciliation_reason = "Los cambios detectados son tributarios o de identidad y requieren revisión manual."
        operation.save(update_fields=["state", "error_code", "reconciliation_reason", "updated_at"])
        return operation
    context = service._build_update_context(remote)
    payload = service._build_update_payload(operation.client, local_fields, external_context=context)
    operation.result_metadata = {
        **metadata,
        "deferred_prepare": False,
        "fields": local_fields,
        "prepared_payload": payload,
        "external_context": context,
    }
    operation.save(update_fields=["result_metadata", "updated_at"])
    return operation


def linked_client_update_candidates(*, limit=100, timeout=None, actor=None, persist=False):
    """Detecta cambios locales y prepara operaciones PUT sin ejecutarlas.

    ``limit`` es el máximo de candidatos preparados, no el número de mapas
    inspeccionados. Los mapas se recorren una sola vez mediante cursor estable
    por ``ExternalObjectMap.id`` para que un bloqueo no oculte los siguientes.
    """
    from tienda.services.alegra_write import AlegraContactWriteService, AlegraWriteClient

    system = alegra_system()
    content_type = ContentType.objects.get_for_model(Cliente)
    mapping_queryset = ExternalObjectMap.objects.filter(
        system=system, resource_type="contacts", content_type=content_type,
        status=ExternalObjectMap.STATUS_ACTIVE,
    ).select_related("content_type").order_by("id")
    target = max(int(limit), 1)
    service = AlegraContactWriteService(transport=AlegraWriteClient(timeout=timeout))
    prepared, blocked = [], []
    diagnostics = []
    last_id = 0
    while len(prepared) < target:
        mappings = list(mapping_queryset.filter(pk__gt=last_id)[:target])
        if not mappings:
            break
        for mapping in mappings:
            last_id = mapping.pk
            client = mapping.local_object
            if not mapping.external_id or not mapping.object_id:
                item = {"external_id": mapping.external_id, "reason": "invalid_mapping"}
                blocked.append(item); diagnostics.append(item); continue
            duplicate_maps = ExternalObjectMap.objects.filter(
                system=system, resource_type="contacts", content_type=content_type,
                object_id=mapping.object_id, status=ExternalObjectMap.STATUS_ACTIVE,
            ).count()
            if duplicate_maps != 1:
                item = {"external_id": mapping.external_id, "reason": "duplicate_active_maps"}
                blocked.append(item); diagnostics.append(item); continue
            metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
            baseline_state, baseline = InboundClientSyncService.baseline_info(metadata)
            if not client:
                item = {"external_id": mapping.external_id, "reason": "invalid_mapping"}
                blocked.append(item); diagnostics.append(item); continue
            if baseline_state != "confirmed":
                item = {"external_id": mapping.external_id, "reason": f"{baseline_state}_baseline"}
                blocked.append(item); diagnostics.append(item); continue
            try:
                remote = service.transport.get_contact(mapping.external_id)
                service._build_update_context(remote)  # valida contexto colombiano
                compare = BidirectionalClientSync().compare_linked_client(client, remote, baseline)
                if compare["conflict_fields"]:
                    item = {"external_id": mapping.external_id, "reason": "conflict"}
                    blocked.append(item); diagnostics.append(item); continue
                if not compare["local_fields"]:
                    item = {"external_id": mapping.external_id, "reason": "no_changes"}
                    blocked.append(item); diagnostics.append(item); continue
                if persist:
                    operation, _ = service.prepare_update(
                        client, system, external_id=mapping.external_id,
                        remote_row=remote, baseline=baseline,
                    )
                    prepared.append(operation.pk)
                else:
                    prepared.append(f"preview:{mapping.external_id}")
            except (ValidationError, WriteConflict) as exc:
                item = {"external_id": mapping.external_id, "reason": str(exc)[:180]}
                blocked.append(item); diagnostics.append(item)
            except Exception as exc:
                item = {"external_id": mapping.external_id, "reason": f"query_error:{exc.__class__.__name__}"}
                blocked.append(item); diagnostics.append(item)
    return {"prepared": prepared, "blocked": blocked, "diagnostics": diagnostics}


def enqueue_missing_creates(*, limit=100, actor=None, timeout=None):
    """Encola altas automáticas sin adelantar GET de operaciones pendientes.

    La selección no se corta por el primer cliente bloqueado: se recorren los
    elegibles hasta encontrar ``limit`` intenciones nuevas.
    """
    system = alegra_system()
    ct = ContentType.objects.get_for_model(Cliente)
    mapped_ids = set(ExternalObjectMap.objects.filter(
        system=system, resource_type="contacts", content_type=ct,
        status=ExternalObjectMap.STATUS_ACTIVE, object_id__isnull=False,
    ).values_list("object_id", flat=True))
    pending_create_ids = set(AlegraWriteOperation.objects.filter(
        client_id__isnull=False,
        operation=AlegraWriteOperation.OP_CREATE,
        state__in=[AlegraWriteOperation.STATE_PENDING, AlegraWriteOperation.STATE_SENT,
                   AlegraWriteOperation.STATE_NEEDS_RECONCILIATION],
    ).values_list("client_id", flat=True))
    queued = []
    for client in Cliente.objects.filter(
        alegra_sync_policy=Cliente.ALEGRA_SYNC_AUTO,
    ).exclude(pk__in=mapped_ids).order_by("pk"):
        if client.pk in pending_create_ids:
            continue
        operation = enqueue_create(client, actor=actor, timeout=timeout)
        if operation and operation.state == AlegraWriteOperation.STATE_PENDING:
            queued.append(operation.pk)
        if len(queued) >= max(int(limit), 1):
            break
    return queued


def process_pending_operations(*, limit=50, timeout=None, execute=False, actor=None, operation_id=None):
    """Procesa exclusivamente pendientes seguros; nunca reintenta inciertos."""
    candidates = AlegraWriteOperation.objects.filter(
        state=AlegraWriteOperation.STATE_PENDING,
        operation__in=[AlegraWriteOperation.OP_CREATE, AlegraWriteOperation.OP_UPDATE],
    ).select_related("client").order_by("created_at", "pk")
    if operation_id is not None:
        candidates = candidates.filter(pk=operation_id)
    operations = []
    now = timezone.now()
    for candidate in candidates:
        retry_at = (candidate.result_metadata or {}).get("retry_at")
        if retry_at:
            try:
                if datetime.fromisoformat(str(retry_at)) > now:
                    continue
            except (TypeError, ValueError):
                pass
        operations.append(candidate)
        if len(operations) >= max(int(limit), 1):
            break
    if not execute:
        return {"mode": "dry_run", "pending": len(operations), "results": []}
    if os.environ.get("ALEGRA_AUTOMATION_WRITES_ENABLED", "").casefold() != "true":
        raise WriteConflict("La automatización de escrituras no está habilitada por sistema.")
    transport = AlegraWriteClient(timeout=timeout)
    service = AlegraContactWriteService(transport=transport)
    results = []
    for operation in operations:
        # La autorización se limita al cliente y verbo de esta operación.
        method = "POST" if operation.operation == AlegraWriteOperation.OP_CREATE else "PUT"
        authorization = authorize_external_write(
            client_id=operation.client_id, operation=method,
            environment="production", confirmed=True,
        )
        try:
            if operation.operation == AlegraWriteOperation.OP_UPDATE:
                operation = _materialize_deferred_update(operation, service)
                if operation.state != AlegraWriteOperation.STATE_PENDING:
                    results.append({"id": operation.pk, "state": operation.state, "attempts": operation.attempts})
                    continue
            if operation.operation == AlegraWriteOperation.OP_CREATE:
                result = service.execute_create(operation.pk, authorization=authorization)
            else:
                result = service.execute_update(operation.pk, authorization=authorization)
        except (AlegraError, TimeoutError, OSError) as exc:
            retry_count = int((operation.result_metadata or {}).get("retry_count", 0)) + 1
            delay = min(3600, 60 * (2 ** min(retry_count - 1, 5)))
            operation.result_metadata = {
                **(operation.result_metadata or {}),
                "retry_count": retry_count,
                "retry_at": (timezone.now() + timedelta(seconds=delay)).isoformat(),
                "last_transient_error": exc.__class__.__name__,
            }
            operation.error_code = "remote_check"
            operation.error_message = "No fue posible verificar Alegra; se reintentará con backoff."
            operation.save(update_fields=["result_metadata", "error_code", "error_message", "updated_at"])
            SyncAuditLog.objects.create(
                system=operation.system, operation="process_outbound_contact", resource="contacts",
                external_id=operation.external_id, actor=actor,
                result=SyncAuditLog.RESULT_PARTIAL,
                detail="Operación temporalmente bloqueada por fallo remoto; no se escribió en Alegra.",
                metadata={"operation_id": operation.pk, "state": "pending_retry", "retry_count": retry_count},
            )
            results.append({"id": operation.pk, "state": "pending_retry", "attempts": operation.attempts})
            continue
        except ValidationError as exc:
            # La materialización diferida valida nuevamente el contexto remoto
            # antes de construir el PUT. Un contexto incompleto no es un fallo
            # transitorio ni autoriza un reintento: debe quedar registrado como
            # revisión para que no se seleccione indefinidamente con attempts=0.
            operation.state = AlegraWriteOperation.STATE_NEEDS_RECONCILIATION
            operation.error_code = "payload_validation"
            operation.error_message = str(exc)[:500]
            operation.reconciliation_reason = (
                "La actualización no pudo validarse con la información remota "
                "disponible; requiere revisión antes de continuar."
            )
            operation.save(update_fields=[
                "state", "error_code", "error_message", "reconciliation_reason", "updated_at",
            ])
            SyncAuditLog.objects.create(
                system=operation.system, operation="process_outbound_contact", resource="contacts",
                external_id=operation.external_id, actor=actor,
                result=SyncAuditLog.RESULT_PARTIAL,
                detail="Operación bloqueada por validación incompleta; no se escribió en Alegra.",
                metadata={"operation_id": operation.pk, "state": "needs_reconciliation", "error_code": "payload_validation"},
            )
            results.append({"id": operation.pk, "state": operation.state, "attempts": operation.attempts})
            continue
        results.append({"id": operation.pk, "state": result.state, "attempts": result.attempts})
        SyncAuditLog.objects.create(
            system=operation.system, operation="process_outbound_contact", resource="contacts",
            external_id=operation.external_id, actor=actor,
            result=SyncAuditLog.RESULT_SUCCESS if result.state == AlegraWriteOperation.STATE_SYNCED else SyncAuditLog.RESULT_ERROR,
            detail=f"Operación {operation.pk}: {result.state}.",
            metadata={"operation_id": operation.pk, "state": result.state, "attempts": result.attempts},
        )
    return {"mode": "execute", "pending": len(operations), "results": results}
