"""Cola durable y segura para operaciones salientes de contactos.

La cola no hace llamadas HTTP al guardar un registro. Un comando controlado
la inspecciona y, solo con una autorización de sistema explícita, puede
entregar una operación al adaptador de escritura existente.
"""

from __future__ import annotations

from typing import Any
import os

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from tienda.models import AlegraWriteOperation, Cliente, ExternalObjectMap, ExternalSystem, SyncAuditLog
from tienda.services.alegra_write import (
    AlegraError,
    AlegraContactWriteService,
    AlegraWriteClient,
    WriteConflict,
    authorize_external_write,
    build_contact_payload,
)
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync


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


def enqueue_create(client: Cliente, *, actor=None) -> AlegraWriteOperation | None:
    """Registra una alta local pendiente, sin consultar ni escribir Alegra."""
    content_type = ContentType.objects.get_for_model(Cliente)
    if ExternalObjectMap.objects.filter(
        system=alegra_system(), resource_type="contacts", content_type=content_type,
        object_id=client.pk, status=ExternalObjectMap.STATUS_ACTIVE,
    ).exists():
        return None
    try:
        payload = build_contact_payload(client)
    except ValidationError as exc:
        SyncAuditLog.objects.create(
            system=alegra_system(), operation="enqueue_contact_create", resource="contacts",
            actor=actor, result=SyncAuditLog.RESULT_ERROR, detail=str(exc)[:500],
            metadata={"client_id": client.pk, "queued": False},
        )
        return None
    key = BidirectionalClientSync.idempotency_key("betta_to_alegra_create", str(client.pk), payload)
    operation, _ = AlegraWriteOperation.objects.get_or_create(
        system=alegra_system(), client=client, operation=AlegraWriteOperation.OP_CREATE,
        idempotency_key=key, defaults={"state": AlegraWriteOperation.STATE_PENDING},
    )
    return operation


def enqueue_update_if_changed(client: Cliente, *, actor=None, timeout=None) -> AlegraWriteOperation | None:
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
    baseline = metadata.get("last_confirmed") or metadata.get("last_synced_fields")
    if not isinstance(baseline, dict) or not baseline:
        SyncAuditLog.objects.create(
            system=mapping.system, operation="enqueue_contact_update", resource="contacts",
            external_id=mapping.external_id, actor=actor, result=SyncAuditLog.RESULT_PARTIAL,
            detail="Actualización local conservada; falta baseline para preparar PUT.",
            metadata={"client_id": client.pk, "state": "blocked_missing_baseline"},
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


def linked_client_update_candidates(*, limit=100, timeout=None, actor=None, persist=False):
    """Detecta cambios locales y prepara operaciones PUT sin ejecutarlas."""
    from tienda.services.alegra_write import AlegraContactWriteService, AlegraWriteClient

    system = alegra_system()
    content_type = ContentType.objects.get_for_model(Cliente)
    mappings = list(ExternalObjectMap.objects.filter(
        system=system, resource_type="contacts", content_type=content_type,
        status=ExternalObjectMap.STATUS_ACTIVE,
    ).select_related("content_type")[: max(int(limit), 1)])
    service = AlegraContactWriteService(transport=AlegraWriteClient(timeout=timeout))
    prepared, blocked = [], []
    for mapping in mappings:
        client = mapping.local_object
        metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
        baseline = metadata.get("last_confirmed") or metadata.get("last_synced_fields")
        if not client or not isinstance(baseline, dict):
            blocked.append({"external_id": mapping.external_id, "reason": "missing_baseline"})
            continue
        try:
            remote = service.transport.get_contact(mapping.external_id)
            comparison = service._build_update_context(remote)  # valida contexto colombiano
            compare = BidirectionalClientSync().compare_linked_client(client, remote, baseline)
            if compare["conflict_fields"]:
                blocked.append({"external_id": mapping.external_id, "reason": "conflict"})
                continue
            if not compare["local_fields"]:
                blocked.append({"external_id": mapping.external_id, "reason": "no_changes"})
                continue
            if persist:
                operation, _ = service.prepare_update(
                    client, system, external_id=mapping.external_id,
                    remote_row=remote, baseline=baseline,
                )
                prepared.append(operation.pk)
            else:
                prepared.append(f"preview:{mapping.external_id}")
        except (ValidationError, WriteConflict) as exc:
            blocked.append({"external_id": mapping.external_id, "reason": str(exc)[:180]})
        except Exception as exc:
            blocked.append({"external_id": mapping.external_id, "reason": f"GET/prepare failed: {exc.__class__.__name__}"})
    return {"prepared": prepared, "blocked": blocked}


def enqueue_missing_creates(*, limit=100, actor=None):
    """Encola altas locales elegibles; nunca realiza HTTP."""
    system = alegra_system()
    ct = ContentType.objects.get_for_model(Cliente)
    mapped_ids = set(ExternalObjectMap.objects.filter(
        system=system, resource_type="contacts", content_type=ct,
        status=ExternalObjectMap.STATUS_ACTIVE, object_id__isnull=False,
    ).values_list("object_id", flat=True))
    queued = []
    for client in Cliente.objects.exclude(pk__in=mapped_ids).order_by("pk")[: max(int(limit), 1)]:
        operation = enqueue_create(client, actor=actor)
        if operation:
            queued.append(operation.pk)
    return queued


def process_pending_operations(*, limit=50, timeout=None, execute=False, actor=None):
    """Procesa exclusivamente pendientes seguros; nunca reintenta inciertos."""
    operations = list(AlegraWriteOperation.objects.filter(
        state=AlegraWriteOperation.STATE_PENDING,
        operation__in=[AlegraWriteOperation.OP_CREATE, AlegraWriteOperation.OP_UPDATE],
    ).select_related("client").order_by("created_at", "pk")[: max(int(limit), 1)])
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
        if operation.operation == AlegraWriteOperation.OP_CREATE:
            result = service.execute_create(operation.pk, authorization=authorization)
        else:
            result = service.execute_update(operation.pk, authorization=authorization)
        results.append({"id": operation.pk, "state": result.state, "attempts": result.attempts})
        SyncAuditLog.objects.create(
            system=operation.system, operation="process_outbound_contact", resource="contacts",
            external_id=operation.external_id, actor=actor,
            result=SyncAuditLog.RESULT_SUCCESS if result.state == AlegraWriteOperation.STATE_SYNCED else SyncAuditLog.RESULT_ERROR,
            detail=f"Operación {operation.pk}: {result.state}.",
            metadata={"operation_id": operation.pk, "state": result.state, "attempts": result.attempts},
        )
    return {"mode": "execute", "pending": len(operations), "results": results}
