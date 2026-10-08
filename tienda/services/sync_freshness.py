"""Estado y frescura de sincronizaciones externas, sin llamadas a Alegra."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from tienda.models import SyncAuditLog
SYNC_OPERATIONS = {
    "invoices": "sync_invoices",
    "payments": "sync_payments",
    "contacts": "sync_contacts",
    "items": "sync_items",
}


def sync_is_complete(responses, requested_limit, processed_count):
    """Solo marca cobertura completa si la API permite demostrarla."""
    if not responses:
        return False
    total = None
    for response in responses:
        data = response.data
        metadata = data.get("metadata") if isinstance(data, dict) else None
        if isinstance(metadata, dict) and metadata.get("total") is not None:
            try:
                total = int(metadata["total"])
            except (TypeError, ValueError):
                total = None
            if total is not None:
                break
    if total is not None:
        return processed_count >= total
    # If fewer records than the requested cap were received, pagination ended.
    return processed_count < max(int(requested_limit), 1)


def _audit_complete(log):
    return log.result == SyncAuditLog.RESULT_SUCCESS and bool(log.metadata.get("complete"))


def source_freshness(resource, *, now=None, threshold_minutes=None):
    now = now or timezone.now()
    threshold = threshold_minutes if threshold_minutes is not None else settings.ALEGRA_FINANCIAL_FRESHNESS_MINUTES
    operation = SYNC_OPERATIONS[resource]
    attempts = SyncAuditLog.objects.filter(operation=operation, resource=resource).order_by("-created_at", "-id")
    latest_attempt = attempts.first()
    successful = next((log for log in attempts if _audit_complete(log)), None)
    if latest_attempt is None:
        state = "never"
    elif latest_attempt.result == SyncAuditLog.RESULT_ERROR:
        state = "failed"
    elif latest_attempt.result == SyncAuditLog.RESULT_PARTIAL or not _audit_complete(latest_attempt):
        state = "partial"
    elif successful is None:
        state = "never"
    elif now - successful.created_at > timedelta(minutes=threshold):
        state = "stale"
    else:
        state = "current"
    return {
        "resource": resource,
        "state": state,
        "is_current": state == "current",
        "is_definitive": state == "current",
        "threshold_minutes": threshold,
        "last_attempt": latest_attempt.created_at if latest_attempt else None,
        "last_attempt_result": latest_attempt.result if latest_attempt else None,
        "last_success": successful.created_at if successful else None,
        "warning": freshness_warning(resource, state, successful.created_at if successful else None),
    }


def freshness_warning(resource, state, last_success=None):
    labels = {"invoices": "facturas", "payments": "pagos", "contacts": "clientes", "items": "productos"}
    label = labels.get(resource, resource)
    if state == "never":
        return f"No existe una sincronización completa de {label}; los datos financieros no son definitivos."
    if state == "failed":
        return f"El último intento de sincronizar {label} falló; se conservan datos anteriores y no se consideran actuales."
    if state == "partial":
        return f"La última sincronización de {label} fue parcial; los indicadores no representan cobertura completa."
    if state == "stale":
        return f"La última sincronización completa de {label} está desactualizada ({last_success:%d/%m/%Y %H:%M})."
    return ""


def financial_freshness():
    invoices = source_freshness("invoices")
    payments = source_freshness("payments")
    warnings = [item["warning"] for item in (invoices, payments) if item["warning"]]
    return {
        "invoices": invoices,
        "payments": payments,
        "current": invoices["is_current"] and payments["is_current"],
        "warnings": warnings,
    }
