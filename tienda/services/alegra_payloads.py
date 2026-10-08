"""Allow-lists for external payloads kept in local staging.

The API response is used to populate normalized fields, but the complete
response is intentionally not persisted.  These helpers retain only fields
needed for reconciliation, display and deterministic reprocessing.
"""

from __future__ import annotations


def _text(value, limit):
    return str(value or "")[:limit]


def invoice_payload(row, line_items):
    client = row.get("client") or row.get("contact") or {}
    if not isinstance(client, dict):
        client = {}
    return {
        "id": _text(row.get("id"), 120),
        "number": _text(row.get("number") or row.get("invoiceNumber") or row.get("name"), 120),
        "prefix": _text(row.get("prefix") or (row.get("numberTemplate") or {}).get("prefix") if isinstance(row.get("numberTemplate"), dict) else row.get("prefix"), 40),
        "issue_date": _text(row.get("date") or row.get("issueDate"), 30),
        "due_date": _text(row.get("dueDate"), 30),
        "client": {
            "id": _text(client.get("id"), 120),
            "name": _text(client.get("name") or client.get("company"), 240),
            "identification": _text(client.get("identification"), 100),
        },
        "subtotal": row.get("subtotal", row.get("subtotalExcludingTax")),
        "discount": row.get("discount", row.get("discountTotal")),
        "tax": row.get("tax", row.get("taxTotal", row.get("totalTax"))),
        "total": row.get("total", row.get("totalAmount")),
        "currency": row.get("currency", row.get("currencyCode", "")),
        "status": _text(row.get("status"), 30),
        "balance": row.get("balance", row.get("pendingBalance", row.get("amountPending"))),
        "payment_status": _text(row.get("paymentStatus"), 40),
        "reference": _text(row.get("reference") or row.get("observations"), 240),
        "items": line_items[:100],
    }


def payment_payload(row, invoice_external_ids):
    client = row.get("client") or row.get("contact") or {}
    if not isinstance(client, dict):
        client = {}
    currency = row.get("currency")
    if isinstance(currency, dict):
        currency = currency.get("code", "")
    return {
        "id": _text(row.get("id"), 120),
        "date": _text(row.get("date"), 30),
        "client": {
            "id": _text(client.get("id"), 120),
            "name": _text(client.get("name") or client.get("company"), 240),
            "identification": _text(client.get("identification"), 100),
        },
        "amount": row.get("amount", row.get("value")),
        "currency": _text(currency, 12),
        "payment_method": _text(row.get("paymentMethod"), 40),
        "status": _text(row.get("status"), 40),
        "invoice_ids": invoice_external_ids[:100],
        "reference": _text(row.get("number") or row.get("observations") or row.get("reference"), 240),
    }
