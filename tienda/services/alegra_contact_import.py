"""Consulta, staging y conciliación local de contactos Alegra."""

from __future__ import annotations

import re

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from tienda.models import AlegraContactStaging, Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient, extract_rows
from tienda.services.alegra_import import get_alegra_system
from tienda.services.sync_freshness import sync_is_complete


def normalize_identity(value):
    return re.sub(r"[^0-9A-Za-z]", "", str(value or "")).casefold()


def normalize_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _text(value, length):
    return str(value or "")[:length]


def _address(contact):
    value = contact.get("address")
    return value if isinstance(value, dict) else {}


def _types(contact):
    value = contact.get("type")
    if isinstance(value, list):
        return [_text(item, 40) for item in value[:10]]
    return [_text(value, 40)] if value else []


def _safe_technical_data(contact):
    address = _address(contact)
    return {
        "id": contact.get("id"),
        "uuid": _text(contact.get("uuid"), 160),
        "type": _types(contact),
        "status": _text(contact.get("status"), 30),
        "term": contact.get("term") if isinstance(contact.get("term"), (str, int, float, bool)) or contact.get("term") is None else None,
        "seller": contact.get("seller") if isinstance(contact.get("seller"), (str, int, float, bool)) or contact.get("seller") is None else None,
        "priceList": contact.get("priceList") if isinstance(contact.get("priceList"), (str, int, float, bool)) or contact.get("priceList") is None else None,
        "address": {key: _text(address.get(key), 120) for key in ("zipCode", "department", "country", "address", "city")},
        "internalContacts_count": len(contact.get("internalContacts") or []) if isinstance(contact.get("internalContacts"), list) else 0,
    }


