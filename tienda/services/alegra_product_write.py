"""Cola y transporte saliente de productos hacia Alegra.

El módulo no realiza llamadas externas al guardar un producto. Las operaciones
quedan persistidas y solo el comando con ``--execute`` puede procesarlas.
"""

from __future__ import annotations

import hashlib
import json
import os
from decimal import Decimal
from typing import Any, Mapping

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tienda.models import (
    AlegraProductWriteOperation,
    ExternalObjectMap,
    ExternalSystem,
    Producto,
    SyncAuditLog,
)
from .alegra_import import get_alegra_system
from .alegra_write import (
    AlegraWriteClient,
    AlegraWriteHTTPError,
    AlegraWriteResponseError,
    AlegraWriteUncertain,
    authorize_external_write,
)
from .alegra_client import AlegraReadOnlyClient, extract_rows


class ProductSyncBlocked(ValidationError):
    """El producto no tiene datos comerciales inequívocos para Alegra."""


def _positive_price(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    value = Decimal(value)
    return value if value > 0 else None


def _catalog_price(product: Producto) -> Decimal | None:
    """Obtiene un precio técnico para el alta del ítem, no para facturación."""
    return _positive_price(product.precio_base_unidad) or _positive_price(product.precio_base_m2)


def build_item_payload(product: Producto, *, include_price: bool = True) -> dict[str, Any]:
    """Construye únicamente campos permitidos por POST/PUT /items.

    No inventa precios, impuestos, referencias, categorías externas ni
    inventario. En POST el precio técnico es obligatorio según Alegra; en PUT
    se omite deliberadamente para no sobrescribir el catálogo externo.
    """
    name = (product.nombre or "").strip()
    if not name:
        raise ProductSyncBlocked("El producto no tiene nombre.")
    if len(name) > 150:
        raise ProductSyncBlocked("El nombre supera el máximo aceptado por Alegra.")
    payload: dict[str, Any] = {
        "name": name,
        "type": "product",
    }
    if include_price:
        price = _catalog_price(product)
        if price is None:
            raise ProductSyncBlocked("POST /items exige un precio técnico positivo; no se inventó uno.")
        payload["price"] = [{"price": float(price)}]
    description = (product.descripcion_larga or product.descripcion_corta or "").strip()
    if description:
        payload["description"] = description[:500]
    return payload


def _key(operation: str, product_id: int, external_id: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(
        {"operation": operation, "product": product_id, "external_id": external_id, "payload": payload},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _active_mappings(product: Producto):
    ct = ContentType.objects.get_for_model(Producto)
    return list(ExternalObjectMap.objects.filter(
        system=get_alegra_system(), resource_type="items", content_type=ct,
        object_id=product.pk, status=ExternalObjectMap.STATUS_ACTIVE,
    ).order_by("pk"))


def _mapping(product: Producto):
    mappings = _active_mappings(product)
    return mappings[0] if len(mappings) == 1 else None


def enqueue_product_sync(product: Producto, *, actor=None) -> AlegraProductWriteOperation:
    """Persiste la intención de sincronización junto con el guardado local."""
    system = get_alegra_system()
    mappings = _active_mappings(product)
    mapping = mappings[0] if len(mappings) == 1 else None
    operation = AlegraProductWriteOperation.OP_UPDATE if mapping else AlegraProductWriteOperation.OP_CREATE
    external_id = str(mapping.external_id) if mapping else ""
    try:
        payload = build_item_payload(product, include_price=operation == AlegraProductWriteOperation.OP_CREATE)
        state = AlegraProductWriteOperation.STATE_PENDING
        error = ""
        if len(mappings) > 1:
            state = AlegraProductWriteOperation.STATE_BLOCKED
            error = "Existen múltiples vínculos activos para el producto; requiere revisión."
    except ProductSyncBlocked as exc:
        payload = {}
        state = AlegraProductWriteOperation.STATE_BLOCKED
        error = str(exc)[:500]
    key = _key(operation, product.pk, external_id, payload)

    existing = AlegraProductWriteOperation.objects.filter(
        system=system, product=product, operation=operation,
        state__in=[AlegraProductWriteOperation.STATE_PENDING, AlegraProductWriteOperation.STATE_SENT,
                   AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION],
    ).order_by("-id").first()
    if existing and existing.state != AlegraProductWriteOperation.STATE_PENDING:
        return existing
    if existing:
        existing.payload = payload
        existing.external_id = external_id
        existing.idempotency_key = key
        existing.state = state
        existing.error_message = error
        existing.save(update_fields=["payload", "external_id", "idempotency_key", "state", "error_message", "updated_at"])
        operation_obj = existing
    else:
        operation_obj = AlegraProductWriteOperation.objects.create(
            system=system, product=product, operation=operation,
            external_id=external_id, state=state, idempotency_key=key,
            payload=payload, error_message=error,
        )

    # The operation is already durable in the same transaction as the product.
    # on_commit is only used for post-commit audit activation, never as the sole
    # persistence mechanism.
    transaction.on_commit(lambda operation_id=operation_obj.pk: SyncAuditLog.objects.create(
        system=system, operation="enqueue_product_write", resource="items",
        external_id=external_id, actor=actor,
        result=SyncAuditLog.RESULT_PARTIAL if state == AlegraProductWriteOperation.STATE_BLOCKED else SyncAuditLog.RESULT_SUCCESS,
        detail=(error or f"Operación de producto {operation_id} persistida; pendiente de procesamiento."),
        metadata={"operation_id": operation_id, "state": state},
    ))
    return operation_obj


def process_pending_product_operations(*, limit=50, timeout=None, execute=False, actor=None, operation_id=None):
    qs = AlegraProductWriteOperation.objects.filter(
        state=AlegraProductWriteOperation.STATE_PENDING,
    ).select_related("product", "system").order_by("created_at", "pk")
    if operation_id is not None:
        qs = qs.filter(pk=operation_id)
    else:
        qs = qs[:max(int(limit), 1)]
    operations = list(qs)
    if not execute:
        return {"mode": "dry_run", "pending": len(operations), "results": []}
    if os.environ.get("ALEGRA_AUTOMATION_WRITES_ENABLED", "").casefold() != "true":
        raise ProductSyncBlocked("La automatización de escrituras permanece deshabilitada.")
    if os.environ.get("ALEGRA_EXTERNAL_WRITES_ENABLED", "").casefold() != "true":
        raise ProductSyncBlocked("Las escrituras externas permanecen deshabilitadas.")

    results = []
    for candidate in operations:
        with transaction.atomic():
            operation = AlegraProductWriteOperation.objects.select_for_update().select_related("product", "system").get(pk=candidate.pk)
            if operation.state != AlegraProductWriteOperation.STATE_PENDING:
                continue
            active_mappings = _active_mappings(operation.product)
            mapping = active_mappings[0] if len(active_mappings) == 1 else None
            if len(active_mappings) > 1:
                operation.state = AlegraProductWriteOperation.STATE_BLOCKED
                operation.error_message = "Existen múltiples vínculos activos; se bloqueó la escritura."
                operation.save(update_fields=["state", "error_message", "updated_at"])
                continue
            if operation.operation == AlegraProductWriteOperation.OP_CREATE and mapping:
                operation.state = AlegraProductWriteOperation.STATE_BLOCKED
                operation.error_message = "El producto ya tiene un vínculo externo; se bloqueó el POST."
                operation.save(update_fields=["state", "error_message", "updated_at"])
                continue
            if operation.operation == AlegraProductWriteOperation.OP_UPDATE and (not mapping or mapping.external_id != operation.external_id):
                operation.state = AlegraProductWriteOperation.STATE_BLOCKED
                operation.error_message = "El vínculo externo no coincide con la operación."
                operation.save(update_fields=["state", "error_message", "updated_at"])
                continue
            operation.state = AlegraProductWriteOperation.STATE_SENT
            operation.attempts += 1
            operation.last_attempt_at = timezone.now()
            operation.save(update_fields=["state", "attempts", "last_attempt_at", "updated_at"])

        authorization = authorize_external_write(
            client_id=operation.product_id, operation="POST" if operation.operation == "create" else "PUT",
            environment="production", confirmed=True, resource_type="items",
        )
        try:
            client = AlegraWriteClient(timeout=timeout)
            if operation.operation == AlegraProductWriteOperation.OP_CREATE:
                response = client.create_item(operation.payload, authorization=authorization)
                external_id = str(response["id"])
                # Persist the remote identity before attempting the local map.
                # If mapping fails, the operation remains recoverable and no
                # second POST can be issued automatically.
                with transaction.atomic():
                    locked = AlegraProductWriteOperation.objects.select_for_update().get(pk=operation.pk)
                    locked.external_id = external_id
                    locked.result_metadata = {"external_id": external_id, "http_status": client.last_status}
                    locked.save(update_fields=["external_id", "result_metadata", "updated_at"])
                with transaction.atomic():
                    ct = ContentType.objects.get_for_model(Producto)
                    existing_map = ExternalObjectMap.objects.filter(
                        system=operation.system, resource_type="items", external_id=external_id,
                    ).first()
                    if existing_map and (existing_map.object_id != operation.product_id or existing_map.content_type_id != ct.pk):
                        raise ProductSyncBlocked("El ID externo ya está vinculado a otro producto.")
                    if not existing_map:
                        ExternalObjectMap.objects.create(
                            system=operation.system, resource_type="items", external_id=external_id,
                            content_type=ct, object_id=operation.product_id,
                            status=ExternalObjectMap.STATUS_ACTIVE, last_synced_at=timezone.now(),
                            metadata={"source": "product_write", "operation_id": operation.pk},
                        )
            else:
                response = client.update_item(operation.external_id, operation.payload, authorization=authorization)
                external_id = operation.external_id
            with transaction.atomic():
                operation = AlegraProductWriteOperation.objects.select_for_update().get(pk=operation.pk)
                operation.state = AlegraProductWriteOperation.STATE_SYNCED
                operation.external_id = external_id
                operation.result_metadata = {"external_id": external_id, "http_status": client.last_status}
                operation.save(update_fields=["state", "external_id", "result_metadata", "updated_at"])
            results.append({"id": operation.pk, "state": operation.state, "attempts": operation.attempts})
        except AlegraWriteUncertain as exc:
            _finish_product_operation(operation.pk, AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION, str(exc), "uncertain")
            results.append({"id": operation.pk, "state": AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION, "attempts": operation.attempts})
        except IntegrityError as exc:
            _finish_product_operation(
                operation.pk, AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION,
                "POST confirmado, pero no fue posible guardar el vínculo local; requiere conciliación.",
                "local_mapping",
            )
            results.append({"id": operation.pk, "state": AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION, "attempts": operation.attempts})
        except ProductSyncBlocked as exc:
            _finish_product_operation(operation.pk, AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION, str(exc), "local_mapping")
            results.append({"id": operation.pk, "state": AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION, "attempts": operation.attempts})
        except (AlegraWriteHTTPError, AlegraWriteResponseError) as exc:
            _finish_product_operation(operation.pk, AlegraProductWriteOperation.STATE_FAILED, str(exc), getattr(exc, "status", "validation"))
            results.append({"id": operation.pk, "state": AlegraProductWriteOperation.STATE_FAILED, "attempts": operation.attempts})
    return {"mode": "execute", "pending": len(operations), "results": results}


def _finish_product_operation(operation_id, state, message, code):
    with transaction.atomic():
        operation = AlegraProductWriteOperation.objects.select_for_update().get(pk=operation_id)
        operation.state = state
        operation.error_code = str(code)[:40]
        operation.error_message = str(message)[:500]
        operation.save(update_fields=["state", "error_code", "error_message", "updated_at"])


def _normalized_name(value):
    return " ".join(str(value or "").casefold().split())


def _remote_id(row):
    return str(row.get("id") or "") if isinstance(row, dict) else ""


def _remote_matches_operation(row, operation):
    if not isinstance(row, dict) or not _remote_id(row):
        return False
    if _normalized_name(row.get("name")) != _normalized_name(operation.product.nombre):
        return False
    expected_type = operation.payload.get("type") if isinstance(operation.payload, dict) else None
    if expected_type and row.get("type") and str(row.get("type")) != expected_type:
        return False
    return True


def _find_remote_candidates(operation, *, timeout=None, max_pages=None):
    """Busca coincidencias exactas por GET; nunca crea ni actualiza ítems."""
    client = AlegraReadOnlyClient(timeout=timeout)
    responses = client.paged_get(
        "/items", limit=None, max_pages=max_pages,
        params={"mode": "advanced", "order_field": "name"},
    )
    rows = [row for response in responses for row in extract_rows(response.data)]
    declared_total = None
    for response in responses:
        metadata = response.data.get("metadata") if isinstance(response.data, dict) else None
        if isinstance(metadata, dict) and metadata.get("total") is not None:
            try:
                declared_total = int(metadata["total"])
                break
            except (TypeError, ValueError):
                pass
    last_rows = extract_rows(responses[-1].data) if responses else []
    complete = bool(responses) and (
        (declared_total is not None and len(rows) >= declared_total) or len(last_rows) < 30
    )
    return [row for row in rows if _remote_matches_operation(row, operation)], complete


def reconcile_product_operations(*, limit=50, timeout=None, max_pages=None, apply=False, actor=None):
    """Concilia estados sent/inciertos solo mediante GET y cambios locales."""
    operations = list(AlegraProductWriteOperation.objects.filter(
        state__in=[AlegraProductWriteOperation.STATE_SENT, AlegraProductWriteOperation.STATE_NEEDS_RECONCILIATION],
    ).select_related("product", "system").order_by("created_at", "pk")[:max(int(limit), 1)])
    results = []
    for candidate in operations:
        operation = AlegraProductWriteOperation.objects.get(pk=candidate.pk)
        product_maps = _active_mappings(operation.product)
        if len(product_maps) > 1:
            results.append({"id": operation.pk, "status": "blocked_multiple_local_maps", "external_id": operation.external_id})
            continue
        mapping = ExternalObjectMap.objects.filter(
            system=operation.system, resource_type="items", external_id=operation.external_id,
        ).first() if operation.external_id else None
        remote_rows = []
        if operation.external_id:
            try:
                remote = AlegraReadOnlyClient(timeout=timeout).get(f"/items/{operation.external_id}").data
                remote_rows = [remote] if _remote_matches_operation(remote, operation) else []
            except Exception as exc:
                results.append({"id": operation.pk, "status": "blocked", "reason": f"GET failed: {exc.__class__.__name__}"})
                continue
        else:
            try:
                remote_rows, coverage_complete = _find_remote_candidates(operation, timeout=timeout, max_pages=max_pages)
            except Exception as exc:
                results.append({"id": operation.pk, "status": "blocked", "reason": f"GET failed: {exc.__class__.__name__}"})
                continue
            if not coverage_complete:
                results.append({"id": operation.pk, "status": "blocked_incomplete_coverage", "external_id": ""})
                continue

        if mapping and mapping.object_id == operation.product_id and mapping.status == ExternalObjectMap.STATUS_ACTIVE and remote_rows:
            status = "ready_existing_map"
        elif len(remote_rows) == 1 and not mapping:
            status = "ready_create_map"
        elif mapping and mapping.object_id != operation.product_id:
            status = "blocked_conflicting_map"
        elif not remote_rows:
            status = "blocked_remote_not_confirmed"
        else:
            status = "blocked_ambiguous_remote"

        if apply and status in {"ready_existing_map", "ready_create_map"}:
            with transaction.atomic():
                locked = AlegraProductWriteOperation.objects.select_for_update().get(pk=operation.pk)
                ct = ContentType.objects.get_for_model(Producto)
                external_id = operation.external_id or _remote_id(remote_rows[0])
                existing = ExternalObjectMap.objects.filter(
                    system=locked.system, resource_type="items", external_id=external_id,
                ).first()
                if existing and (existing.object_id != locked.product_id or existing.content_type_id != ct.pk):
                    status = "blocked_conflicting_map"
                else:
                    if not existing:
                        ExternalObjectMap.objects.create(
                            system=locked.system, resource_type="items", external_id=external_id,
                            content_type=ct, object_id=locked.product_id,
                            status=ExternalObjectMap.STATUS_ACTIVE, last_synced_at=timezone.now(),
                            metadata={"source": "product_reconciliation", "operation_id": locked.pk},
                        )
                    locked.external_id = external_id
                    locked.state = AlegraProductWriteOperation.STATE_SYNCED
                    locked.error_message = ""
                    locked.error_code = ""
                    locked.result_metadata = {**(locked.result_metadata or {}), "reconciled_by": "GET"}
                    locked.save(update_fields=["external_id", "state", "error_message", "error_code", "result_metadata", "updated_at"])
                    status = "synced"
        results.append({"id": operation.pk, "status": status, "external_id": operation.external_id or (_remote_id(remote_rows[0]) if len(remote_rows) == 1 else "")})
    return {"mode": "apply" if apply else "dry_run", "processed": len(operations), "results": results}
