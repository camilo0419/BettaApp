"""Aplicación transaccional futura de clientes Alegra, detrás de confirmación."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from tienda.models import Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_bidirectional_clients import BidirectionalClientSync
from tienda.services.alegra_contact_import import normalize_identity
from tienda.services.alegra_preimport_clients import ACTION_CREATE_LOCAL, build_preimport_plan


def _text(value, length):
    return str(value or "").strip()[:length]


def _address(row):
    value = row.get("address")
    return value if isinstance(value, dict) else {}


def _types(row):
    value = row.get("type")
    if isinstance(value, list):
        return {str(item).strip().casefold() for item in value if item not in (None, "")}
    return {str(value).strip().casefold()} if value not in (None, "") else set()


def _client_values(row):
    address = _address(row)
    raw_identification = _text(row.get("identification"), 60)
    explicit_dv = _text(row.get("verificationDigit") or row.get("dv"), 4)
    if "-" in raw_identification:
        raw_identification, inferred_dv = [part.strip() for part in raw_identification.rsplit("-", 1)]
    else:
        inferred_dv = ""
    types = _types(row)
    identification_type = _text(row.get("identificationType") or row.get("identification_type"), 20).casefold()
    identification_type = {"passport": "pasaporte", "cedula": "cc", "cédula": "cc"}.get(identification_type, identification_type)
    if identification_type not in {choice[0] for choice in Cliente.TIPO_IDENTIFICACION_CHOICES}:
        identification_type = ""
    is_company = bool(types & {"company", "empresa"}) or identification_type == Cliente.ID_NIT
    name = _text(row.get("name"), 180)
    return {
        "tipo_cliente": Cliente.TIPO_EMPRESA if is_company else Cliente.TIPO_PERSONA,
        "nombre": name,
        "razon_social": name if is_company else "",
        "identificacion": raw_identification,
        "tipo_identificacion": identification_type,
        "digito_verificacion": explicit_dv or inferred_dv,
        "email": _text(row.get("email"), 254),
        "telefono": _text(row.get("phonePrimary"), 40),
        "telefono_secundario": _text(row.get("phoneSecondary"), 40),
        "celular": _text(row.get("mobile"), 40),
        "direccion": _text(address.get("address"), 255),
        "ciudad": _text(address.get("city"), 120),
        "departamento": _text(address.get("department"), 120),
        "pais": _text(address.get("country"), 80),
        "codigo_postal": _text(address.get("zipCode"), 20),
        "activo": str(row.get("status") or "").casefold() != "inactive",
    }


def _external_id(row):
    return str(row.get("id") or row.get("external_id") or "").strip()


def apply_create_plan(rows: Iterable[Mapping[str, Any]], *, system, actor=None):
    """Aplica solo CREATE_LOCAL; cada cliente y su mapeo son una unidad atómica.

    Esta función no consulta ni escribe Alegra. El comando exige confirmación y
    entorno seguro antes de invocarla.
    """
    rows_by_external = {_external_id(row): row for row in rows if _external_id(row)}
    content_type = ContentType.objects.get_for_model(Cliente)
    result = Counter()
    errors = []
    created_ids = []
    for external_id, row in rows_by_external.items():
        try:
            with transaction.atomic():
                existing = list(
                    ExternalObjectMap.objects.select_for_update().filter(
                        system=system, resource_type="contacts", external_id=external_id,
                    )
                )
                active = [item for item in existing if item.status == ExternalObjectMap.STATUS_ACTIVE]
                if active:
                    result["NO_ACTION"] += 1
                    continue
                values = _client_values(row)
                if not values["nombre"] or not normalize_identity(values["identificacion"]):
                    result["SKIP_INVALID"] += 1
                    continue
                local_matches = []
                for client in Cliente.objects.select_for_update().only("id", "identificacion").iterator(chunk_size=500):
                    if normalize_identity(client.identificacion) == normalize_identity(values["identificacion"]):
                        local_matches.append(client.pk)
                if local_matches:
                    result["REVIEW_CONFLICT"] += 1
                    errors.append(f"{external_id}: la identificación ya existe localmente.")
                    continue
                client = Cliente(**values)
                client.full_clean()
                client.save(force_insert=True)
                ExternalObjectMap.objects.create(
                    system=system,
                    resource_type="contacts",
                    external_id=external_id,
                    content_type=content_type,
                    object_id=client.pk,
                    status=ExternalObjectMap.STATUS_ACTIVE,
                    last_synced_at=timezone.now(),
                    metadata={"source": "alegra_initial_import", "phase": "7.3"},
                )
                created_ids.append(client.pk)
                result["CREATE_LOCAL"] += 1
        except (ValidationError, IntegrityError, ValueError) as exc:
            result["FAILED"] += 1
            errors.append(f"{external_id}: {str(exc)[:160]}")
    SyncAuditLog.objects.create(
        system=system,
        operation="alegra_initial_client_import",
        resource="contacts",
        actor=actor,
        result=SyncAuditLog.RESULT_SUCCESS if not errors else SyncAuditLog.RESULT_PARTIAL,
        detail=f"Creados: {result['CREATE_LOCAL']}; omitidos: {result['NO_ACTION'] + result['SKIP_INVALID']}; errores: {len(errors)}.",
        metadata={"created": result["CREATE_LOCAL"], "skipped": result["NO_ACTION"] + result["SKIP_INVALID"], "errors": len(errors), "phase": "7.3"},
    )
    return {"counts": dict(result), "errors": errors, "created_ids": created_ids}


def apply_remote_updates(rows: Iterable[Mapping[str, Any]], comparisons: Iterable[Mapping[str, Any]], *, system):
    """Aplica solo cambios remotos previamente clasificados como PENDING."""
    rows_by_id = {_external_id(row): row for row in rows if _external_id(row)}
    sync = BidirectionalClientSync()
    result = Counter()
    errors = []
    for comparison in comparisons:
        if comparison.get("state") != "PENDING" or not comparison.get("remote_fields"):
            continue
        external_id = comparison.get("external_id", "")
        try:
            with transaction.atomic():
                mapping = ExternalObjectMap.objects.select_for_update().get(system=system, resource_type="contacts", external_id=external_id, status=ExternalObjectMap.STATUS_ACTIVE)
                if mapping.content_type_id != ContentType.objects.get_for_model(Cliente).pk:
                    raise ValidationError("El mapeo no corresponde a un cliente.")
                client = Cliente.objects.select_for_update().get(pk=mapping.object_id)
                values = sync._external_values(rows_by_id[external_id])
                for field_name in comparison["remote_fields"]:
                    value = values.get(field_name, "")
                    if value:
                        setattr(client, field_name, value)
                client.full_clean()
                client.save(update_fields=[*comparison["remote_fields"], "fecha_actualizacion"])
                metadata = dict(mapping.metadata or {})
                metadata["last_confirmed"] = values
                mapping.metadata = metadata
                mapping.last_synced_at = timezone.now()
                mapping.save(update_fields=["metadata", "last_synced_at", "updated_at"])
                result["UPDATED"] += 1
        except (ValidationError, IntegrityError, KeyError, ExternalObjectMap.DoesNotExist, Cliente.DoesNotExist) as exc:
            result["FAILED"] += 1
            errors.append(f"{external_id}: {str(exc)[:160]}")
    return {"counts": dict(result), "errors": errors}
