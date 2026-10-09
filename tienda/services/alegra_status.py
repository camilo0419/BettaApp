"""Estado operativo registrado de la integración con Alegra.

Este módulo no realiza llamadas de red. El estado se deriva exclusivamente de
la auditoría y de la cola persistente, por lo que puede mostrarse en el panel
sin convertir cada carga de página en una comprobación contra Alegra.
"""

from __future__ import annotations

import os

from tienda.models import AlegraWriteOperation, SyncAuditLog


READ_RESOURCES = ("contacts", "items", "invoices", "payments")


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
