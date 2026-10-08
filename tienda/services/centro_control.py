"""Indicadores y alertas internas del centro de control.

Este servicio solo lee la información de los módulos existentes.  La única
escritura que realiza es la creación idempotente de notificaciones internas
cuando un administrador lo solicita explícitamente desde el panel.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from tienda.models import (
    AlegraInvoiceStaging,
    CarteraGestion,
    CompromisoPago,
    Cotizacion,
    Notificacion,
    Solicitud,
    SolicitudTarea,
    Venta,
)
from tienda.services.cartera import cartera_rows, cartera_summary, STATUS_OVERDUE
from tienda.services.sync_freshness import financial_freshness


def _today(value: date | None = None) -> date:
    return value or timezone.localdate()


def _link(label, url_name, object_id, **extra):
    """Return a small, template-friendly row without duplicating screens."""
    return {"label": label, "url": reverse(url_name, args=[object_id]), **extra}


def _tasks_for_user(user):
    qs = SolicitudTarea.objects.select_related(
        "responsable__user", "solicitud", "proyecto"
    ).filter(activa=True).exclude(
        estado__in=[SolicitudTarea.ESTADO_TERMINADA, SolicitudTarea.ESTADO_APROBADA, SolicitudTarea.ESTADO_CANCELADA]
    )
    if not user.is_staff:
        qs = qs.filter(responsable__user=user)
    return qs


def centro_control_snapshot(user, hoy=None):
    """Build the dashboard from authoritative local records.

    No external request is made here.  Financial sections are explicitly
    marked as known-only and warn when staging has no synchronized data.
    """
    hoy = _today(hoy)
    solicitud_pendiente = Solicitud.objects.filter(
        estado__in=[Solicitud.ESTADO_NUEVA, Solicitud.ESTADO_REVISION, Solicitud.ESTADO_PENDIENTE_INFO]
    ).select_related("cliente", "producto")
    cotizaciones_pendientes = Cotizacion.objects.filter(
        activa=True,
        estado__in=[Cotizacion.ESTADO_BORRADOR, Cotizacion.ESTADO_ENVIADA, Cotizacion.ESTADO_VISTA],
    ).select_related("cliente")
    cotizaciones_sin_venta = Cotizacion.objects.filter(
        activa=True,
        estado=Cotizacion.ESTADO_APROBADA,
        ventas__isnull=True,
    ).select_related("cliente").distinct()
    ventas_pendientes = Venta.objects.filter(
        estado__in=[Venta.ESTADO_BORRADOR, Venta.ESTADO_CONFIRMADA, Venta.ESTADO_PROCESO]
    ).select_related("cliente")

    ordenes_pendientes = Solicitud.objects.filter(
        estado_produccion__in=[
            Solicitud.PROD_PENDIENTE_ASIGNAR,
            Solicitud.PROD_ASIGNADO,
            Solicitud.PROD_EN_PROCESO,
            Solicitud.PROD_CON_NOVEDAD,
            Solicitud.PROD_TERMINADO,
            Solicitud.PROD_CALIDAD,
            Solicitud.PROD_LISTO_ENTREGA,
        ]
    ).select_related("cliente", "producto")
    entregas_pendientes = ordenes_pendientes.filter(
        estado_produccion__in=[Solicitud.PROD_TERMINADO, Solicitud.PROD_CALIDAD, Solicitud.PROD_LISTO_ENTREGA]
    )
    tareas = _tasks_for_user(user)
    procesos_atrasados = tareas.filter(fecha_limite__lt=hoy)

    invoice_qs = AlegraInvoiceStaging.objects.select_related("matched_client")
    invoice_count = invoice_qs.count()
    latest_sync = invoice_qs.order_by("-last_synced_at").values_list("last_synced_at", flat=True).first()
    freshness = financial_freshness()
    financial_warning = " ".join(freshness["warnings"]) if freshness["warnings"] else None
    if not invoice_count:
        financial_warning = "No hay facturas Alegra sincronizadas; " + (financial_warning or "los indicadores de cartera no están disponibles.")

    rows = cartera_rows(queryset=invoice_qs)
    overdue_rows = [row for row in rows if row["is_overdue"] and row["balance"] > 0]
    commitments = CompromisoPago.objects.filter(
        estado=CompromisoPago.PENDIENTE,
        fecha_comprometida__lte=hoy + timedelta(days=7),
    ).select_related("cliente", "responsable", "factura")
    gestiones = CarteraGestion.objects.filter(
        fecha_seguimiento__isnull=False,
        fecha_seguimiento__lte=hoy,
    ).select_related("cliente", "factura", "responsable")

    return {
        "hoy": hoy,
        "commercial": {
            "solicitudes_pendientes": [_link(f"Solicitud #{item.id}", "panel_solicitud_detalle", item.id, object=item, detail=item.producto.nombre) for item in solicitud_pendiente],
            "cotizaciones_pendientes": [_link(item.numero, "panel_cotizacion_detalle", item.id, object=item, detail=item.cliente.nombre_comercial) for item in cotizaciones_pendientes],
            "cotizaciones_aprobadas_sin_venta": [_link(item.numero, "panel_cotizacion_detalle", item.id, object=item, detail=item.cliente.nombre_comercial) for item in cotizaciones_sin_venta],
            "ventas_pendientes": [_link(item.numero, "panel_venta_detalle", item.id, object=item, detail=item.cliente.nombre_comercial) for item in ventas_pendientes],
        },
        "operation": {
            "ventas_produccion": [_link(item.numero, "panel_venta_detalle", item.id, object=item, detail=item.cliente.nombre_comercial) for item in ventas_pendientes.filter(solicitud__isnull=False)],
            "ordenes_pendientes": [_link(f"Solicitud #{item.id}", "panel_solicitud_detalle", item.id, object=item, detail=item.get_estado_produccion_display()) for item in ordenes_pendientes],
            "procesos_atrasados": [_link(item.titulo, "produccion_tarea_detalle", item.id, object=item, detail=item.fecha_limite) for item in procesos_atrasados],
            "entregas_pendientes": [_link(f"Solicitud #{item.id}", "panel_solicitud_detalle", item.id, object=item, detail=item.get_estado_produccion_display()) for item in entregas_pendientes],
        },
        "collection": {
            "invoices_overdue": [{"object": row["invoice"], "url": reverse("alegra_factura_detalle", args=[row["invoice"].id]), "label": row["invoice"].number or row["invoice"].external_id, "detail": row["invoice"].matched_client or row["invoice"].client_name, "balance": row["balance"], "days": row["days_overdue"]} for row in overdue_rows],
            "commitments": [_link(f"{item.cliente} · {item.fecha_comprometida:%d/%m/%Y}", "panel_cartera_cliente", item.cliente_id, object=item, detail=item.estado) for item in commitments],
            "gestiones": [_link(f"{item.cliente} · {item.proxima_accion or item.resultado}", "panel_cartera_cliente", item.cliente_id, object=item, detail=item.fecha_seguimiento) for item in gestiones],
        },
        "summary": {
            "requests": solicitud_pendiente.count(),
            "quotes_pending": cotizaciones_pendientes.count(),
            "quotes_without_sale": cotizaciones_sin_venta.count(),
            "sales_pending": ventas_pendientes.count(),
            "orders_pending": ordenes_pendientes.count(),
            "processes_overdue": procesos_atrasados.count(),
            "deliveries_pending": entregas_pendientes.count(),
            "overdue_invoices": len(overdue_rows),
            "commitments": commitments.count(),
            "gestions": gestiones.count(),
            "tasks": tareas.count(),
        },
        "tasks": {
            "mine": [_link(item.titulo, "produccion_tarea_detalle", item.id, object=item, detail=item.get_estado_display()) for item in tareas.order_by("fecha_limite", "id")[:30]],
            "overdue": [_link(item.titulo, "produccion_tarea_detalle", item.id, object=item, detail=item.fecha_limite) for item in procesos_atrasados[:30]],
        },
        "financial": {
            "summary": cartera_summary(queryset=invoice_qs),
            "latest_sync": latest_sync,
            "warning": financial_warning,
            "invoice_count": invoice_count,
            "freshness": freshness,
            "definitive": freshness["current"],
        },
        "warnings": [financial_warning] if financial_warning else [],
    }


def _notify_once(*, user, event_key, title, message, url, kind=Notificacion.TIPO_SISTEMA, tarea=None, solicitud=None, proyecto=None):
    """Upsert one control event, protected by a database uniqueness constraint."""
    existing = Notificacion.objects.filter(usuario_destino=user, event_key=event_key).first()
    if existing:
        if existing.estado == Notificacion.ESTADO_RESUELTA:
            existing.estado = Notificacion.ESTADO_ABIERTA
            existing.resuelta_at = None
            existing.leida = False
            existing.save(update_fields=["estado", "resuelta_at", "leida"])
            return True
        return False
    try:
        with transaction.atomic():
            Notificacion.objects.create(
                usuario_destino=user,
                event_key=event_key,
                titulo=title[:160],
                mensaje=message[:255],
                tipo=kind,
                estado=Notificacion.ESTADO_ABIERTA,
                url_destino=url,
                tarea=tarea,
                solicitud=solicitud,
                proyecto=proyecto,
            )
        return True
    except IntegrityError:
        # Another worker won the unique insert race.  The event already exists.
        return False


@transaction.atomic
def refresh_control_alerts(actor, hoy=None):
    """Generate only verifiable internal alerts; safe to repeat."""
    hoy = _today(hoy)
    created = 0
    active_event_keys = set()
    overdue_tasks = _tasks_for_user(actor).filter(fecha_limite__lt=hoy)
    for task in overdue_tasks:
        recipient = task.responsable.user if task.responsable_id else actor
        event_key = f"control:task:{task.id}:user:{recipient.id}"
        active_event_keys.add(event_key)
        created += _notify_once(
            user=recipient,
            event_key=event_key,
            title=f"Tarea atrasada: {task.titulo}",
            message=f"La tarea tiene fecha límite {task.fecha_limite:%d/%m/%Y}.",
            url=reverse("produccion_tarea_detalle", args=[task.id]),
            kind=Notificacion.TIPO_TAREA,
            tarea=task,
            solicitud=task.solicitud,
            proyecto=task.proyecto,
        )

    quote_deadline = hoy + timedelta(days=7)
    quotes_due = Cotizacion.objects.filter(
        activa=True,
        fecha_vencimiento__gte=hoy,
        fecha_vencimiento__lte=quote_deadline,
        estado__in=[Cotizacion.ESTADO_BORRADOR, Cotizacion.ESTADO_ENVIADA, Cotizacion.ESTADO_VISTA],
    ).select_related("creada_por", "cliente")
    for quote in quotes_due:
        recipient = quote.creada_por or actor
        event_key = f"control:quote:{quote.id}:user:{recipient.id}"
        active_event_keys.add(event_key)
        created += _notify_once(
            user=recipient,
            event_key=event_key,
            title=f"Cotización próxima a vencer: {quote.numero}",
            message=f"La cotización vence el {quote.fecha_vencimiento:%d/%m/%Y}.",
            url=reverse("panel_cotizacion_detalle", args=[quote.id]),
        )

    for commitment in CompromisoPago.objects.filter(
        estado=CompromisoPago.PENDIENTE,
        fecha_comprometida__lt=hoy,
    ).select_related("responsable", "cliente"):
        event_key = f"control:commitment:{commitment.id}:user:{commitment.responsable_id}"
        active_event_keys.add(event_key)
        created += _notify_once(
            user=commitment.responsable,
            event_key=event_key,
            title="Compromiso de pago vencido",
            message=f"Revisar el compromiso de {commitment.cliente} con fecha {commitment.fecha_comprometida:%d/%m/%Y}.",
            url=reverse("panel_cartera_cliente", args=[commitment.cliente_id]),
        )

    stale_alerts = Notificacion.objects.filter(
        event_key__startswith="control:",
        estado=Notificacion.ESTADO_ABIERTA,
    )
    if not actor.is_staff:
        stale_alerts = stale_alerts.filter(usuario_destino=actor)
    if active_event_keys:
        stale_alerts = stale_alerts.exclude(event_key__in=active_event_keys)
    stale_alerts.update(estado=Notificacion.ESTADO_RESUELTA, resuelta_at=timezone.now(), leida=True)

    return {"created": created, "evaluated_at": timezone.now(), "actor_id": actor.id}
