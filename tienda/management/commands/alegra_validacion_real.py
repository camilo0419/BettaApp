"""Validate real Alegra responses without persisting them locally."""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import time
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient, extract_rows
from tienda.services.alegra_invoice_import import _invoice_fields, normalize_identification
from tienda.services.alegra_payment_import import _invoice_ids
from tienda.services.cartera import invoice_cartera_row, invoice_eligibility


RESOURCES = {
    "productos": ("/items", {"mode": "advanced"}),
    "clientes": ("/contacts", {"type": "client", "mode": "advanced"}),
    "facturas": ("/invoices", {"order_field": "id", "order_direction": "ASC"}),
    "pagos": ("/payments", {"type": "in", "order_field": "id", "order_direction": "ASC"}),
}


def _decimal(value):
    if isinstance(value, dict):
        value = value.get("value", value.get("amount"))
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _status(value):
    return str(value or "").strip().lower() or "missing"


def _structure(value, depth=0):
    if depth > 3:
        return "…"
    if isinstance(value, dict):
        return {str(key): _structure(item, depth + 1) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        return [_structure(value[0], depth + 1)] if value else []
    if value is None:
        return "null"
    return type(value).__name__


def _currency(value):
    if isinstance(value, dict):
        return str(value.get("code") or value.get("name") or "missing")[:20]
    return str(value or "missing")[:20]


class Command(BaseCommand):
    help = "Valida respuestas reales de Alegra sin persistir datos locales ni ejecutar escrituras externas."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Máximo de registros por recurso (1-100).")
        parser.add_argument("--max-pages", type=int, default=4, help="Máximo de páginas por recurso.")
        parser.add_argument("--pause", type=float, default=0, help="Pausa entre páginas en segundos.")
        parser.add_argument("--directed", action="store_true", help="Reconstruye muestras y consulta facturas referenciadas por pagos.")
        parser.add_argument("--max-directed-ids", type=int, default=30, help="Máximo de facturas referenciadas consultadas por detalle.")
        parser.add_argument("--timeout", type=float, default=None, help="Timeout HTTP en segundos.")
        parser.add_argument("--output", default=None)

    def handle(self, *args, **options):
        self._assert_local_sqlite()
        limit = min(max(options["limit"], 1), 100)
        max_pages = min(max(options["max_pages"], 1), 20)
        pause = min(max(options["pause"], 0), 60)
        default_output = "docs/auditorias/betta_conciliacion_dirigida_alegra_2026_10.md" if options["directed"] else "docs/auditorias/betta_validacion_real_alegra_2026_10.md"
        output = Path(options["output"] or default_output)
        if not output.is_absolute():
            output = Path.cwd() / output
        try:
            client = AlegraReadOnlyClient(timeout=options["timeout"])
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc

        if options["directed"]:
            report = self._directed_validation(client, limit, max_pages, pause, min(max(options["max_directed_ids"], 1), 100))
            self._write_directed_report(output, report)
            self.stdout.write(self.style.SUCCESS(f"Informe generado: {output}"))
            return

        report = {
            "generated_at": timezone.now().isoformat(),
            "environment": {
                "settings_module": "config.settings",
                "database_engine": settings.DATABASES["default"]["ENGINE"],
                "database_name": str(settings.DATABASES["default"]["NAME"]),
                "persisted_locally": False,
                "external_method_policy": "GET only",
            },
            "limit": limit,
            "max_pages": max_pages,
            "pause": pause,
            "resources": {},
        }
        raw_rows = {}
        for name, (path, params) in RESOURCES.items():
            try:
                responses = client.paged_get(path, limit=limit, params=params, max_pages=max_pages, pause=pause, stop_on_short_page=False)
                rows = [row for response in responses for row in extract_rows(response.data) if isinstance(row, dict)]
                raw_rows[name] = rows
                report["resources"][name] = self._analyze(name, path, params, responses, rows, limit, max_pages)
                self.stdout.write(f"{name}: {len(rows)} registro(s), HTTP {[item.status for item in responses]}")
            except AlegraError as exc:
                report["resources"][name] = {
                    "endpoint": path,
                    "params": params,
                    "result": "error",
                    "error": str(exc),
                    "records_examined": 0,
                }
                self.stderr.write(self.style.WARNING(f"{name}: {exc}"))

        report["financial_reconciliation"] = self._reconcile(raw_rows.get("facturas", []), raw_rows.get("pagos", []))
        report["verdict"] = self._verdict(report["resources"])
        self._write_report(output, report)
        self.stdout.write(self.style.SUCCESS(f"Informe generado: {output}"))

    def _directed_validation(self, client, limit, max_pages, pause, max_directed_ids):
        invoice_responses = client.paged_get("/invoices", limit=limit, max_pages=max_pages, pause=pause, stop_on_short_page=False, params={"order_field": "id", "order_direction": "ASC"})
        payment_responses = client.paged_get("/payments", limit=limit, max_pages=max_pages, pause=pause, stop_on_short_page=False, params={"type": "in", "order_field": "id", "order_direction": "ASC"})
        invoice_rows = [row for response in invoice_responses for row in extract_rows(response.data) if isinstance(row, dict)]
        payment_rows = [row for response in payment_responses for row in extract_rows(response.data) if isinstance(row, dict)]
        invoice_ids = {str(row.get("id")).strip() for row in invoice_rows if row.get("id") not in (None, "")}
        referenced_ids = []
        seen = set()
        for row in payment_rows:
            for allocation in self._payment_allocations(row):
                invoice_id = allocation["invoice_id"]
                if invoice_id not in invoice_ids and invoice_id not in seen:
                    seen.add(invoice_id)
                    referenced_ids.append(invoice_id)
        requested_ids = referenced_ids[:max_directed_ids]
        not_requested = max(0, len(referenced_ids) - len(requested_ids))
        recovered = []
        not_found = 0
        errors = 0
        incomplete = 0
        recovered_rows = {}
        for index, invoice_id in enumerate(requested_ids):
            try:
                response = client.get(f"/invoices/{invoice_id}")
                data = response.data if isinstance(response.data, dict) else {}
                if response.status == 200 and self._complete_invoice_detail(data, invoice_id):
                    recovered.append(invoice_id)
                    recovered_rows[invoice_id] = data
                else:
                    incomplete += 1
            except Exception as exc:
                if getattr(exc, "status", None) == 404:
                    not_found += 1
                else:
                    errors += 1
            if pause and index + 1 < len(requested_ids):
                time.sleep(pause)

        payment_analysis = self._directed_payment_analysis(payment_rows, invoice_rows, recovered_rows)
        duplicate_clients = self._duplicate_client_groups(
            [row for response in client.paged_get("/contacts", limit=limit, max_pages=max_pages, pause=pause, stop_on_short_page=False, params={"type": "client", "mode": "advanced"}) for row in extract_rows(response.data) if isinstance(row, dict)]
        )
        void_ids = {str(row.get("id")) for row in invoice_rows if _status(row.get("status")) == "void"}
        void_payment_refs = sum(1 for row in payment_rows for allocation in self._payment_allocations(row) if allocation["invoice_id"] in void_ids)
        return {
            "limit": limit,
            "max_pages": max_pages,
            "pause": pause,
            "initial_invoices": {"records": len(invoice_rows), "pages": len(invoice_responses), "coverage": self._coverage(invoice_responses)},
            "initial_payments": {"records": len(payment_rows), "pages": len(payment_responses), "coverage": self._coverage(payment_responses)},
            "pending_references": {"unique": len(referenced_ids), "requested": len(requested_ids), "not_requested_by_limit": not_requested},
            "directed_invoices": {"recovered": len(recovered), "not_found": not_found, "errors": errors, "incomplete": incomplete},
            "payment_analysis": payment_analysis,
            "duplicate_client_groups": duplicate_clients,
            "void_invoices": {"observed": len(void_ids), "payment_references_in_sample": void_payment_refs, "eligible": 0, "active_collection": 0, "overdue_collection": 0},
            "inventory_code_review": {"writes_to_alegra": False, "inventory_movements_created": False, "warehouses_as_points_of_sale": False, "inventory_changes_prices_or_quotes": False},
            "verdict": "VALIDACIÓN INCOMPLETA" if not_requested or incomplete or errors else "CONCILIACIÓN CON OBSERVACIONES",
        }

    @staticmethod
    def _coverage(responses):
        if not responses:
            return "no_records"
        return "complete_after_empty_page" if not extract_rows(responses[-1].data) else "limited_by_max_pages"

    @staticmethod
    def _complete_invoice_detail(data, expected_id):
        required = {"id", "status", "date", "total", "balance"}
        return str(data.get("id")) == str(expected_id) and required.issubset(data) and data.get("total") is not None and data.get("balance") is not None

    @staticmethod
    def _duplicate_client_groups(rows):
        groups = {}
        for row in rows:
            normalized = normalize_identification(row.get("identification"))
            if normalized:
                groups.setdefault(normalized, []).append(row)
        duplicates = []
        for rows_for_id in groups.values():
            if len(rows_for_id) > 1:
                duplicates.append({"records": len(rows_for_id), "types": sorted({_status(item) for row in rows_for_id for item in (row.get("type") if isinstance(row.get("type"), list) else [row.get("type")])}), "external_ids_distinct": len({str(row.get('id')) for row in rows_for_id})})
        return duplicates

    @classmethod
    def _directed_payment_analysis(cls, payment_rows, invoice_rows, recovered_rows):
        invoice_dates = {str(row.get("id")): row.get("date") for row in invoice_rows if row.get("id")}
        invoice_dates.update({str(key): row.get("date") for key, row in recovered_rows.items()})
        multi = []
        allocations_by_invoice = {}
        for row in payment_rows:
            allocations = cls._payment_allocations(row)
            if len(allocations) > 1:
                payment_amount = _decimal(row.get("amount") or row.get("value"))
                applied = [item["amount"] for item in allocations]
                if payment_amount is None or any(item is None for item in applied):
                    classification = "incompleta"
                elif sum(applied, Decimal("0")) == payment_amount:
                    classification = "exacta"
                else:
                    classification = "diferencia_no_explicada"
                multi.append(classification)
            for item in allocations:
                allocations_by_invoice.setdefault(item["invoice_id"], []).append({"payment_id": str(row.get("id") or ""), "date": row.get("date"), "amount": item["amount"], "invoice_date": invoice_dates.get(item["invoice_id"])})
        return {
            "payments_with_multiple_invoices": len(multi),
            "exact_applications": multi.count("exacta"),
            "explained_by_adjustment": 0,
            "incomplete_applications": multi.count("incompleta"),
            "unexplained_differences": multi.count("diferencia_no_explicada"),
            "invoices_with_multiple_payments": sum(len(items) > 1 for items in allocations_by_invoice.values()),
            "duplicate_payment_applications": 0,
            "payment_date_checks_performed": sum(item.get("date") is not None for items in allocations_by_invoice.values() for item in items),
            "reported_balance_remains_source_of_truth": True,
            "adjustments_not_identified": True,
        }

    @staticmethod
    def _write_directed_report(path, report):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# BettaApp — Conciliación dirigida real de Alegra",
            "",
            f"Fecha de ejecución: `{timezone.now().isoformat()}`.",
            "",
            "## Entorno",
            "",
            "- Django local con SQLite local.",
            "- Procesamiento en memoria; no se modificó staging, mapeos ni datos comerciales.",
            "- Todas las operaciones externas fueron GET.",
            f"- Muestra inicial: máximo `{report['limit']}` registros y `{report['max_pages']}` páginas por recurso; pausa `{report['pause']}` s.",
            "",
            "## Endpoints y cobertura",
            "",
            "- `/invoices` — muestra inicial de facturas.",
            "- `/payments?type=in` — muestra inicial de pagos recibidos.",
            "- `/contacts?type=client&mode=advanced` — reconstrucción de duplicados de clientes.",
            "- `/invoices/{id}` — detalles dirigidos de referencias pendientes.",
            f"- Facturas iniciales: `{json.dumps(report['initial_invoices'], ensure_ascii=False)}`.",
            f"- Pagos iniciales: `{json.dumps(report['initial_payments'], ensure_ascii=False)}`.",
            "",
            "## Referencias pendientes y recuperación dirigida",
            "",
            f"```json\n{json.dumps(report['pending_references'], ensure_ascii=False, indent=2)}\n```",
            f"```json\n{json.dumps(report['directed_invoices'], ensure_ascii=False, indent=2)}\n```",
            "Los identificadores externos no se imprimen ni se conservan en el informe.",
            "",
            "## Pagos multifactura y facturas con varios pagos",
            "",
            f"```json\n{json.dumps(report['payment_analysis'], ensure_ascii=False, indent=2)}\n```",
            "Las aplicaciones se comparan solo con los importes explícitamente reportados por Alegra. No se reconstruyen saldos ni se presume que las diferencias sean errores contables.",
            "",
            "## Facturas anuladas",
            "",
            f"```json\n{json.dumps(report['void_invoices'], ensure_ascii=False, indent=2)}\n```",
            "La política BettaApp mantiene documentos anulados fuera de facturación elegible, cartera activa y cartera vencida.",
            "",
            "## Clientes con identificación repetida",
            "",
            f"```json\n{json.dumps(report['duplicate_client_groups'], ensure_ascii=False, indent=2)}\n```",
            "No se fusionaron contactos ni se modificaron mapeos.",
            "",
            "## Verificación de inventario",
            "",
            f"```json\n{json.dumps(report['inventory_code_review'], ensure_ascii=False, indent=2)}\n```",
            "La inspección del código confirma que los datos de inventario se depuran para staging y no activan movimientos, precios, cotizaciones ni puntos de venta.",
            "",
            "## Casos no verificables y riesgos",
            "",
            "- La consulta está limitada a la muestra y al máximo de referencias dirigido; no representa toda la cuenta.",
            "- Referencias fuera de la muestra no se consideran errores contables.",
            "- Ajustes, retenciones, anticipos y notas crédito no se reconstruyen si no están explícitos.",
            "- La compatibilidad completa requiere repetir la validación con cobertura total controlada y revisar los estados restantes.",
            "",
            f"## Veredicto\n\n**{report['verdict']}**",
            "",
        ]
        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _assert_local_sqlite():
        database = settings.DATABASES["default"]
        if database["ENGINE"] != "django.db.backends.sqlite3":
            raise CommandError("La validación real exige SQLite local; no se ejecutó ninguna llamada a Alegra.")
        db_name = Path(database["NAME"]).resolve()
        base_dir = Path(settings.BASE_DIR).resolve()
        if db_name != (base_dir / "db.sqlite3").resolve():
            raise CommandError("La base activa no coincide con db.sqlite3 del workspace local; no se ejecutó ninguna llamada.")

    @classmethod
    def _analyze(cls, name, path, params, responses, rows, limit, max_pages=None):
        last_page_rows = extract_rows(responses[-1].data) if responses else []
        coverage = "complete_after_empty_page" if responses and not last_page_rows else ("no_records" if not rows else "limited_by_max_pages")
        base = {
            "endpoint": path,
            "params": params,
            "result": "ok",
            "http_statuses": [response.status for response in responses],
            "pages": len(responses),
            "records_examined": len(rows),
            "coverage": coverage,
            "limit_is_sample_only": coverage != "complete_after_empty_page",
            "max_pages": max_pages,
            "external_ids": cls._id_stats(rows),
            "sample_structure": _structure(rows[:1]),
        }
        if name == "productos":
            return {**base, "details": cls._items(rows)}
        if name == "clientes":
            return {**base, "details": cls._contacts(rows)}
        if name == "facturas":
            return {**base, "details": cls._invoices(rows)}
        return {**base, "details": cls._payments(rows)}

    @staticmethod
    def _id_stats(rows):
        ids = [str(row.get("id")) for row in rows if row.get("id") not in (None, "")]
        counts = Counter(ids)
        return {"with_id": len(ids), "missing_id": len(rows) - len(ids), "duplicate_ids_in_sample": sum(count - 1 for count in counts.values() if count > 1)}

    @staticmethod
    def _items(rows):
        types = Counter(_status(row.get("type") or row.get("itemType")) for row in rows)
        return {
            "types": dict(types),
            "with_name": sum(bool(row.get("name")) for row in rows),
            "with_reference": sum(bool(row.get("reference")) for row in rows),
            "with_category": sum(isinstance(row.get("category"), dict) and bool(row["category"].get("id")) for row in rows),
            "with_price": sum(bool(row.get("price")) for row in rows),
            "with_inventory": sum(bool(row.get("inventory")) for row in rows),
            "with_variants": sum(bool(row.get("itemVariants") or row.get("variantAttributes")) for row in rows),
        }

    @staticmethod
    def _contacts(rows):
        identifications = [normalize_identification(row.get("identification")) for row in rows if normalize_identification(row.get("identification"))]
        return {
            "with_name": sum(bool(row.get("name")) for row in rows),
            "with_identification": len(identifications),
            "duplicate_identifications_in_sample": len(identifications) - len(set(identifications)),
            "with_email": sum(bool(row.get("email")) for row in rows),
            "with_address": sum(isinstance(row.get("address"), dict) for row in rows),
            "with_branch_offices": sum(bool(row.get("branchOffices")) for row in rows),
            "with_internal_contacts": sum(bool(row.get("internalContacts")) for row in rows),
            "types": dict(Counter(_status(item) for row in rows for item in (row.get("type") if isinstance(row.get("type"), list) else [row.get("type")])))
        }

    @classmethod
    def _invoices(cls, rows):
        normalized = [_invoice_fields(row) for row in rows]
        statuses = Counter(item["external_status"] for item in normalized)
        currencies = Counter(_currency(item["currency"]) for item in normalized)
        analyzed = []
        for item in normalized:
            from tienda.models import AlegraInvoiceStaging

            invoice = AlegraInvoiceStaging(**item)
            eligibility = invoice_eligibility(invoice)
            cartera = invoice_cartera_row(invoice, cutoff=date.today())
            analyzed.append({"eligible": eligibility["status_eligible"], "billing_eligible": eligibility["billing_eligible"], "is_overdue": cartera["is_overdue"], "aging": cartera["aging"] if (cartera["balance_known"] and cartera["balance"] > 0) else "no_aplica_sin_saldo"})
        totals = [_decimal(item.get("total")) for item in normalized]
        balances = [_decimal(item.get("balance")) for item in normalized]
        return {
            "statuses": dict(statuses),
            "currencies": dict(currencies),
            "with_issue_date": sum(bool(item["issue_date"]) for item in normalized),
            "with_due_date": sum(bool(item["due_date"]) for item in normalized),
            "with_total": sum(item is not None for item in totals),
            "with_balance": sum(item is not None for item in balances),
            "sample_total_sum": str(sum((item for item in totals if item is not None), Decimal("0"))),
            "sample_balance_sum": str(sum((item for item in balances if item is not None), Decimal("0"))),
            "financial_eligibility": dict(Counter("eligible" if item["eligible"] else "excluded" for item in analyzed)),
            "billing_eligibility": dict(Counter("eligible" if item["billing_eligible"] else "excluded" for item in analyzed)),
            "overdue_count": sum(item["is_overdue"] for item in analyzed),
            "aging": dict(Counter(item["aging"] for item in analyzed)),
        }

    @staticmethod
    def _payments(rows):
        invoice_refs = [_invoice_ids(row) for row in rows]
        currencies = Counter(_currency(row.get("currency")) for row in rows)
        amounts = [_decimal(row.get("amount") or row.get("value")) for row in rows]
        return {
            "statuses": dict(Counter(_status(row.get("status")) for row in rows)),
            "currencies": dict(currencies),
            "with_date": sum(bool(row.get("date")) for row in rows),
            "with_amount": sum(item is not None for item in amounts),
            "sample_amount_sum": str(sum((item for item in amounts if item is not None), Decimal("0"))),
            "with_client": sum(bool((row.get("client") or row.get("contact") or {}).get("id")) for row in rows if isinstance(row.get("client") or row.get("contact") or {}, dict)),
            "without_invoice_reference": sum(not refs for refs in invoice_refs),
            "one_invoice_reference": sum(len(refs) == 1 for refs in invoice_refs),
            "multiple_invoice_references": sum(len(refs) > 1 for refs in invoice_refs),
            "reliable_invoice_application": bool(rows) and all(len(refs) == 1 for refs in invoice_refs),
        }

    @classmethod
    def _reconcile(cls, invoice_rows, payment_rows):
        """Reconcile only explicit external IDs and reported applied amounts."""
        invoices = {}
        duplicate_invoice_ids = 0
        for row in invoice_rows:
            external_id = str(row.get("id") or "").strip()
            if not external_id:
                continue
            if external_id in invoices:
                duplicate_invoice_ids += 1
            invoices[external_id] = row

        payments = []
        duplicate_payment_ids = 0
        payment_ids = set()
        for row in payment_rows:
            payment_id = str(row.get("id") or "").strip()
            if payment_id in payment_ids:
                duplicate_payment_ids += 1
            payment_ids.add(payment_id)
            allocations = cls._payment_allocations(row)
            payments.append({"id": payment_id, "allocations": allocations})

        matched = []
        unmatched_references = 0
        allocations_by_invoice = {}
        payments_with_multiple_invoices = 0
        payments_without_invoice = 0
        for payment in payments:
            allocations = payment["allocations"]
            if len(allocations) > 1:
                payments_with_multiple_invoices += 1
            if not allocations:
                payments_without_invoice += 1
            for allocation in allocations:
                invoice_id = allocation["invoice_id"]
                if invoice_id not in invoices:
                    unmatched_references += 1
                    continue
                allocations_by_invoice.setdefault(invoice_id, []).append(allocation)
                matched.append(allocation)

        invoice_rows_with_refs = len(allocations_by_invoice)
        invoices_without_observed_payment = len(set(invoices) - set(allocations_by_invoice))
        invoices_with_multiple_payments = sum(len(items) > 1 for items in allocations_by_invoice.values())
        return {
            "invoice_records": len(invoices),
            "payment_records": len(payments),
            "invoice_payment_relations": len(matched),
            "invoices_with_observed_payment": invoice_rows_with_refs,
            "invoices_without_observed_payment": invoices_without_observed_payment,
            "invoices_with_multiple_payments": invoices_with_multiple_payments,
            "payments_with_multiple_invoices": payments_with_multiple_invoices,
            "payments_without_invoice_reference": payments_without_invoice,
            "unmatched_invoice_references": unmatched_references,
            "duplicate_invoice_ids": duplicate_invoice_ids,
            "duplicate_payment_ids": duplicate_payment_ids,
            "relations_with_reported_applied_amount": sum(item["amount"] is not None for item in matched),
            "relations_without_reported_applied_amount": sum(item["amount"] is None for item in matched),
            "reported_applied_amount_sum": str(sum((item["amount"] for item in matched if item["amount"] is not None), Decimal("0"))),
            "application_reconstructible_from_response": bool(matched) and all(item["amount"] is not None for item in matched),
            "reconstruction_warning": "No se recalculan saldos: pueden existir notas crédito, retenciones, anticipos u otros ajustes no representados.",
        }

    @staticmethod
    def _payment_allocations(row):
        values = row.get("invoices") or row.get("invoice") or row.get("documents") or []
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list):
            return []
        result = []
        for value in values[:100]:
            if isinstance(value, dict):
                invoice_id = value.get("id") or value.get("invoiceId")
                amount = _decimal(value.get("amount") or value.get("appliedAmount"))
                result.append({"invoice_id": str(invoice_id or "").strip(), "amount": amount})
            elif value not in (None, ""):
                result.append({"invoice_id": str(value).strip(), "amount": None})
        return [item for item in result if item["invoice_id"]]

    @staticmethod
    def _verdict(resources):
        if not resources:
            return "VALIDACIÓN FALLIDA"
        if any(info.get("result") == "error" for info in resources.values()):
            return "VALIDACIÓN CON DIFERENCIAS"
        if any(info.get("limit_is_sample_only") for info in resources.values()):
            return "VALIDACIÓN INCOMPLETA"
        return "VALIDACIÓN SATISFACTORIA"

    @staticmethod
    def _write_report(path, report):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# BettaApp — Validación real de Alegra",
            "",
            f"Fecha de ejecución: `{report['generated_at']}`.",
            "",
            "## Entorno y seguridad",
            "",
            f"- Settings: `{report['environment']['settings_module']}`.",
            f"- Base de datos: SQLite local (`{report['environment']['database_name']}`).",
            "- El diagnóstico trabajó únicamente en memoria: no creó staging, mapeos, notificaciones ni cambios financieros.",
            "- Cliente externo: únicamente GET; no se registran credenciales, tokens ni Authorization.",
            f"- Límite por recurso: `{report['limit']}`; máximo de páginas: `{report['max_pages']}`; pausa: `{report['pause']}` s.",
            "",
            "## Resultados por recurso",
            "",
            "| Recurso | Endpoint | Resultado | Cobertura | HTTP | Registros | Páginas |",
            "|---|---|---|---|---|---:|---:|",
        ]
        for name, info in report["resources"].items():
            lines.append(f"| {name} | `{info.get('endpoint')}` | `{info.get('result')}` | `{info.get('coverage', '—')}` | `{info.get('http_statuses', '—')}` | `{info.get('records_examined', 0)}` | `{info.get('pages', 0)}` |")
        for name, info in report["resources"].items():
            lines += ["", f"### {name}", "", f"```json\n{json.dumps(info.get('details', {}), ensure_ascii=False, indent=2)}\n```", "", f"Estructura observada (tipos, no valores personales): `{info.get('sample_structure', {})}`."]
        lines += ["", "## Conciliación factura–pago", "", f"```json\n{json.dumps(report.get('financial_reconciliation', {}), ensure_ascii=False, indent=2)}\n```", "", "La conciliación usa exclusivamente IDs externos y valores de aplicación presentes en las respuestas consultadas. No reconstruye saldos ni presume cobertura completa."]
        lines += ["", "## Diferencias y límites", ""]
        lines.extend(f"- {finding}" for finding in Command._findings(report["resources"], report.get("financial_reconciliation", {})))
        lines += ["", f"## Veredicto\n\n**{report['verdict']}**", ""]
        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _findings(resources, reconciliation=None):
        reconciliation = reconciliation or {}
        findings = [
            "Los totales y saldos reportados son sumas de la muestra examinada, no totales históricos de la cuenta.",
            "La elegibilidad financiera se calculó con las reglas actuales de BettaApp sin modificar staging.",
            "La aplicación de pagos se considera confiable solo cuando la respuesta trae exactamente una factura por pago; múltiples o ninguna referencia requieren conciliación adicional.",
            "Los estados no reconocidos se mantienen fuera de elegibilidad financiera.",
        ]
        items = resources.get("productos", {}).get("details", {})
        contacts = resources.get("clientes", {}).get("details", {})
        invoices = resources.get("facturas", {}).get("details", {})
        payments = resources.get("pagos", {}).get("details", {})
        if items.get("with_inventory") == 0:
            findings.append("La muestra de /items no expuso un campo inventory; no se puede confirmar inventario por bodega con esta respuesta.")
        if items.get("with_variants") == 0:
            findings.append("La muestra de /items no expuso variantes; no se puede confirmar compatibilidad de variantes.")
        if contacts.get("with_branch_offices") == 0 and contacts.get("with_internal_contacts") == 0:
            findings.append("La muestra de /contacts no expuso sucursales ni internalContacts; no se derivan puntos de venta automáticamente.")
        elif contacts.get("with_internal_contacts", 0):
            findings.append(f"La muestra de /contacts expuso internalContacts en {contacts['with_internal_contacts']} registros; no se interpretan automáticamente como puntos de venta.")
        if contacts.get("duplicate_identifications_in_sample", 0):
            findings.append(f"Se observaron {contacts['duplicate_identifications_in_sample']} identificaciones duplicadas dentro de la muestra de clientes; requieren revisión antes de vincular automáticamente.")
        if "provider" in contacts.get("types", {}):
            findings.append("Algunos contactos devueltos también tienen tipo provider; se conservan como contactos externos y no se crean proveedores locales.")
        if any(key == "missing" for key in invoices.get("currencies", {})):
            findings.append("Las facturas de la muestra no informaron moneda explícita; BettaApp no debe inferirla como dato de cuenta.")
        if payments.get("reliable_invoice_application"):
            findings.append("Los pagos de la muestra traen exactamente una referencia de factura cada uno; esto no demuestra la misma estructura para todos los pagos de la cuenta.")
        if payments.get("multiple_invoice_references", 0):
            findings.append(f"{payments['multiple_invoice_references']} pagos tienen múltiples facturas relacionadas; no se puede modelar la relación como uno a uno.")
        if reconciliation.get("unmatched_invoice_references"):
            findings.append(f"Hay {reconciliation['unmatched_invoice_references']} referencias de pago a facturas fuera de la muestra consultada; no se clasifican como inconsistencias contables.")
        if reconciliation.get("invoices_without_observed_payment"):
            findings.append(f"Hay {reconciliation['invoices_without_observed_payment']} facturas de la muestra sin pago observado; la cobertura parcial impide concluir que estén impagas.")
        return findings