class AlegraContactImporter:
    resource_type = "contacts"

    def __init__(self, client=None):
        self.client = client

    def sync(self, *, limit=300, actor=None):
        system = get_alegra_system()
        total = created = updated = 0
        errors = []
        try:
            client = self.client or AlegraReadOnlyClient()
            responses = client.paged_get("/contacts", limit=min(limit, 300), params={"mode": "advanced"})
            for response in responses:
                for contact in extract_rows(response.data):
                    result = self._upsert(system, contact)
                    total += 1
                    created += result == "created"
                    updated += result == "updated"
            classification = AlegraContactReconciler().classify(system=system, actor=actor)
            complete = sync_is_complete(responses, limit, total)
            result = SyncAuditLog.RESULT_SUCCESS if complete else SyncAuditLog.RESULT_PARTIAL
            SyncAuditLog.objects.create(system=system, operation="sync_contacts", resource=self.resource_type, actor=actor, result=result, detail=f"Contactos procesados: {total}; nuevos: {created}; actualizados: {updated}.", metadata={"limit": limit, "pages": len(responses), "classification": classification, "complete": complete})
        except AlegraError as exc:
            errors.append(str(exc))
            SyncAuditLog.objects.create(system=system, operation="sync_contacts", resource=self.resource_type, actor=actor, result=SyncAuditLog.RESULT_ERROR, detail=str(exc)[:500], metadata={"limit": limit, "complete": False})
        return {"total": total, "created": created, "updated": updated, "errors": errors, "complete": bool(not errors and 'complete' in locals() and complete), "status": result if not errors else SyncAuditLog.RESULT_ERROR}

    @transaction.atomic
    def _upsert(self, system, contact):
        external_id = _text(contact.get("id"), 120)
        if not external_id:
            return "updated"
        address = _address(contact)
        raw_identification = _text(contact.get("identification"), 80)
        explicit_dv = _text(contact.get("verificationDigit") or contact.get("dv"), 4)
        inferred_dv = raw_identification.rsplit("-", 1)[1].strip() if "-" in raw_identification else ""
        identification_number = raw_identification.rsplit("-", 1)[0].strip() if "-" in raw_identification else raw_identification
        defaults = {
            "external_uuid": _text(contact.get("uuid"), 160),
            "name": _text(contact.get("name"), 180) or f"Contacto Alegra {external_id}",
            "identification": identification_number,
            "identification_type": _text(contact.get("identificationType") or contact.get("identification_type"), 40),
            "verification_digit": explicit_dv or inferred_dv,
            "email": _text(contact.get("email"), 254),
            "phone_primary": _text(contact.get("phonePrimary"), 40),
            "phone_secondary": _text(contact.get("phoneSecondary"), 40),
            "mobile": _text(contact.get("mobile"), 40),
            "address": _text(address.get("address"), 255),
            "city": _text(address.get("city"), 120),
            "department": _text(address.get("department"), 120),
            "country": _text(address.get("country"), 80),
            "postal_code": _text(address.get("zipCode"), 20),
            "external_status": _text(contact.get("status"), 30),
            "external_types": _types(contact),
            "technical_data": _safe_technical_data(contact),
            "fetched_at": timezone.now(),
        }
        staging = AlegraContactStaging.objects.filter(system=system, external_id=external_id).first()
        if staging and staging.classification_locked:
            for field in defaults:
                setattr(staging, field, defaults[field])
            staging.save(update_fields=[*defaults.keys(), "updated_at"])
            return "updated"
        _, created = AlegraContactStaging.objects.update_or_create(system=system, external_id=external_id, defaults=defaults)
        return "created" if created else "updated"

    @transaction.atomic
    def import_contact(self, staging_id, *, actor=None):
        staging = AlegraContactStaging.objects.select_for_update().get(pk=staging_id)
        if staging.classification != AlegraContactStaging.CLASS_NEW or staging.imported_client_id:
            raise ValueError("El contacto no está clasificado como nuevo o ya fue importado.")
        if not normalize_name(staging.name):
            raise ValueError("El contacto no tiene nombre válido.")
        tipo = Cliente.TIPO_EMPRESA if any("company" in normalize_name(item) or "empresa" in normalize_name(item) for item in staging.external_types) else Cliente.TIPO_PERSONA
        identification_type = staging.identification_type.casefold() if staging.identification_type.casefold() in {choice[0] for choice in Cliente.TIPO_IDENTIFICACION_CHOICES} else ""
        client = Cliente.objects.create(tipo_cliente=tipo, nombre=staging.name[:180], razon_social=staging.name[:180] if tipo == Cliente.TIPO_EMPRESA else "", tipo_identificacion=identification_type, identificacion=staging.identification[:60], digito_verificacion=staging.verification_digit, email=staging.email, telefono=staging.phone_primary, telefono_secundario=staging.phone_secondary, celular=staging.mobile, direccion=staging.address, ciudad=staging.city, departamento=staging.department, pais=staging.country, codigo_postal=staging.postal_code, activo=staging.external_status != "inactive")
        ExternalObjectMap.objects.update_or_create(system=staging.system, resource_type=self.resource_type, external_id=staging.external_id, defaults={"content_type": ContentType.objects.get_for_model(client), "object_id": client.pk, "status": ExternalObjectMap.STATUS_ACTIVE, "last_synced_at": timezone.now(), "metadata": {"source": "alegra_contact_staging", "staging_id": staging.pk}})
        staging.imported_client = client
        staging.matched_client = client
        staging.classification = AlegraContactStaging.CLASS_LINKED
        staging.classification_reason = "Cliente local creado desde importación aprobada."
        staging.classification_locked = True
        staging.error_detail = ""
        staging.save(update_fields=["imported_client", "matched_client", "classification", "classification_reason", "classification_locked", "error_detail", "updated_at"])
        SyncAuditLog.objects.create(system=staging.system, operation="import_contact", resource=self.resource_type, external_id=staging.external_id, actor=actor, result=SyncAuditLog.RESULT_SUCCESS, detail=f"Cliente local {client.pk} creado.", metadata={"staging_id": staging.pk})
        return client

    @transaction.atomic
    def link_contact(self, staging_id, client_id, *, actor=None):
        staging = AlegraContactStaging.objects.select_for_update().get(pk=staging_id)
        client = Cliente.objects.get(pk=client_id)
        existing = ExternalObjectMap.objects.filter(system=staging.system, resource_type=self.resource_type, external_id=staging.external_id).first()
        if existing and existing.object_id and existing.object_id != client.pk:
            raise ValueError("El contacto externo ya está vinculado a otro cliente.")
        ExternalObjectMap.objects.update_or_create(system=staging.system, resource_type=self.resource_type, external_id=staging.external_id, defaults={"content_type": ContentType.objects.get_for_model(client), "object_id": client.pk, "status": ExternalObjectMap.STATUS_ACTIVE, "last_synced_at": timezone.now(), "metadata": {"source": "manual_link", "staging_id": staging.pk}})
        staging.matched_client = client
        staging.imported_client = None
        staging.classification = AlegraContactStaging.CLASS_LINKED
        staging.classification_reason = "Vinculación manual confirmada."
        staging.classification_locked = True
        staging.save(update_fields=["matched_client", "imported_client", "classification", "classification_reason", "classification_locked", "updated_at"])
        SyncAuditLog.objects.create(system=staging.system, operation="link_contact", resource=self.resource_type, external_id=staging.external_id, actor=actor, result=SyncAuditLog.RESULT_SUCCESS, detail=f"Vinculado con cliente local {client.pk}.", metadata={"staging_id": staging.pk})
        return client

    @transaction.atomic
    def ignore_contact(self, staging_id, *, actor=None):
        staging = AlegraContactStaging.objects.select_for_update().get(pk=staging_id)
        staging.classification = AlegraContactStaging.CLASS_IGNORED
        staging.classification_reason = "Excluido manualmente por un administrador."
        staging.classification_locked = True
        staging.save(update_fields=["classification", "classification_reason", "classification_locked", "updated_at"])
        SyncAuditLog.objects.create(system=staging.system, operation="ignore_contact", resource=self.resource_type, external_id=staging.external_id, actor=actor, result=SyncAuditLog.RESULT_SUCCESS, detail="Contacto marcado como ignorado.", metadata={"staging_id": staging.pk})


