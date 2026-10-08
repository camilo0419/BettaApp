"""Consulta y staging local de facturas de venta de Alegra (solo GET)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from tienda.models import AlegraInvoiceStaging, Cliente, ExternalObjectMap, SyncAuditLog
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient, extract_rows
from tienda.services.alegra_import import get_alegra_system
from tienda.services.alegra_payloads import invoice_payload
from tienda.services.sync_freshness import sync_is_complete


def _decimal(value):
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        value = value.get("value", value.get("amount"))
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def normalize_identification(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()


def _nested(data, *keys):
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _client_data(row):
    client = row.get("client") or row.get("contact") or {}
    if not isinstance(client, dict):
        client = {}
    return client


def _amount(row, *keys):
    for key in keys:
        value = row.get(key)
        parsed = _decimal(value)
        if parsed is not None:
            return parsed
    return None


def _invoice_fields(row):
    client = _client_data(row)
    number = row.get("number") or row.get("invoiceNumber") or row.get("name") or ""
    prefix = row.get("prefix") or _nested(row, "numberTemplate", "prefix") or ""
    status = str(row.get("status") or "").lower()
    if status not in {AlegraInvoiceStaging.STATUS_ACTIVE, AlegraInvoiceStaging.STATUS_OPEN, AlegraInvoiceStaging.STATUS_CLOSED, AlegraInvoiceStaging.STATUS_VOID, AlegraInvoiceStaging.STATUS_DRAFT}:
        status = AlegraInvoiceStaging.STATUS_UNKNOWN
    return {
        "number": str(number)[:120],
        "prefix": str(prefix)[:40],
        "issue_date": _date(row.get("date") or row.get("issueDate")),
        "due_date": _date(row.get("dueDate")),
        "client_external_id": str(client.get("id") or "")[:120],
        "client_name": str(client.get("name") or client.get("company") or "")[:240],
        "client_identification": str(client.get("identification") or "")[:100],
        "subtotal": _amount(row, "subtotal", "subtotalExcludingTax"),
        "discount": _amount(row, "discount", "discountTotal"),
        "tax": _amount(row, "tax", "taxTotal", "totalTax"),
        "total": _amount(row, "total", "totalAmount"),
        "currency": str(row.get("currency") or row.get("currencyCode") or "")[:12],
        "external_status": status,
        "balance": _amount(row, "balance", "pendingBalance", "amountPending"),
        "payment_status": str(row.get("paymentStatus") or row.get("status") or "")[:40],
        "reference": str(row.get("reference") or row.get("observations") or "")[:240],
    }


def _hash_payload(row):
    encoded = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _line_items(row):
    items = row.get("items") or row.get("itemsList") or []
    if not isinstance(items, list):
        return []
    normalized = []
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "id": str(item.get("id") or "")[:120],
            "name": str(item.get("name") or item.get("description") or "")[:240],
            "reference": str(item.get("reference") or "")[:120],
            "quantity": item.get("quantity"),
            "price": item.get("price") or item.get("unitPrice"),
            "discount": item.get("discount"),
            "tax": item.get("tax"),
            "total": item.get("total"),
        })
    return normalized


class AlegraInvoiceImporter:
    resource_type = "invoices"

    def __init__(self, client=None):
        self.client = client

    def sync(self, *, limit=300, actor=None, params=None):
        system = get_alegra_system()
        total = created = updated = changed = 0
        errors = []
        try:
            client = self.client or AlegraReadOnlyClient()
            responses = client.paged_get("/invoices", limit=limit, params=params or {"order_field": "id", "order_direction": "ASC"})
            client_index = self._client_index(system)
            for response in responses:
                for row in extract_rows(response.data):
                    external_id = str(row.get("id") or "").strip()
                    if not external_id:
                        continue
                    defaults = _invoice_fields(row)
                    line_items = _line_items(row)
                    defaults.update({"line_items": line_items, "original_data": invoice_payload(row, line_items), "data_hash": _hash_payload(row), "fetched_at": timezone.now(), "error_detail": ""})
                    with transaction.atomic():
                        staging, was_created = AlegraInvoiceStaging.objects.select_for_update().get_or_create(
                            system=system, external_id=external_id, defaults=defaults
                        )
                        if not was_created:
                            changed += staging.data_hash != defaults["data_hash"]
                            for field, value in defaults.items():
                                setattr(staging, field, value)
                            staging.save()
                        else:
                            created += 1
                        self._match_client(staging, client_index)
                    updated += not was_created
                    total += 1
            complete = sync_is_complete(responses, limit, total)
            result = SyncAuditLog.RESULT_SUCCESS if complete else SyncAuditLog.RESULT_PARTIAL
            SyncAuditLog.objects.create(system=system, operation="sync_invoices", resource=self.resource_type, actor=actor, result=result, detail=f"Facturas procesadas: {total}; nuevas: {created}; actualizadas: {updated}.", metadata={"limit": limit, "pages": len(responses), "changed": changed, "complete": complete})
            return {"total": total, "created": created, "updated": updated, "changed": changed, "errors": errors, "pages": len(responses), "complete": complete, "status": result}
        except AlegraError as exc:
            errors.append(str(exc))
            SyncAuditLog.objects.create(system=system, operation="sync_invoices", resource=self.resource_type, actor=actor, result=SyncAuditLog.RESULT_ERROR, detail=str(exc)[:500], metadata={"limit": limit, "complete": False})
            return {"total": total, "created": created, "updated": updated, "changed": changed, "errors": errors, "pages": 0, "complete": False, "status": SyncAuditLog.RESULT_ERROR}

    @staticmethod
    def _client_index(system):
        """Load comparable client identifiers once per synchronization batch."""
        index = {}
        for client_id, identification in Cliente.objects.filter(activo=True).values_list("id", "identificacion"):
            normalized = normalize_identification(identification)
            if normalized:
                index.setdefault(normalized, []).append(client_id)
        return index

    def _match_client(self, staging, client_index=None):
        if not staging.client_external_id and not staging.client_identification:
            return
        mapped = ExternalObjectMap.objects.filter(system=staging.system, resource_type="contacts", external_id=staging.client_external_id, status=ExternalObjectMap.STATUS_ACTIVE).first()
        if mapped and isinstance(mapped.local_object, Cliente):
            staging.matched_client = mapped.local_object
            staging.reconciliation_status = AlegraInvoiceStaging.CLASS_MATCHED
            staging.save(update_fields=["matched_client", "reconciliation_status", "last_synced_at"])
            return
        identification = normalize_identification(staging.client_identification)
        if identification:
            candidate_ids = (client_index or self._client_index(staging.system)).get(identification, [])
            if len(candidate_ids) == 1:
                staging.matched_client_id = candidate_ids[0]
                staging.reconciliation_status = AlegraInvoiceStaging.CLASS_MATCHED
                staging.save(update_fields=["matched_client", "reconciliation_status", "last_synced_at"])
            elif len(candidate_ids) > 1:
                staging.reconciliation_status = AlegraInvoiceStaging.CLASS_CONFLICT
                staging.save(update_fields=["reconciliation_status", "last_synced_at"])
