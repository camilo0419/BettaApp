"""Estado operativo registrado de la integración con Alegra.

Este módulo no realiza llamadas de red. El estado se deriva exclusivamente de
la auditoría y de la cola persistente, por lo que puede mostrarse en el panel
sin convertir cada carga de página en una comprobación contra Alegra.
"""

from __future__ import annotations

import os
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from tienda.services.alegra_client import (
    AlegraConfigurationError,
    AlegraError,
    AlegraHTTPError,
    AlegraReadOnlyClient,
)
from tienda.models import AlegraWriteOperation, SyncAuditLog


READ_RESOURCES = ("contacts", "items", "invoices", "payments")
CONNECTION_CACHE_KEY = "alegra:connection:default"
CONNECTION_LOCK_KEY = "alegra:connection:lock:default"
CONNECTION_TTL_SECONDS = 300


def _state_from_record(record: dict | None) -> dict:
    if not record:
        return {
            "state": "unknown", "label": "Verificando", "reason": "Sin comprobación registrada.",
            "checked_at": None, "last_success_at": None,
        }
    checked_at = record.get("checked_at")
    if isinstance(checked_at, str):
        try:
            checked_at = timezone.datetime.fromisoformat(checked_at)
        except ValueError:
            checked_at = None
    if checked_at and timezone.is_naive(checked_at):
        checked_at = timezone.make_aware(checked_at)
    stale = not checked_at or timezone.now() - checked_at > timedelta(seconds=CONNECTION_TTL_SECONDS)
    state = record.get("state", "unknown")
    if state == "connected" and stale:
        state = "warning"
    labels = {"connected": "Conectado", "disconnected": "Sin conexión", "warning": "Advertencia", "unknown": "Verificando"}
    return {**record, "state": state, "label": labels.get(state, "Verificando"), "checked_at": checked_at.isoformat() if checked_at else None}


def connection_state() -> dict:
    """Estado para UI; no consulta la red y respeta la caché de comprobación."""
    cached = cache.get(CONNECTION_CACHE_KEY)
    if cached:
        return _state_from_record(cached)
    audit = SyncAuditLog.objects.filter(operation="alegra_connection_check").order_by("-created_at", "-id").first()
    if not audit:
        return _state_from_record(None)
    metadata = audit.metadata if isinstance(audit.metadata, dict) else {}
    return _state_from_record({
        "state": metadata.get("state", "warning"),
        "reason": audit.detail or "Sin detalle disponible.",
        "checked_at": audit.created_at.isoformat(),
        "last_success_at": metadata.get("last_success_at"),
    })


def check_connection(*, force=False, timeout=5) -> dict:
    """Comprueba un endpoint ligero mediante GET y registra solo metadatos."""
    cached = cache.get(CONNECTION_CACHE_KEY)
    if cached and not force:
        return _state_from_record(cached)
    if not cache.add(CONNECTION_LOCK_KEY, "1", timeout=30):
        return connection_state()
    now = timezone.now()
    previous_success = cached.get("last_success_at") if isinstance(cached, dict) else None
    if not previous_success:
        previous = SyncAuditLog.objects.filter(
            operation="alegra_connection_check", result=SyncAuditLog.RESULT_SUCCESS
        ).order_by("-created_at", "-id").first()
        previous_success = previous.created_at.isoformat() if previous else None
    try:
        try:
            response = AlegraReadOnlyClient(timeout=timeout).get(
                "/contacts", params={"start": 0, "limit": 1, "metadata": "true"}
            )
            if response.status != 200:
                raise AlegraHTTPError(response.status, f"Alegra respondió HTTP {response.status}.")
            record = {
                "state": "connected", "reason": "GET de comprobación exitoso.",
                "checked_at": now.isoformat(), "last_success_at": now.isoformat(),
            }
            result = SyncAuditLog.RESULT_SUCCESS
        except AlegraHTTPError as exc:
            state = "disconnected" if exc.status in (401, 403) else "warning"
            record = {"state": state, "reason": str(exc)[:200], "checked_at": now.isoformat(), "last_success_at": previous_success}
            result = SyncAuditLog.RESULT_ERROR
        except AlegraConfigurationError as exc:
            record = {"state": "disconnected", "reason": str(exc)[:200], "checked_at": now.isoformat(), "last_success_at": previous_success}
            result = SyncAuditLog.RESULT_ERROR
        except AlegraError as exc:
            record = {"state": "warning", "reason": str(exc)[:200], "checked_at": now.isoformat(), "last_success_at": previous_success}
            result = SyncAuditLog.RESULT_ERROR
        SyncAuditLog.objects.create(
            operation="alegra_connection_check", resource="connection", result=result,
            detail=record["reason"], metadata={
                "state": record["state"], "checked_at": record["checked_at"],
                "last_success_at": record.get("last_success_at"),
            },
        )
        cache.set(CONNECTION_CACHE_KEY, record, CONNECTION_TTL_SECONDS)
        return _state_from_record(record)
    finally:
        cache.delete(CONNECTION_LOCK_KEY)


def alegra_operational_status() -> dict:
    """Devuelve un resumen agregado y no sensible del estado conocido."""
    audits = SyncAuditLog.objects.filter(resource__in=READ_RESOURCES)
    last_attempt = audits.order_by("-created_at", "-id").first()
    last_success = audits.filter(result=SyncAuditLog.RESULT_SUCCESS).order_by("-created_at", "-id").first()
    last_error = audits.filter(result=SyncAuditLog.RESULT_ERROR).order_by("-created_at", "-id").first()

    writes = AlegraWriteOperation.objects.filter(resource_type="contacts")
    last_write = writes.order_by("-updated_at", "-id").first()

    configured = bool(
        os.environ.get("ALEGRA_EMAIL", "").strip()
        and os.environ.get("ALEGRA_API_TOKEN", "").strip()
    )
    if last_attempt is None:
        communication = "Sin comunicación registrada"
        authentication = "No verificada"
    elif last_attempt.result == SyncAuditLog.RESULT_SUCCESS:
        communication = "API accesible (última consulta exitosa)"
        authentication = "Validada por la última consulta"
    elif last_attempt.result == SyncAuditLog.RESULT_PARTIAL:
        communication = "Consulta parcial; requiere atención"
        authentication = "No concluyente"
    else:
        communication = "Última consulta con error"
        detail = (last_attempt.detail or "").lower()
        authentication = "Rechazada" if "401" in detail or "403" in detail else "No verificable"

    return {
        "configured": configured,
        "configuration_label": "Configuración presente" if configured else "Configuración incompleta",
        "communication": communication,
        "authentication": authentication,
        "last_attempt": last_attempt,
        "last_success": last_success,
        "last_error": last_error,
        "last_write": last_write,
        "pending_writes": writes.filter(
            state__in=(AlegraWriteOperation.STATE_PENDING, AlegraWriteOperation.STATE_SENT)
        ).count(),
        "failed_writes": writes.filter(
            state__in=(
                AlegraWriteOperation.STATE_FAILED,
                AlegraWriteOperation.STATE_CONFLICT,
                AlegraWriteOperation.STATE_NEEDS_RECONCILIATION,
            )
        ).count(),
        "writes_enabled": os.environ.get("ALEGRA_EXTERNAL_WRITES_ENABLED", "").casefold() == "true",
        "automation_enabled": os.environ.get("ALEGRA_AUTOMATION_WRITES_ENABLED", "").casefold() == "true",
    }
