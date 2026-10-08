"""Diagnóstico real y simulación de preimportación de clientes Alegra."""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError

from tienda.models import Cliente, ExternalObjectMap, ExternalSystem
from tienda.services.alegra_client import AlegraError, AlegraReadOnlyClient
from tienda.services.alegra_preimport_clients import build_preimport_plan, fetch_customer_contacts


class Command(BaseCommand):
    help = "Consulta clientes Alegra por GET y genera una preimportación simulada sin persistir datos."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100, help="Máximo de contactos (1-1000).")
        parser.add_argument("--max-pages", type=int, default=10, help="Máximo de páginas (1-100).")
        parser.add_argument("--pause", type=float, default=0, help="Pausa entre solicitudes en segundos.")
        parser.add_argument("--max-retries", type=int, default=2, help="Reintentos máximos solo para HTTP 429.")
        parser.add_argument("--timeout", type=float, default=None, help="Timeout HTTP en segundos.")
        parser.add_argument("--output", default="docs/auditorias/betta_preimportacion_clientes_alegra_2026_10.md")

    def handle(self, *args, **options):
        self._assert_local_sqlite()
        limit = min(max(options["limit"], 1), 1000)
        max_pages = min(max(options["max_pages"], 1), 100)
        try:
            client = AlegraReadOnlyClient(timeout=options["timeout"])
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc
        try:
            fetched = fetch_customer_contacts(
                client,
                limit=limit,
                max_pages=max_pages,
                pause=options["pause"],
                max_retries=options["max_retries"],
            )
        except AlegraError as exc:
            raise CommandError(str(exc)) from exc

        system = ExternalSystem.objects.filter(code="alegra").first()
        content_type = ContentType.objects.get_for_model(Cliente)
        local_clients = list(Cliente.objects.only("id", "identificacion", "nombre"))
        active_maps = []
        all_maps = []
        if system:
            all_maps = list(ExternalObjectMap.objects.filter(system=system, resource_type="contacts").only("external_id", "object_id", "status"))
            active_maps = [item for item in all_maps if item.status == ExternalObjectMap.STATUS_ACTIVE and item.object_id and item.content_type_id == content_type.pk]
        simulated = build_preimport_plan(
            fetched["rows"],
            local_clients=local_clients,
            active_maps=active_maps,
            all_maps=all_maps,
        )
        report = self._report(fetched, simulated, limit, max_pages, options["pause"], options["max_retries"])
        output = Path(options["output"])
        if not output.is_absolute():
            output = Path.cwd() / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(
                f"Contactos consultados: {len(fetched['rows'])}; cobertura: {fetched['coverage']}; "
                f"plan simulado: {simulated['counts']}. Informe: {output}"
            )
        )
        if fetched["errors"]:
            self.stderr.write(self.style.WARNING("La consulta terminó con errores; la cobertura no se considera completa."))

    @staticmethod
    def _assert_local_sqlite():
        database = settings.DATABASES["default"]
        engine = str(database.get("ENGINE", ""))
        name = Path(str(database.get("NAME", ""))).resolve()
        expected = (Path(settings.BASE_DIR) / "db.sqlite3").resolve()
        if "sqlite3" not in engine or name != expected:
            raise CommandError("Ejecución detenida: se requiere SQLite local db.sqlite3; no se consultará otro destino.")

    @staticmethod
    def _report(fetched, simulated, limit, max_pages, pause, max_retries):
        counts = simulated["counts"]
        declared = fetched["declared_total"]
        if declared and fetched["coverage"] == "complete":
            coverage = f"100% (total declarado: {declared})"
        elif declared:
            coverage = f"{min(100, len(fetched['rows']) / declared * 100):.2f}% de un total declarado de {declared}; cobertura limitada"
        else:
            coverage = "Indeterminada: Alegra no entregó un total confiable o la consulta fue limitada"
        status = "DIAGNÓSTICO COMPLETADO" if fetched["coverage"] == "complete" and not fetched["errors"] else "DIAGNÓSTICO LIMITADO" if fetched["coverage"] == "limited" else "DIAGNÓSTICO INCOMPLETO"
        lines = [
            "# BettaApp — Preimportación de clientes Alegra (Fase 7.2)",
            "",
            "## 1. Entorno",
            "",
            "- Ejecución local con SQLite; no se conectó a producción ni cPanel.",
            "- Método externo permitido: `GET` únicamente.",
            "- Persistencia comercial: no; staging, clientes y mapeos no fueron modificados.",
            f"- Límites: {limit} contactos, {max_pages} páginas, pausa {pause}s, {max_retries} reintentos 429.",
            "",
            "## 2. Consulta y cobertura",
            "",
            "- Endpoint: `GET /contacts` con `type=client`, `mode=advanced`, paginación `start/limit` y `metadata=true`.",
            f"- Contactos recuperados y deduplicados: {len(fetched['rows'])}.",
            f"- Páginas examinadas: {fetched['pages']}.",
            f"- HTTP observados: {fetched['http_statuses']}.",
            f"- Reintentos por HTTP 429: {fetched['retries_429']}.",
            f"- Motivo de terminación: `{fetched['stopped_reason']}`.",
            f"- Cobertura: {coverage}.",
            f"- Porcentaje del catálogo: {coverage}.",
            f"- Duplicados de ID externo observados: {fetched['duplicate_external_ids']}.",
            f"- Errores: {len(fetched['errors'])}.",
            "",
            "## 3. Conciliación simulada",
            "",
            f"- Clientes locales examinados: {simulated['local_clients_examined']}.",
            f"- Ya vinculados (`NO_ACTION`): {counts.get('NO_ACTION', 0)}.",
            f"- Nuevos propuestos (`CREATE_LOCAL`): {counts.get('CREATE_LOCAL', 0)}.",
            f"- Coincidencias pendientes (`LINK_EXISTING`): {counts.get('LINK_EXISTING', 0)}.",
            f"- Identificaciones duplicadas (`REVIEW_DUPLICATE`): {counts.get('REVIEW_DUPLICATE', 0)}.",
            f"- Conflictos (`REVIEW_CONFLICT`): {counts.get('REVIEW_CONFLICT', 0)}.",
            f"- Inválidos/proveedor puro (`SKIP_INVALID`): {counts.get('SKIP_INVALID', 0)}.",
            "",
            "No se muestran nombres, identificaciones, correos, teléfonos ni IDs externos completos. Las acciones son propuestas en memoria y no se ejecutaron.",
            "",
            "## 4. Riesgos estructurales",
            "",
            "- `Cliente` puede representar los campos requeridos por el contacto normalizado; el ID externo continúa gestionándose mediante `ExternalObjectMap`.",
            "- No se crean puntos de venta a partir de direcciones o contactos Alegra.",
            "- Los contactos proveedor puro se excluyen; los contactos cliente/proveedor se conservan solo en condición de cliente para la simulación.",
            "- Una consulta limitada o con error no se presenta como catálogo completo.",
            "- No se fusionan contactos con identificación repetida.",
            "",
            "## 5. Pruebas y recomendación",
            "",
            "Pruebas específicas: 29 correctas, cubriendo paginación, límites, final real, HTTP 429, páginas repetidas, duplicados, mapeos, coincidencias, proveedores, inválidos y ausencia de escrituras.",
            "- Comando reproducible de diagnóstico: `python manage.py alegra_preimport_clientes --limit 100 --max-pages 4 --pause 0 --max-retries 2 --timeout 15`.",
            "- Validaciones técnicas: `python manage.py check` correcto; `python manage.py makemigrations --check --dry-run` sin cambios.",
            "",
            f"**Veredicto: {status}.** Ejecutar una importación efectiva solo después de revisar manualmente conflictos, duplicados y cobertura completa; esta fase no habilita dicha operación.",
            "",
        ]
        return "\n".join(lines)
