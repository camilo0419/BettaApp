"""Reglas centrales de cartera y facturación basadas en datos de Alegra."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone

from tienda.models import AlegraInvoiceStaging, CompromisoPago

STATUS_PENDING = "pendiente"
STATUS_OVERDUE = "vencida"
STATUS_PAID = "pagada"
STATUS_PARTIAL = "parcialmente_pagada"
STATUS_INSUFFICIENT = "sin_informacion"
STATUS_VOID = "anulada"
AGING_BUCKETS = ("no_vencida", "1_30", "31_60", "61_90", "mas_90", "sin_informacion")

FINANCIAL_ELIGIBLE_STATUSES = frozenset({AlegraInvoiceStaging.STATUS_ACTIVE, AlegraInvoiceStaging.STATUS_OPEN, AlegraInvoiceStaging.STATUS_CLOSED})
EXCLUSION_REASONS = {
    AlegraInvoiceStaging.STATUS_VOID: "Documento anulado",
    AlegraInvoiceStaging.STATUS_DRAFT: "Documento en borrador",
    AlegraInvoiceStaging.STATUS_UNKNOWN: "Estado externo desconocido",
}


def invoice_eligibility(invoice):
    """Política común de inclusión, sin alterar el staging."""
    status = invoice.external_status
    status_eligible = status in FINANCIAL_ELIGIBLE_STATUSES
    status_reason = EXCLUSION_REASONS.get(status, "Estado externo no verificable")
    return {
        "status": status,
        "status_eligible": status_eligible,
        "billing_eligible": status_eligible and invoice.total is not None,
        "billing_reason": None if status_eligible and invoice.total is not None else (status_reason if not status_eligible else "Total ausente"),
        "reason": None if status_eligible else status_reason,
    }


def _days_overdue(due_date, cutoff):
    return max((cutoff - due_date).days, 0) if due_date else None


def _aging(days, balance_known=True, due_date_known=True):
    if not balance_known or not due_date_known:
        return "sin_informacion"
    if not days:
        return "no_vencida"
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "mas_90"


def invoice_cartera_row(invoice, cutoff=None):
    """Clasifica independientemente pago, vencimiento y saldo pendiente."""
    cutoff = cutoff or timezone.localdate()
    balance = invoice.balance
    total = invoice.total
    eligibility = invoice_eligibility(invoice)
    base = {
        "invoice": invoice,
        "included": eligibility["status_eligible"],
        "status": STATUS_INSUFFICIENT,
        "payment_status": STATUS_INSUFFICIENT,
        "due_status": STATUS_INSUFFICIENT,
        "balance_known": balance is not None,
        "balance": balance if balance is not None else Decimal("0"),
        "paid": None,
        "days_overdue": None,
        "aging": "sin_informacion",
        "is_overdue": False,
        "exclusion_reason": eligibility["reason"],
        "cutoff": cutoff,
    }
    if not eligibility["status_eligible"]:
        base["status"] = STATUS_VOID if invoice.external_status == AlegraInvoiceStaging.STATUS_VOID else STATUS_INSUFFICIENT
        return base

    balance_known = balance is not None
    due_date_known = invoice.due_date is not None
    days = _days_overdue(invoice.due_date, cutoff)
    paid = None
    if balance_known and total is not None:
        paid = max(Decimal("0"), total - balance)
    base.update({"paid": paid, "days_overdue": days, "balance_known": balance_known})
    if not balance_known:
        return base
    if balance == 0:
        payment_status = STATUS_PAID
        due_status = "no_vencida"
        status = STATUS_PAID
    elif balance > 0:
        payment_status = STATUS_PARTIAL if total is not None and balance < total else STATUS_PENDING
        is_overdue = due_date_known and invoice.due_date < cutoff
        due_status = STATUS_OVERDUE if is_overdue else (STATUS_PENDING if due_date_known else STATUS_INSUFFICIENT)
        # Keep the legacy aggregate status for a fully pending overdue
        # invoice, while exposing payment and due status independently.
        status = STATUS_OVERDUE if is_overdue and payment_status == STATUS_PENDING else payment_status
        base["is_overdue"] = is_overdue
    else:
        payment_status = STATUS_INSUFFICIENT
        due_status = STATUS_INSUFFICIENT
        status = STATUS_INSUFFICIENT
    base.update({"status": status, "payment_status": payment_status, "due_status": due_status, "aging": _aging(days, balance_known, due_date_known)})
    return base


def cartera_rows(*, cutoff=None, queryset=None):
    queryset = queryset if queryset is not None else AlegraInvoiceStaging.objects.select_related("matched_client")
    return [row for row in (invoice_cartera_row(invoice, cutoff) for invoice in queryset) if row["included"]]


def facturacion_summary(*, queryset=None):
    queryset = queryset if queryset is not None else AlegraInvoiceStaging.objects.all()
    invoices = list(queryset)
    reportable = [invoice for invoice in invoices if invoice_eligibility(invoice)["status_eligible"]]
    included_totals = [invoice for invoice in reportable if invoice.total is not None]
    excluded = []
    for invoice in invoices:
        eligibility = invoice_eligibility(invoice)
        if not eligibility["billing_eligible"]:
            excluded.append({"invoice": invoice, "reason": eligibility["billing_reason"], "reason_code": invoice.external_status})
    return {
        "source_count": len(invoices),
        "reportable_count": len(reportable),
        "reportable_ids": [invoice.id for invoice in reportable],
        "eligible_total": sum((invoice.total for invoice in included_totals), Decimal("0")),
        "excluded_documents": excluded,
    }


def cartera_summary(*, cutoff=None, queryset=None, rows=None):
    queryset = queryset if queryset is not None else AlegraInvoiceStaging.objects.select_related("matched_client")
    invoices = list(queryset)
    rows = rows if rows is not None else [row for row in (invoice_cartera_row(invoice, cutoff) for invoice in invoices) if row["included"]]
    excluded = []
    for invoice in invoices:
        eligibility = invoice_eligibility(invoice)
        if not eligibility["status_eligible"]:
            excluded.append({"invoice": invoice, "reason": eligibility["reason"], "reason_code": invoice.external_status})
    summary = {
        "source_count": len(invoices),
        "known_balance": Decimal("0"),
        "overdue_balance": Decimal("0"),
        "pending_count": 0,
        "overdue_count": 0,
        "clients_with_balance": set(),
        "aging": defaultdict(lambda: Decimal("0")),
        "rows": rows,
        "excluded_documents": excluded,
    }
    for row in rows:
        if not row["balance_known"]:
            continue
        balance = row["balance"]
        summary["known_balance"] += balance
        if balance > 0 and row["invoice"].matched_client_id:
            summary["clients_with_balance"].add(row["invoice"].matched_client_id)
        if row["is_overdue"] and balance > 0:
            summary["overdue_balance"] += balance
            summary["overdue_count"] += 1
        if row["payment_status"] in {STATUS_PENDING, STATUS_PARTIAL} and balance > 0:
            summary["pending_count"] += 1
        summary["aging"][row["aging"]] += balance
    summary["clients_with_balance"] = len(summary["clients_with_balance"])
    effective_cutoff = cutoff or timezone.localdate()
    summary["commitments_due"] = CompromisoPago.objects.filter(estado=CompromisoPago.PENDIENTE, fecha_comprometida__gte=effective_cutoff).count()
    summary["commitments_overdue"] = CompromisoPago.objects.filter(estado=CompromisoPago.PENDIENTE, fecha_comprometida__lt=effective_cutoff).count()
    return summary
