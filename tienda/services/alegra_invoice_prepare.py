"""Preparación local de facturas Alegra, sin solicitudes externas."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction

from tienda.models import AlegraInvoicePreparation, ExternalObjectMap, Venta


def _money(value):
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def _map_for(resource_type, obj):
    ct = ContentType.objects.get_for_model(obj.__class__)
    return ExternalObjectMap.objects.filter(
        system__code="alegra", resource_type=resource_type,
        content_type=ct, object_id=obj.pk, status=ExternalObjectMap.STATUS_ACTIVE,
    ).first()


def _snapshot_hash(venta):
    values = {
        "venta": venta.pk,
        "updated": venta.actualizado.isoformat() if venta.actualizado else "",
        "total": _money(venta.total),
        "items": [
            {"id": item.pk, "producto": item.producto_id, "cantidad": str(item.cantidad),
             "precio": _money(item.precio_unitario), "descuento": _money(item.descuento),
             "impuesto": _money(item.impuesto), "activo": item.activo}
            for item in venta.items.order_by("orden", "id")
        ],
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def prepare_invoice(venta, *, mode, reference="", description="", actor=None, service_external_id=""):
    if venta.estado != Venta.ESTADO_CONFIRMADA:
        raise ValidationError("Solo se pueden preparar facturas de ventas confirmadas.")
    if venta.facturas_alegra.exists():
        raise ValidationError("La venta ya tiene una factura Alegra vinculada.")
    client_map = _map_for("contacts", venta.cliente)
    errors, warnings, lines = [], [], []
    if not client_map:
        errors.append("El cliente no tiene un vínculo externo activo.")
    items = list(venta.items.filter(activo=True).select_related("producto"))
    if not items:
        errors.append("La venta no tiene ítems activos.")
    if mode == AlegraInvoicePreparation.MODE_DETAILED:
        for item in items:
            mapping = _map_for("items", item.producto) if item.producto else None
            if not mapping:
                errors.append(f"El producto de la línea {item.pk} no tiene vínculo Alegra.")
                continue
            if not str(mapping.external_id).isdigit():
                errors.append(f"El ID externo del producto de la línea {item.pk} no es numérico.")
                continue
            subtotal = item.cantidad * item.precio_unitario
            if item.descuento and not subtotal:
                errors.append(f"La línea {item.pk} tiene descuento sin base válida.")
            discount = (item.descuento / subtotal * Decimal("100")) if subtotal else Decimal("0")
            if item.impuesto:
                warnings.append(f"La línea {item.pk} requiere configuración de impuesto Alegra.")
                errors.append("Hay impuestos locales sin código externo confirmado.")
            lines.append({"id": int(mapping.external_id), "description": item.descripcion,
                          "quantity": str(item.cantidad), "price": _money(item.precio_unitario),
                          "discount": str(discount.quantize(Decimal("0.01"))),
                          "tax_amount_local": _money(item.impuesto)})
    elif mode == AlegraInvoicePreparation.MODE_CONSOLIDATED_SERVICE:
        if not service_external_id or not str(service_external_id).isdigit():
            errors.append("Debe configurarse un ID externo numérico para el ítem SERVICIO.")
        if any(item.impuesto for item in items):
            errors.append("No se puede consolidar con impuestos sin equivalencia fiscal confirmada.")
        if any(item.descuento for item in items):
            errors.append("No se puede consolidar con descuentos cuya base fiscal no pueda conservarse.")
        if service_external_id and str(service_external_id).isdigit():
            lines = [{"id": int(service_external_id), "description": description or "Servicio",
                      "reference": reference, "quantity": "1", "price": _money(venta.subtotal)}]
        warnings.append("La modalidad consolidada usa un único ítem SERVICIO y no modifica la venta local.")
    else:
        errors.append("Modalidad de facturación no reconocida.")
    payload = {"client": int(client_map.external_id) if client_map and str(client_map.external_id).isdigit() else (client_map.external_id if client_map else ""),
               "date": str(venta.fecha_venta), "dueDate": str(venta.fecha_venta), "items": lines,
               "reference": reference, "description": description, "service_external_id": service_external_id}
    status = AlegraInvoicePreparation.STATUS_READY if not errors else AlegraInvoicePreparation.STATUS_BLOCKED
    with transaction.atomic():
        preparation, _ = AlegraInvoicePreparation.objects.update_or_create(
            venta=venta,
            defaults={"mode": mode, "status": status, "payload": payload, "warnings": warnings,
                      "validation_errors": errors, "sale_snapshot_hash": _snapshot_hash(venta), "prepared_by": actor},
        )
    return preparation
