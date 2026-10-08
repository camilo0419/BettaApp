from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand

from tienda.models import Categoria, Cliente, ClientePuntoVenta, Producto
from tienda.services.alegra_client import (
    AlegraConfigurationError,
    AlegraError,
    AlegraReadOnlyClient,
    AlegraHTTPError,
    extract_rows,
)


RESOURCES = {
    "items": ("/items", {"mode": "advanced"}),
    "categorias_alegra": ("/item-categories", {}),
    "listas_precios": ("/price-lists", {}),
    "bodegas": ("/warehouses", {}),
    "impuestos": ("/taxes", {}),
    "clientes_contactos": ("/contacts", {"type": "client", "mode": "advanced"}),
}


class Command(BaseCommand):
    help = "Diagnóstico de integración con Alegra en modo exclusivamente lectura."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=30, help="Máximo total por recurso (1-300).")
        parser.add_argument("--timeout", type=float, default=None, help="Timeout HTTP en segundos.")
        parser.add_argument("--output", default="docs/integraciones/alegra_diagnostico.md")

    def handle(self, *args, **options):
        limit = min(max(options["limit"], 1), 300)
        output = Path(options["output"])
        if not output.is_absolute():
            output = Path.cwd() / output
        output.parent.mkdir(parents=True, exist_ok=True)
        report = self._base_report(limit)
        try:
            client = AlegraReadOnlyClient(timeout=options["timeout"])
        except AlegraConfigurationError as exc:
            report["connection"] = {"status": "not_attempted", "detail": str(exc)}
            self._write_report(output, report)
            self.stdout.write(self.style.WARNING(str(exc)))
            self.stdout.write(f"Informe generado: {output}")
            return

        try:
            company = client.get("/company")
            report["connection"] = {"status": "connected", "http_status": company.status}
            report["resources"]["empresa"] = self._response_info(company.data, company.status, company.url)
        except AlegraError as exc:
            report["connection"] = {"status": "failed", "detail": str(exc)}
            report["resources"]["empresa"] = {"status": self._status(exc), "detail": str(exc)}

        for name, (path, params) in RESOURCES.items():
            try:
                responses = client.paged_get(path, limit=limit, params=params)
                rows = [row for response in responses for row in extract_rows(response.data)]
                report["resources"][name] = {
                    "status": "ok",
                    "http_statuses": [response.status for response in responses],
                    "endpoints": [response.url for response in responses],
                    "count_detected": len(rows),
                    "sample_structure": self._structure(rows[:2]),
                }
                self.stdout.write(f"{name}: {len(rows)} registro(s), HTTP {report['resources'][name]['http_statuses']}")
            except AlegraError as exc:
                report["resources"][name] = {"status": self._status(exc), "detail": str(exc)}
                self.stderr.write(self.style.WARNING(f"{name}: {exc}"))

        report["betta"] = self._betta_snapshot()
        report["compatibility"] = self._compatibility()
        report["references"] = [
            "https://developer.alegra.com/reference/get_company-1",
            "https://developer.alegra.com/reference/get_items",
            "https://developer.alegra.com/reference/get_item-categories",
            "https://developer.alegra.com/reference/get_price-lists",
            "https://developer.alegra.com/reference/get_warehouses",
            "https://developer.alegra.com/reference/get_taxes",
            "https://developer.alegra.com/reference/listcontacts-1",
        ]
        self._write_report(output, report)
        self.stdout.write(self.style.SUCCESS(f"Informe generado: {output}"))

    @staticmethod
    def _status(exc):
        return f"http_{exc.status}" if isinstance(exc, AlegraHTTPError) else "error"

    @staticmethod
    def _base_report(limit):
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "diagnostic_limit": limit, "connection": {}, "resources": {}}

    @staticmethod
    def _response_info(data, status, url):
        return {"status": "ok", "http_status": status, "endpoint": url, "sample_structure": Command._structure([data])}

    @staticmethod
    def _structure(value: Any):
        if isinstance(value, dict):
            return {str(key): Command._structure(item) for key, item in list(value.items())[:40]}
        if isinstance(value, list):
            return [Command._structure(value[0])] if value else []
        if value is None:
            return "null"
        return type(value).__name__

    @staticmethod
    def _betta_snapshot():
        return {
            "models": {
                "Producto": {"count": Producto.objects.count(), "fields": ["nombre", "categoria_id", "descripcion_corta", "descripcion_larga", "activo", "tipo_calculo", "precio_base_m2", "precio_base_unidad"]},
                "Categoria": {"count": Categoria.objects.count(), "fields": ["nombre", "slug", "activa"]},
                "Cliente": {"count": Cliente.objects.count(), "fields": ["tipo_cliente", "nombre", "razon_social", "identificacion", "email", "telefono", "direccion", "ciudad", "activo"]},
                "ClientePuntoVenta": {"count": ClientePuntoVenta.objects.count(), "fields": ["cliente_id", "nombre", "direccion", "ciudad", "contacto", "telefono", "email", "activo"]},
            },
            "missing_domain_models": ["precio/lista de precios", "inventario", "bodega", "impuesto", "identificador externo Alegra"],
        }

    @staticmethod
    def _compatibility():
        return {
            "equivalences": [
                "Alegra item.name/description ↔ Producto.nombre/descripcion_*.",
                "Alegra item.category ↔ Producto.categoria, pero requiere tabla de equivalencias por id externo.",
                "Alegra contact ↔ Cliente; identificación/email son candidatos de conciliación, no suficientes por sí solos.",
                "Alegra contact internalContacts ↔ ClienteContacto y posiblemente ClientePuntoVenta, con revisión semántica.",
                "Alegra item.price ↔ precios Betta; Betta tiene dos precios calculados, no listas de precio normalizadas.",
                "Alegra inventory.warehouse ↔ no hay inventario/bodega equivalente en Betta.",
                "Alegra tax ↔ no hay impuesto equivalente en Betta.",
            ],
            "gaps_and_risks": [
                "Variantes, combos y subitems no tienen representación directa en Producto/ProductoCampo.",
                "Una categoría local y una categoría de ítem de Alegra son dominios distintos; no asumir igualdad por nombre.",
                "Se necesita alegra_id por recurso y marca de origen/última sincronización para evitar duplicados.",
                "Definir sistema maestro por campo, idempotencia y estrategia de conflictos antes de CRUD bidireccional.",
                "Las respuestas avanzadas pueden incluir estructura variable por país, permisos y configuración de la cuenta.",
            ],
            "architecture_proposal": [
                "Servicio aislado AlegraReadOnlyClient → adaptadores por recurso → capa de conciliación → persistencia de mapeos externos.",
                "Cola/outbox e idempotency keys para la futura escritura; no implementar en esta etapa.",
                "Webhook receptor y sincronización incremental con auditoría para el futuro.",
            ],
            "initial_sync_recommendation": "Primero importar en modo staging y generar un reporte de coincidencias por identificación/referencia/nombre; aprobar manualmente el mapeo antes de crear o actualizar datos.",
            "next_steps": [
                "Confirmar recursos/campos reales de la cuenta y reglas de negocio para variantes, bodegas, impuestos y precios.",
                "Diseñar modelos de integración/mapeos externos sin alterar los modelos actuales hasta aprobar el contrato.",
                "Definir permisos, reintentos, rate limit, webhooks y política de conflictos para CRUD.",
            ],
        }

    @staticmethod
    def _write_report(path, report):
        resources = report["resources"]
        lines = [
            "# Diagnóstico inicial de integración con Alegra API",
            "",
            f"Generado: `{report['generated_at']}`. Alcance: solo lectura; límite por recurso: `{report['diagnostic_limit']}`.",
            "",
            "## Resultado de conexión",
            "",
            f"- Estado: `{report['connection'].get('status', 'unknown')}`.",
            "- No se registran credenciales, tokens, encabezados Authorization ni datos personales completos.",
            "",
            "## Endpoints y resultados",
            "",
            "| Recurso | Resultado | HTTP | Registros detectados | Estructura depurada |",
            "|---|---|---:|---:|---|",
        ]
        for name, info in resources.items():
            lines.append(f"| `{name}` | `{info.get('status')}` | `{info.get('http_statuses', info.get('http_status', '—'))}` | `{info.get('count_detected', '—')}` | `{json.dumps(info.get('sample_structure', {}), ensure_ascii=False)[:500]}` |")
        lines += ["", "Los inventarios se diagnostican dentro de `/items` en modo `advanced`, pues la API expone allí la información de inventario por ítem y bodega; no se ejecutó una operación de escritura.", ""]
        lines += ["## BettaApp inspeccionado", "", f"- Modelos y cantidades: `{json.dumps(report.get('betta', {}), ensure_ascii=False)}`", "", "## Compatibilidad, riesgos y propuesta", "", f"- Equivalencias y brechas: `{json.dumps(report['compatibility']['equivalences'], ensure_ascii=False)}`", "- Riesgos: " + " ".join(report["compatibility"]["gaps_and_risks"]), "- Arquitectura propuesta: " + " ".join(report["compatibility"]["architecture_proposal"]), "- Recomendación inicial: " + report["compatibility"]["initial_sync_recommendation"], "", "## Próximos pasos CRUD y webhooks", "", *[f"- {item}" for item in report["compatibility"]["next_steps"]], "", "## Documentación oficial consultada", "", *[f"- {url}" for url in report["references"]], ""]
        path.write_text("\n".join(lines), encoding="utf-8")
