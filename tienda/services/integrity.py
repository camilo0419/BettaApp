"""Servicios transaccionales para invariantes entre módulos comerciales."""

from __future__ import annotations

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from tienda.models import (
    AlegraInvoiceStaging,
    Cliente,
    ClientePuntoVenta,
    ExternalObjectMap,
    Proyecto,
    Venta,
    VentaFacturaAlegra,
)
from tienda.services.alegra_invoice_import import normalize_identification


def resolve_invoice_client(invoice):
    """Resuelve identidad por mapeo externo o identificación sin usar nombre."""
    candidate_ids = set()
    if invoice.matched_client_id:
        candidate_ids.add(invoice.matched_client_id)
    if invoice.client_external_id:
        contact_type = ContentType.objects.get_for_model(Cliente)
        mapped = ExternalObjectMap.objects.filter(
            system=invoice.system,
            resource_type="contacts",
            external_id=invoice.client_external_id,
            content_type=contact_type,
            status=ExternalObjectMap.STATUS_ACTIVE,
        ).first()
        if mapped and mapped.object_id:
            candidate_ids.add(mapped.object_id)
    identification = normalize_identification(invoice.client_identification)
    if identification:
        candidates = [
            client.pk
            for client in Cliente.objects.filter(activo=True)
            if normalize_identification(client.identificacion) == identification
        ]
        if len(candidates) == 1:
            candidate_ids.add(candidates[0])
        if len(candidates) > 1:
            raise ValidationError("La identificación de la factura tiene múltiples clientes candidatos.")
    if len(candidate_ids) > 1:
        raise ValidationError("Los datos de identidad de la factura son contradictorios.")
    return next(iter(candidate_ids), None)


def user_can_review_identity(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and user.is_staff
        and (user.is_superuser or user.has_perm("tienda.change_alegrainvoicestaging"))
    )


@transaction.atomic
def link_invoice_to_sale(invoice_id, sale_id, *, evidence, actor):
    """Vincula localmente una factura, rechazando contradicciones de identidad."""
    if not user_can_review_identity(actor):
        raise PermissionDenied("Se requiere permiso para revisar y vincular facturas.")
    evidence = (evidence or "").strip()
    if not evidence:
        raise ValidationError({"evidencia": "La vinculación requiere evidencia comercial."})
    invoice = AlegraInvoiceStaging.objects.select_for_update().get(pk=invoice_id)
    sale = Venta.objects.select_for_update().get(pk=sale_id)
    invoice_client_id = resolve_invoice_client(invoice)
    if invoice_client_id and invoice_client_id != sale.cliente_id:
        raise ValidationError("La factura y la venta pertenecen a clientes diferentes.")
    if not invoice_client_id and not user_can_review_identity(actor):
        raise PermissionDenied("La factura no tiene identidad conciliada y requiere revisión autorizada.")
    link = VentaFacturaAlegra(
        venta=sale,
        factura=invoice,
        evidencia=evidence[:500],
        confirmado_por=actor,
    )
    link.full_clean()
    link.save()
    invoice.reconciliation_status = AlegraInvoiceStaging.CLASS_MATCHED
    invoice.save(update_fields=["reconciliation_status", "last_synced_at"])
    return link


@transaction.atomic
def set_project_points_of_sale(project_id, point_ids, *, actor=None):
    """Único servicio recomendado para escribir la relación M2M del proyecto."""
    project = Proyecto.objects.select_for_update().get(pk=project_id)
    points = list(ClientePuntoVenta.objects.filter(pk__in=set(point_ids)))
    if len(points) != len(set(point_ids)):
        raise ValidationError("Uno o más puntos de venta no existen.")
    if any(point.cliente_id != project.cliente_id for point in points):
        raise ValidationError("Todos los puntos de venta deben pertenecer al cliente del proyecto.")
    project.puntos_venta.set(points)
    return project