class AlegraContactReconciler:
    resource_type = "contacts"

    def classify(self, *, system=None, staging_ids=None, actor=None):
        qs = AlegraContactStaging.objects.select_related("matched_client", "imported_client")
        if system is not None:
            qs = qs.filter(system=system)
        if staging_ids is not None:
            qs = qs.filter(pk__in=list(staging_ids))
        records = list(qs.order_by("pk"))
        identification_index = self._identification_index()
        counts = {key: 0 for key, _ in AlegraContactStaging.CLASSIFICATIONS}
        for staging in records:
            if staging.classification_locked:
                counts[staging.classification] += 1
                continue
            classification, reason, client = self._classify_one(staging, identification_index)
            staging.classification = classification
            staging.classification_reason = reason[:500]
            staging.matched_client = client
            staging.error_detail = reason[:500] if classification == AlegraContactStaging.CLASS_INCOMPLETE else ""
            staging.save(update_fields=["classification", "classification_reason", "matched_client", "error_detail", "updated_at"])
            counts[classification] += 1
        if records:
            SyncAuditLog.objects.create(system=system or records[0].system, operation="classify_contacts", resource=self.resource_type, actor=actor, result=SyncAuditLog.RESULT_SUCCESS, detail=f"Clasificación de {len(records)} contactos.", metadata={"counts": counts})
        return counts

    @staticmethod
    def _identification_index():
        index = {}
        for client in Cliente.objects.filter(activo=True).only("id", "identificacion").iterator(chunk_size=500):
            normalized = normalize_identity(client.identificacion)
            if normalized:
                index.setdefault(normalized, []).append(client)
        return index

    def _classify_one(self, staging, identification_index=None):
        mapped = ExternalObjectMap.objects.filter(system=staging.system, resource_type=self.resource_type, external_id=staging.external_id, status=ExternalObjectMap.STATUS_ACTIVE).first()
        if mapped and isinstance(mapped.local_object, Cliente):
            return AlegraContactStaging.CLASS_LINKED, "Existe un mapeo externo válido.", mapped.local_object
        if not normalize_name(staging.name):
            return AlegraContactStaging.CLASS_INCOMPLETE, "Falta el nombre del contacto externo.", None
        if not normalize_identity(staging.identification):
            return AlegraContactStaging.CLASS_NEW, "No tiene identificación externa; requiere revisión antes de importar.", None
        candidates = (identification_index or self._identification_index()).get(normalize_identity(staging.identification), [])
        if len(candidates) == 1:
            return AlegraContactStaging.CLASS_PROBABLE, "Coincidencia por identificación normalizada; requiere vinculación manual.", candidates[0]
        if len(candidates) > 1:
            return AlegraContactStaging.CLASS_CONFLICT, "Existen múltiples clientes con la identificación indicada.", None
        return AlegraContactStaging.CLASS_NEW, "No se encontró un cliente local por identificación.", None
