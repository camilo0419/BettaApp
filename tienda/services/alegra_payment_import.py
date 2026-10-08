"""Consulta pagos de ingresos de Alegra y los conserva en staging local."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from tienda.models import AlegraPaymentStaging, Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient, extract_rows
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_invoice_import import normalize_identification
from tienda.services.alegra_payloads import payment_payload
from tienda.services.sync_freshness import sync_is_complete


def _decimal(value):
    if isinstance(value, dict):
        value = value.get("value", value.get("amount"))
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _client(row):
    value = row.get("client") or row.get("contact") or {}
    return value if isinstance(value, dict) else {}


def _invoice_ids(row):
    values = row.get("invoices") or row.get("invoice") or row.get("documents") or []
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return []
    result = []
    for value in values[:100]:
        if isinstance(value, dict):
            value = value.get("id") or value.get("invoiceId")
        if value not in (None, ""):
            result.append(str(value)[:120])
    return result


def _fields(row):
    client = _client(row)
    return {
        "payment_date": _date(row.get("date")),
        "client_external_id": str(client.get("id") or "")[:120],
        "client_name": str(client.get("name") or client.get("company") or "")[:240],
        "amount": _decimal(row.get("amount") or row.get("value")),
        "currency": str((row.get("currency") or {}).get("code", "") if isinstance(row.get("currency"), dict) else row.get("currency") or "")[:12],
        "payment_method": str(row.get("paymentMethod") or "")[:40],
        "external_status": str(row.get("status") or "")[:40],
        "invoice_external_ids": _invoice_ids(row),
        "reference": str(row.get("number") or row.get("observations") or row.get("reference") or "")[:240],
    }


def _hash(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


class AlegraPaymentImporter:
    resource_type = "payments"

    def __init__(self, client=None):
        self.client = client

    def sync(self, *, limit=300, actor=None, params=None):
        system = get_alegra_system()
        total = created = updated = changed = 0
        errors = []
        try:
            client = self.client or AlegraReadOnlyClient()
            query = {"type": "in", "order_field": "id", "order_direction": "ASC"}
            query.update(params or {})
            responses = client.paged_get("/payments", limit=limit, params=query)
            client_index = self._client_index(system)
            for row in (item for response in responses for item in extract_rows(response.data)):
                external_id = str(row.get("id") or "").strip()
                if not external_id:
                    continue
                defaults = _fields(row)
                defaults.update({"original_data": payment_payload(row, defaults["invoice_external_ids"]), "data_hash": _hash(row), "fetched_at": timezone.now(), "error_detail": ""})
                with transaction.atomic():
                    payment, was_created = AlegraPaymentStaging.objects.select_for_update().get_or_create(system=system, external_id=external_id, defaults=defaults)
                    if not was_created:
                        changed += payment.data_hash != defaults["data_hash"]
                        for field, value in defaults.items():
                            setattr(payment, field, value)
                        payment.save()
                    else:
                        created += 1
                    self._match_client(payment, client_index)
                updated += not was_created
                total += 1
            complete = sync_is_complete(responses, limit, total)
            result = SyncAuditLog.RESULT_SUCCESS if complete else SyncAuditLog.RESULT_PARTIAL
            SyncAuditLog.objects.create(system=system, operation="sync_payments", resource=self.resource_type, actor=actor, result=result, detail=f"Pagos de ingreso procesados: {total}; nuevos: {created}; actualizados: {updated}.", metadata={"limit": limit, "pages": len(responses), "changed": changed, "type": "in", "complete": complete})
            return {"total": total, "created": created, "updated": updated, "changed": changed, "errors": errors, "pages": len(responses), "complete": complete, "status": result}
        except AlegraError as exc:
            errors.append(str(exc))
            SyncAuditLog.objects.create(system=system, operation="sync_payments", resource=self.resource_type, actor=actor, result=SyncAuditLog.RESULT_ERROR, detail=str(exc)[:500], metadata={"limit": limit, "type": "in", "complete": False})
            return {"total": total, "created": created, "updated": updated, "changed": changed, "errors": errors, "pages": 0, "complete": False, "status": SyncAuditLog.RESULT_ERROR}

    @staticmethod
    def _client_index(system):
        index = {}
        for client_id, identification in Cliente.objects.filter(activo=True).values_list("id", "identificacion"):
            normalized = normalize_identification(identification)
            if normalized:
                index.setdefault(normalized, []).append(client_id)
        return index

    def _match_client(self, payment, client_index=None):
        mapped = ExternalObjectMap.objects.filter(system=payment.system, resource_type="contacts", external_id=payment.client_external_id, status=ExternalObjectMap.STATUS_ACTIVE).first()
        if mapped and isinstance(mapped.local_object, Cliente):
            payment.matched_client = mapped.local_object
            payment.reconciliation_status = AlegraPaymentStaging.CLASS_MATCHED
            payment.save(update_fields=["matched_client", "reconciliation_status", "last_synced_at"])
            return
        identification = normalize_identification(payment.original_data.get("client", {}).get("identification", "") if isinstance(payment.original_data.get("client"), dict) else "")
        if identification:
            candidate_ids = (client_index or self._client_index(payment.system)).get(identification, [])
            if len(candidate_ids) == 1:
                payment.matched_client_id = candidate_ids[0]
                payment.reconciliation_status = AlegraPaymentStaging.CLASS_MATCHED
                payment.save(update_fields=["matched_client", "reconciliation_status", "last_synced_at"])
            elif len(candidate_ids) > 1:
                payment.reconciliation_status = AlegraPaymentStaging.CLASS_CONFLICT
                payment.save(update_fields=["reconciliation_status", "last_synced_at"])
