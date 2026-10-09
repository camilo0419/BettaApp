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
from django.db import transaction
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


def _mapping(product: Producto):
    ct = ContentType.objects.get_for_model(Producto)
    return ExternalObjectMap.objects.filter(
        system=get_alegra_system(), resource_type="items", content_type=ct,
        object_id=product.pk, status=ExternalObjectMap.STATUS_ACTIVE,
    ).first()


def enqueue_product_sync(product: Producto, *, actor=None) -> AlegraProductWriteOperation:
    """Persiste la intención de sincronización junto con el guardado local."""
    system = get_alegra_system()
    mapping = _mapping(product)
    operation = AlegraProductWriteOperation.OP_UPDATE if mapping else AlegraProductWriteOperation.OP_CREATE
    external_id = str(mapping.external_id) if mapping else ""
    try:
        payload = build_item_payload(product, include_price=operation == AlegraProductWriteOperation.OP_CREATE)
        state = AlegraProductWriteOperation.STATE_PENDING
        error = ""
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


def process_pending_product_operations(*, limit=50, timeout=None, execute=False, actor=None):
    qs = AlegraProductWriteOperation.objects.filter(
        state=AlegraProductWriteOperation.STATE_PENDING,
    ).select_related("product", "system").order_by("created_at", "pk")[:max(int(limit), 1)]
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
            mapping = _mapping(operation.product)
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
                with transaction.atomic():
                    ct = ContentType.objects.get_for_model(Producto)
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
        except (AlegraWriteHTTPError, AlegraWriteResponseError, ProductSyncBlocked) as exc:
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
